"""
`adpo analyze OWNER/REPO` - run the deterministic analyzers on a public repo.

No GitHub App, database or backend needed: runs are fetched read-only from
the public REST API (see `adpo.github_fetch`), parsed by `adpo.ingest`, and
analysed by the same `adpo.engine.AnalysisEngine` the backend uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .engine import AnalysisEngine
from .github_fetch import GitHubClient, GitHubFetchError, fetch_history
from .ingest import parse_workflow_runs
from .models import Finding

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def rank_findings(findings: List[Finding]) -> List[Finding]:
    """Severity first, then confidence, then the upper savings bound."""

    def key(f: Finding) -> tuple:
        s = f.estimated_savings_range
        high = s.high_seconds if s and not s.unknown and s.high_seconds is not None else 0.0
        return (_SEVERITY_RANK[f.severity.value], _CONFIDENCE_RANK[f.confidence.value], -high, f.title)

    return sorted(findings, key=key)


def _savings_text(f: Finding) -> str:
    s = f.estimated_savings_range
    if s is None or s.unknown:
        return "unknown"
    return f"{s.low_seconds:.0f}-{s.high_seconds:.0f}s"


def _summary(raw_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    run_ids = {r["run_id"].split("/")[0] for r in raw_runs}
    jobs = [j for r in raw_runs for j in r["jobs"]]
    return {
        "runs": len(run_ids),
        "attempts": len(raw_runs),
        "jobs": len(jobs),
        "steps": sum(len(j.get("steps") or []) for j in jobs),
        "workflows": sorted({str(r.get("workflow_name")) for r in raw_runs}),
        "jobs_matched_to_yaml": sum(1 for j in jobs if j.get("yaml_key")),
    }


def render_text(repo: str, summary: Dict[str, Any], findings: List[Finding]) -> str:
    lines = [
        f"ADPO analysis of {repo}",
        f"{summary['runs']} runs ({summary['attempts']} attempts), {summary['jobs']} jobs, "
        f"{summary['steps']} steps; {summary['jobs_matched_to_yaml']}/{summary['jobs']} jobs matched to workflow YAML",
        f"{len(findings)} finding(s)",
        "",
    ]
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. [{f.severity.value.upper()} | {f.confidence.value} confidence] {f.title}")
        lines.append(f"   analyzer: {f.analyzer_type}   est. savings: {_savings_text(f)}")
        lines.extend(f"   - {e}" for e in f.evidence)
        lines.append(f"   -> {f.recommendation}")
        lines.append("")
    return "\n".join(lines)


def render_markdown(repo: str, summary: Dict[str, Any], findings: List[Finding]) -> str:
    lines = [
        f"# ADPO analysis of `{repo}`",
        "",
        f"{summary['runs']} runs ({summary['attempts']} attempts), {summary['jobs']} jobs, {summary['steps']} steps. "
        f"{summary['jobs_matched_to_yaml']}/{summary['jobs']} jobs matched to workflow YAML.",
        "",
        "| # | Severity | Confidence | Analyzer | Finding | Est. savings |",
        "|---|---|---|---|---|---|",
    ]
    for i, f in enumerate(findings, 1):
        title = f.title.replace("|", "\\|")
        lines.append(
            f"| {i} | {f.severity.value} | {f.confidence.value} | {f.analyzer_type} | {title} | {_savings_text(f)} |"
        )
    for i, f in enumerate(findings, 1):
        lines += [
            "",
            f"## {i}. {f.title}",
            "",
            *[f"- {e}" for e in f.evidence],
            "",
            f"**Recommendation:** {f.recommendation}",
        ]
    return "\n".join(lines) + "\n"


def render_json(repo: str, summary: Dict[str, Any], findings: List[Finding]) -> str:
    return json.dumps({"repo": repo, "summary": summary, "findings": [f.to_dict() for f in findings]}, indent=2)


RENDERERS = {"text": render_text, "md": render_markdown, "json": render_json}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adpo", description="Deterministic CI analysis for GitHub Actions.")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze", help="Analyze recent workflow runs of a public repository.")
    a.add_argument("repo", help="OWNER/REPO")
    a.add_argument("--workflow", help="Workflow file name (ci.yml) or display name. Default: all workflows.")
    a.add_argument("--runs", type=int, default=100, help="Most recent completed runs to fetch (default 100).")
    a.add_argument("--since", type=date.fromisoformat, help="Only runs created on/after this date (YYYY-MM-DD).")
    a.add_argument("--format", choices=sorted(RENDERERS), default="text")
    a.add_argument("--cache-dir", type=Path, help="Response cache (default ~/.cache/adpo or $ADPO_CACHE_DIR).")
    a.add_argument("--no-yaml", action="store_true", help="Skip workflow-YAML enrichment (needs/uses/run).")
    return p


def main(argv: Optional[Sequence[str]] = None, client: Optional[GitHubClient] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repo.count("/") != 1 or not all(args.repo.split("/")):
        print("adpo: repository must look like OWNER/REPO", file=sys.stderr)
        return 2
    if args.runs < 1:
        print("adpo: --runs must be at least 1", file=sys.stderr)
        return 2
    owner, repo = args.repo.split("/")
    client = client or GitHubClient.from_env(cache_dir=args.cache_dir)
    if not client.token:
        print("adpo: GITHUB_TOKEN not set; using unauthenticated API (60 requests/hour).", file=sys.stderr)

    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"adpo: fetched jobs for {done}/{total} runs", file=sys.stderr)

    try:
        raw = fetch_history(
            client,
            owner,
            repo,
            workflow=args.workflow,
            max_runs=args.runs,
            since=args.since.isoformat() if args.since else None,
            enrich=not args.no_yaml,
            progress=progress,
        )
    except GitHubFetchError as exc:
        print(f"adpo: {exc}", file=sys.stderr)
        return 1
    if not raw:
        print("adpo: no completed workflow runs found for that selection.", file=sys.stderr)
        return 1

    engine = AnalysisEngine()
    findings = rank_findings(engine.run(parse_workflow_runs(raw)))
    for err in engine.errors:
        print(f"adpo: analyzer error: {err}", file=sys.stderr)
    print(RENDERERS[args.format](args.repo, _summary(raw), findings))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
