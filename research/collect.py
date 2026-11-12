"""Download GitHub Actions history for the repos in repos.yaml.

    GITHUB_TOKEN=... python research/collect.py [--runs 150]

Uses the same read-only fetch layer as `adpo analyze` (adpo.github_fetch),
including its on-disk cache, so an interrupted collection resumes for free.
Writes research/data/raw/{runs,jobs,steps}.parquet (gitignored),
research/data/collection_meta.json (committed) and a small committed sample
in research/data/sample/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from adpo.github_fetch import GitHubClient, fetch_history

sys.path.insert(0, str(Path(__file__).parent))
from adpo_data import HERE, RAW_DIR, SAMPLE_DIR, TABLES, flatten  # noqa: E402

SAMPLE_REPOS = ("pallets/flask", "encode/httpx", "spf13/cobra")
SAMPLE_RUNS = 30


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=150, help="most recent completed runs per repo")
    args = ap.parse_args()

    repos = yaml.safe_load((HERE / "repos.yaml").read_text())["repos"]
    client = GitHubClient.from_env(list_ttl_seconds=float("inf"))  # listings pinned once fetched
    if not client.token:
        print("GITHUB_TOKEN is not set: 60 requests/hour will not cover this collection.", file=sys.stderr)

    frames = {t: [] for t in TABLES}
    meta = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs_requested_per_repo": args.runs,
        "repos": {},
    }
    for entry in repos:
        repo, workflow = entry["repo"], entry["workflow"]
        owner, name = repo.split("/")
        print(f"[{repo}] {workflow}", file=sys.stderr, flush=True)
        before = client.requests_made
        skipped_before = len(client.skipped_runs)
        raw = fetch_history(client, owner, name, workflow=workflow, max_runs=args.runs)
        runs, jobs, steps = flatten(repo, raw)
        for t, df in zip(TABLES, (runs, jobs, steps)):
            frames[t].append(df)
        meta["repos"][repo] = {
            "workflow": workflow,
            "runs": int(runs.run_id.str.split("/").str[0].nunique()) if len(runs) else 0,
            "attempts": len(runs),
            "jobs": len(jobs),
            "steps": len(steps),
            "first_run_created_at": str(runs.created_at.min()) if len(runs) else None,
            "last_run_created_at": str(runs.created_at.max()) if len(runs) else None,
            "jobs_matched_to_yaml": int(jobs.yaml_key.notna().sum()) if len(jobs) else 0,
            "api_requests_uncached": client.requests_made - before,
            "runs_skipped_fetch_error": client.skipped_runs[skipped_before:],
            # Needed to separate push-to-default-branch runs from PR/other runs.
            "default_branch": client.get_json(f"/repos/{owner}/{name}", ttl=float("inf"))[0]["default_branch"],
        }
        print(f"  {meta['repos'][repo]}", file=sys.stderr, flush=True)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    tables = {t: pd.concat(frames[t], ignore_index=True) for t in TABLES}
    for t, df in tables.items():
        df.to_parquet(RAW_DIR / f"{t}.parquet", index=False)

    # Small committed sample: newest SAMPLE_RUNS runs (all attempts) of a few small repos.
    runs = tables["runs"]
    runs = runs.assign(base_id=runs.run_id.str.split("/").str[0])
    keep = set()
    for repo in SAMPLE_REPOS:
        r = runs[runs.repo == repo].sort_values("created_at", ascending=False)
        keep |= set(r[r.base_id.isin(r.base_id.unique()[:SAMPLE_RUNS])].run_id)
    for t, df in tables.items():
        df[df.run_id.isin(keep)].to_parquet(SAMPLE_DIR / f"{t}.parquet", index=False)

    (HERE / "data" / "collection_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"done: {client.requests_made} uncached API requests", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
