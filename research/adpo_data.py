"""Flatten fetched runs into tables and rebuild them into `adpo.ingest` input.

Shared by collect.py (write) and evaluation.ipynb (read), so the notebook
analyses exactly what was collected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

HERE = Path(__file__).parent
RAW_DIR = HERE / "data" / "raw"
SAMPLE_DIR = HERE / "data" / "sample"
TABLES = ("runs", "jobs", "steps")


def flatten(repo: str, raw_runs: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    runs, jobs, steps = [], [], []
    for r in raw_runs:
        runs.append({"repo": repo, **{k: r.get(k) for k in _RUN_COLS}})
        for j in r["jobs"]:
            jobs.append(
                {
                    "repo": repo,
                    "run_id": r["run_id"],
                    "needs": json.dumps(j.get("needs")) if "needs" in j else None,
                    **{k: j.get(k) for k in _JOB_COLS},
                }
            )
            for s in j.get("steps") or []:
                steps.append(
                    {
                        "repo": repo,
                        "run_id": r["run_id"],
                        "job_id": j["job_id"],
                        "with": json.dumps(s["with"], default=str) if "with" in s else None,
                        **{k: s.get(k) for k in _STEP_COLS},
                    }
                )
    return pd.DataFrame(runs), pd.DataFrame(jobs), pd.DataFrame(steps)


_RUN_COLS = [
    "run_id", "workflow_name", "workflow_path", "head_sha", "run_number", "run_attempt",
    "branch", "event", "created_at", "updated_at", "conclusion",
]  # fmt: skip
_JOB_COLS = ["job_id", "name", "status", "conclusion", "started_at", "completed_at", "run_attempt", "runs_on", "yaml_key"]
_STEP_COLS = ["number", "name", "status", "conclusion", "started_at", "completed_at", "uses", "run"]


def load_tables(source: str = "auto") -> Tuple[Dict[str, pd.DataFrame], str]:
    """Load runs/jobs/steps. `auto` prefers the full raw collection, then the committed sample."""
    d = RAW_DIR if source == "raw" or (source == "auto" and (RAW_DIR / "runs.parquet").exists()) else SAMPLE_DIR
    return {t: pd.read_parquet(d / f"{t}.parquet") for t in TABLES}, d.name


def _clean(v: Any) -> Any:
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def to_raw_runs(tables: Dict[str, pd.DataFrame], repo: str) -> List[Dict[str, Any]]:
    """Rebuild the raw run dicts for one repo (inverse of `flatten`)."""
    runs = tables["runs"][tables["runs"].repo == repo]
    jobs = tables["jobs"][tables["jobs"].repo == repo]
    steps = tables["steps"][tables["steps"].repo == repo]
    steps_by_job: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for s in steps.to_dict("records"):
        d = {k: _clean(s[k]) for k in _STEP_COLS}
        if _clean(s["with"]) is not None:
            d["with"] = json.loads(s["with"])
        steps_by_job.setdefault((s["run_id"], s["job_id"]), []).append(d)
    jobs_by_run: Dict[str, List[Dict[str, Any]]] = {}
    for j in jobs.to_dict("records"):
        d = {k: _clean(j[k]) for k in _JOB_COLS}
        d["needs"] = json.loads(j["needs"]) if _clean(j["needs"]) is not None else []
        d["steps"] = sorted(steps_by_job.get((j["run_id"], j["job_id"]), []), key=lambda s: s["number"] or 0)
        jobs_by_run.setdefault(j["run_id"], []).append(d)
    out = []
    for r in runs.to_dict("records"):
        d = {k: _clean(r[k]) for k in _RUN_COLS}
        d["run_number"] = int(d["run_number"])
        d["run_attempt"] = int(d["run_attempt"])
        d["jobs"] = jobs_by_run.get(r["run_id"], [])
        for j in d["jobs"]:
            j["run_attempt"] = int(j["run_attempt"])
        out.append(d)
    return out
