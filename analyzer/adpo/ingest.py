"""
Ingestion layer: converts GitHub-Actions-shaped structured JSON (e.g. the
combined output of the "List jobs for a workflow run" REST API plus run
metadata, enriched with `needs` extracted from the workflow YAML) into the
internal ADPO data model.

This module performs NO analysis. It only parses and defensively defaults
missing fields. Keeping ingestion separate from analysis means:
  * every analyzer can be unit tested purely against `WorkflowRun` objects,
    independent of the exact wire format GitHub happens to expose today;
  * malformed/missing input fields are handled in exactly one place.

Expected raw shape (fields not present are treated as missing, not as an
error, wherever a sensible default exists):

    {
      "run_id": "1001", "workflow_name": "CI", "run_number": 42,
      "run_attempt": 1, "branch": "main", "event": "push",
      "created_at": "2024-01-01T00:00:00Z", "updated_at": "...",
      "conclusion": "success",
      "jobs": [
        {
          "job_id": "j1", "name": "build", "status": "completed",
          "conclusion": "success",
          "started_at": "...", "completed_at": "...",
          "run_attempt": 1, "runs_on": "ubuntu-latest",
          "needs": [],
          "steps": [
            {"number": 1, "name": "Checkout", "status": "completed",
             "conclusion": "success", "started_at": "...", "completed_at": "...",
             "uses": "actions/checkout@v4"},
            {"number": 2, "name": "Install", "status": "completed",
             "conclusion": "success", "started_at": "...", "completed_at": "...",
             "run": "npm ci"}
          ]
        }
      ]
    }
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import Job, Step, WorkflowRun


def _parse_dt(value: Optional[Any]) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    v = str(value).strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def parse_step(raw: Dict[str, Any]) -> Step:
    return Step(
        name=raw.get("name") or f"step-{raw.get('number', '?')}",
        number=raw.get("number", 0),
        status=raw.get("status", "completed"),
        conclusion=raw.get("conclusion"),
        started_at=_parse_dt(raw.get("started_at")),
        completed_at=_parse_dt(raw.get("completed_at")),
        uses=raw.get("uses"),
        with_params=dict(raw.get("with") or raw.get("with_params") or {}),
        run=raw.get("run"),
    )


def parse_job(raw: Dict[str, Any]) -> Job:
    steps_raw = raw.get("steps") or []
    return Job(
        job_id=str(raw.get("job_id") or raw.get("id") or raw.get("name") or "unknown-job"),
        name=raw.get("name", "unnamed-job"),
        status=raw.get("status", "completed"),
        conclusion=raw.get("conclusion"),
        started_at=_parse_dt(raw.get("started_at")),
        completed_at=_parse_dt(raw.get("completed_at")),
        steps=[parse_step(s) for s in steps_raw],
        needs=list(raw.get("needs") or []),
        runs_on=raw.get("runs_on") or raw.get("runner_name"),
        run_attempt=raw.get("run_attempt", 1),
    )


def parse_workflow_run(raw: Dict[str, Any]) -> WorkflowRun:
    jobs_raw = raw.get("jobs") or []
    created_at = _parse_dt(raw.get("created_at") or raw.get("run_started_at"))
    if created_at is None:
        raise ValueError(
            "workflow run is missing a usable 'created_at' (or 'run_started_at') timestamp"
        )
    return WorkflowRun(
        run_id=str(raw.get("run_id") or raw.get("id") or "unknown-run"),
        workflow_name=raw.get("workflow_name") or raw.get("name") or "unknown-workflow",
        run_number=raw.get("run_number", 0),
        branch=raw.get("branch") or raw.get("head_branch") or "",
        event=raw.get("event", "unknown"),
        created_at=created_at,
        conclusion=raw.get("conclusion"),
        jobs=[parse_job(j) for j in jobs_raw],
        run_attempt=raw.get("run_attempt", 1),
        updated_at=_parse_dt(raw.get("updated_at")),
    )


def parse_workflow_runs(raw_list: List[Dict[str, Any]]) -> List[WorkflowRun]:
    return [parse_workflow_run(r) for r in raw_list]
