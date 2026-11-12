"""
Attach workflow-YAML facts the Jobs API does not return.

The REST "list jobs" response has display names, timings and conclusions,
but not `needs` (job dependency edges) or each step's `uses` / `run` /
`with`. Two analyzers depend on those: Potential Parallelization (`needs`)
and Dependency Installation (`run` to spot installs, `uses`/`with` to spot
caching). This module matches API display names back to the workflow file
and copies those fields onto the raw run dicts before `adpo.ingest` runs.

Matching is by name only, deterministic, and deliberately conservative:
  * a job's display name is its YAML `name:` (with `${{ }}` expressions
    expanded), or its key; matrix jobs append " (a, b)"; jobs calling a
    reusable workflow appear as "<caller> / <callee>";
  * a step's display name is its `name:`, else "Run <uses>", else
    "Run <first line of run>".
Anything that cannot be matched is left untouched (and counted), never
guessed. Requires PyYAML (`pip install "adpo-analysis-engine[cli]"`).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_EXPR = re.compile(r"\$\{\{.*?\}\}")
_SKIP_STEPS = ("Set up job", "Complete job")


def _template_regex(template: str) -> "re.Pattern[str]":
    parts = _EXPR.split(template)
    return re.compile(".+?".join(re.escape(p) for p in parts), re.DOTALL)


def _name_matches(display: str, template: str, allow_suffix: bool) -> int:
    """0 = no match, 3 = exact, 2 = matrix/reusable suffix, 1 = expression match."""
    if display == template:
        return 3
    if allow_suffix and (display.startswith(template + " (") or display.startswith(template + " / ")):
        return 2
    if "${{" in template and _template_regex(template).fullmatch(display):
        return 1
    return 0


def resolve_job_key(display_name: str, jobs_spec: Dict[str, Any]) -> Optional[str]:
    best, best_score = None, 0
    for key, spec in jobs_spec.items():
        spec = spec if isinstance(spec, dict) else {}
        template = str(spec.get("name") or key)
        score = _name_matches(display_name, template, allow_suffix=True)
        if template != key:
            score = max(score, _name_matches(display_name, key, allow_suffix=True) and 1)
        if score > best_score:
            best, best_score = key, score
    return best


def _step_display_candidates(step: Dict[str, Any]) -> List[str]:
    if step.get("name"):
        return [str(step["name"])]
    if step.get("uses"):
        return ["Run " + str(step["uses"])]
    if step.get("run"):
        first = next((ln.strip() for ln in str(step["run"]).splitlines() if ln.strip()), "")
        return ["Run " + first]
    return []


def _step_matches(display: str, yaml_step: Dict[str, Any]) -> bool:
    for cand in _step_display_candidates(yaml_step):
        if _name_matches(display, cand, allow_suffix=False):
            return True
        # GitHub truncates long "Run <command>" display names.
        if not yaml_step.get("name") and display.startswith("Run ") and len(display) > 10 and cand.startswith(display):
            return True
    return False


def _enrich_steps(api_steps: List[Dict[str, Any]], yaml_steps: List[Dict[str, Any]]) -> int:
    """Copy uses/run/with onto API steps, matching in order. Returns matched count."""
    matched, cursor = 0, 0
    for api_step in api_steps:
        name = str(api_step.get("name") or "")
        if name in _SKIP_STEPS or name.startswith(("Post ", "Pre ")):
            continue
        for idx in range(cursor, len(yaml_steps)):
            ys = yaml_steps[idx] if isinstance(yaml_steps[idx], dict) else {}
            if _step_matches(name, ys):
                if ys.get("uses"):
                    api_step["uses"] = str(ys["uses"])
                if ys.get("run"):
                    api_step["run"] = str(ys["run"])
                if isinstance(ys.get("with"), dict):
                    api_step["with"] = {str(k): v for k, v in ys["with"].items()}
                cursor = idx + 1
                matched += 1
                break
    return matched


def parse_workflow(text: str) -> Dict[str, Any]:
    import yaml  # optional dependency, only needed for enrichment

    doc = yaml.safe_load(text) or {}
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def enrich_runs(raw_runs: List[Dict[str, Any]], yaml_by_path: Dict[str, str]) -> Dict[str, int]:
    """Mutates `raw_runs` in place. Returns match counts for transparency."""
    specs = {path: parse_workflow(text) for path, text in yaml_by_path.items()}
    stats = {"jobs_total": 0, "jobs_matched": 0, "steps_matched": 0}
    for run in raw_runs:
        jobs_spec = specs.get(run.get("workflow_path") or "")
        for job in run.get("jobs", []):
            stats["jobs_total"] += 1
            if not jobs_spec:
                continue
            key = resolve_job_key(str(job.get("name") or ""), jobs_spec)
            if key is None:
                continue
            stats["jobs_matched"] += 1
            job["yaml_key"] = key
            spec = jobs_spec[key] if isinstance(jobs_spec[key], dict) else {}
            steps = spec.get("steps")
            if isinstance(steps, list):
                stats["steps_matched"] += _enrich_steps(job.get("steps") or [], steps)
        # `needs` refers to YAML keys; translate to this run's display names.
        keyed = [j for j in run.get("jobs", []) if j.get("yaml_key")]
        for job in keyed:
            spec = jobs_spec.get(job["yaml_key"]) if jobs_spec else None
            needs = spec.get("needs", []) if isinstance(spec, dict) else []
            needs_keys = {needs} if isinstance(needs, str) else set(needs or [])
            job["needs"] = sorted({o["name"] for o in keyed if o["yaml_key"] in needs_keys})
    return stats
