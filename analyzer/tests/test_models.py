from datetime import datetime, timedelta, timezone

from adpo.models import (
    Confidence,
    EstimatedSavings,
    Finding,
    Job,
    Severity,
    Step,
    WorkflowRun,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_step_duration_seconds():
    step = Step(
        name="Build", number=1, status="completed", conclusion="success",
        started_at=T0, completed_at=T0 + timedelta(seconds=30),
    )
    assert step.duration_seconds == 30


def test_step_duration_none_when_missing_timestamps():
    step = Step(name="Build", number=1, status="in_progress", conclusion=None)
    assert step.duration_seconds is None


def test_step_duration_none_when_negative():
    # completed_at before started_at is nonsensical data - treat as unusable
    step = Step(
        name="Weird", number=1, status="completed", conclusion="success",
        started_at=T0, completed_at=T0 - timedelta(seconds=5),
    )
    assert step.duration_seconds is None


def test_job_duration_seconds():
    job = Job(
        job_id="j1", name="build", status="completed", conclusion="success",
        started_at=T0, completed_at=T0 + timedelta(seconds=120), steps=[],
    )
    assert job.duration_seconds == 120


def test_workflow_run_duration_seconds_spans_all_jobs():
    job1 = Job(job_id="j1", name="a", status="completed", conclusion="success",
               started_at=T0, completed_at=T0 + timedelta(seconds=60), steps=[])
    job2 = Job(job_id="j2", name="b", status="completed", conclusion="success",
               started_at=T0 + timedelta(seconds=30), completed_at=T0 + timedelta(seconds=100), steps=[])
    run = WorkflowRun(
        run_id="r1", workflow_name="CI", run_number=1, branch="main", event="push",
        created_at=T0, conclusion="success", jobs=[job1, job2],
    )
    assert run.duration_seconds == 100


def test_workflow_run_duration_none_with_no_jobs():
    run = WorkflowRun(
        run_id="r1", workflow_name="CI", run_number=1, branch="main", event="push",
        created_at=T0, conclusion="success", jobs=[],
    )
    assert run.duration_seconds is None


def test_job_by_name():
    job1 = Job(job_id="j1", name="a", status="completed", conclusion="success", steps=[])
    run = WorkflowRun(
        run_id="r1", workflow_name="CI", run_number=1, branch="main", event="push",
        created_at=T0, conclusion="success", jobs=[job1],
    )
    assert run.job_by_name("a") is job1
    assert run.job_by_name("missing") is None


def test_finding_to_dict_serializes_enums_and_savings():
    savings = EstimatedSavings(low_seconds=1.0, high_seconds=2.0, basis="test", unknown=False)
    finding = Finding(
        analyzer_type="slow_job",
        title="t", description="d", evidence=["e1"], metrics={"m": 1},
        severity=Severity.HIGH, confidence=Confidence.MEDIUM,
        recommendation="r", estimated_savings_range=savings,
    )
    d = finding.to_dict()
    assert d["severity"] == "high"
    assert d["confidence"] == "medium"
    assert d["estimated_savings_range"]["low_seconds"] == 1.0
    assert d["evidence"] == ["e1"]


def test_finding_to_dict_handles_unknown_savings():
    savings = EstimatedSavings(low_seconds=None, high_seconds=None, basis="no data", unknown=True)
    finding = Finding(
        analyzer_type="slow_job", title="t", description="d", evidence=[], metrics={},
        severity=Severity.LOW, confidence=Confidence.LOW, recommendation="r",
        estimated_savings_range=savings,
    )
    d = finding.to_dict()
    assert d["estimated_savings_range"]["unknown"] is True
    assert d["estimated_savings_range"]["low_seconds"] is None


def test_finding_to_dict_handles_no_savings():
    finding = Finding(
        analyzer_type="slow_job", title="t", description="d", evidence=[], metrics={},
        severity=Severity.LOW, confidence=Confidence.LOW, recommendation="r",
        estimated_savings_range=None,
    )
    d = finding.to_dict()
    assert d["estimated_savings_range"] is None
