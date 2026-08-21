import pytest

from adpo.ingest import parse_step, parse_job, parse_workflow_run, parse_workflow_runs


RAW_RUN = {
    "run_id": "1001",
    "workflow_name": "CI",
    "run_number": 42,
    "run_attempt": 1,
    "branch": "main",
    "event": "push",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-01T00:12:00Z",
    "conclusion": "success",
    "jobs": [
        {
            "job_id": "j1",
            "name": "build",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2024-01-01T00:00:05Z",
            "completed_at": "2024-01-01T00:04:00Z",
            "run_attempt": 1,
            "runs_on": "ubuntu-latest",
            "needs": [],
            "steps": [
                {
                    "number": 1, "name": "Checkout", "status": "completed",
                    "conclusion": "success",
                    "started_at": "2024-01-01T00:00:05Z",
                    "completed_at": "2024-01-01T00:00:09Z",
                    "uses": "actions/checkout@v4",
                },
                {
                    "number": 2, "name": "Install", "status": "completed",
                    "conclusion": "success",
                    "started_at": "2024-01-01T00:00:09Z",
                    "completed_at": "2024-01-01T00:01:39Z",
                    "run": "npm ci",
                },
            ],
        }
    ],
}


def test_parse_step_basic():
    raw = RAW_RUN["jobs"][0]["steps"][0]
    step = parse_step(raw)
    assert step.name == "Checkout"
    assert step.uses == "actions/checkout@v4"
    assert step.duration_seconds == 4


def test_parse_step_defaults_when_fields_missing():
    step = parse_step({"number": 3})
    assert step.name == "step-3"
    assert step.status == "completed"
    assert step.conclusion is None
    assert step.with_params == {}


def test_parse_job_basic():
    job = parse_job(RAW_RUN["jobs"][0])
    assert job.name == "build"
    assert len(job.steps) == 2
    assert job.duration_seconds == 235  # 00:00:05 -> 00:04:00


def test_parse_job_missing_steps_and_needs():
    job = parse_job({"name": "solo", "job_id": "j9"})
    assert job.steps == []
    assert job.needs == []
    assert job.run_attempt == 1


def test_parse_workflow_run_full_roundtrip():
    run = parse_workflow_run(RAW_RUN)
    assert run.run_id == "1001"
    assert run.workflow_name == "CI"
    assert run.run_number == 42
    assert len(run.jobs) == 1
    assert run.jobs[0].name == "build"
    assert run.updated_at is not None


def test_parse_workflow_run_requires_created_at():
    bad = {k: v for k, v in RAW_RUN.items() if k != "created_at"}
    with pytest.raises(ValueError):
        parse_workflow_run(bad)


def test_parse_workflow_run_z_suffix_handled():
    run = parse_workflow_run(RAW_RUN)
    assert run.created_at.tzinfo is not None


def test_parse_workflow_runs_list():
    runs = parse_workflow_runs([RAW_RUN, RAW_RUN])
    assert len(runs) == 2


def test_parse_job_with_needs_enrichment():
    raw_job = dict(RAW_RUN["jobs"][0])
    raw_job["needs"] = ["setup"]
    job = parse_job(raw_job)
    assert job.needs == ["setup"]
