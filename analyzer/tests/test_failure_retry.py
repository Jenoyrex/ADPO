from adpo.analyzers.failure_retry import FailureRetryAnalyzer, FailureRetryConfig
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_has_no_failure_findings():
    runs = sd.fast_pipeline_runs()
    findings = FailureRetryAnalyzer().analyze(runs)
    assert findings == []


def test_failures_dataset_flags_flaky_and_chronic_and_retry():
    runs = sd.failures_runs(n=45)  # >= flaky_min_runs (40) so the flaky gate can pass
    findings = FailureRetryAnalyzer().analyze(runs)
    classifications = {f.metrics.get("classification") for f in findings if "classification" in f.metrics}
    assert "flaky" in classifications
    assert "chronic_failure" in classifications
    titles = [f.title for f in findings]
    assert any("retry" in t.lower() or "re-run" in t.lower() for t in titles)


def test_flaky_job_uses_measured_failure_durations_for_savings():
    runs = sd.failures_runs(n=45)
    f = next(f for f in FailureRetryAnalyzer().analyze(runs) if f.metrics.get("job_name") == "flaky_e2e")
    assert f.estimated_savings_range.unknown is False
    assert f.estimated_savings_range.low_seconds > 0


def test_chronic_failure_gets_high_severity_and_different_recommendation():
    runs = sd.failures_runs()
    f = next(f for f in FailureRetryAnalyzer().analyze(runs) if f.metrics.get("job_name") == "chronic_fail_job")
    assert f.metrics["classification"] == "chronic_failure"
    assert f.severity in (Severity.HIGH, Severity.CRITICAL)
    assert "retry" not in f.recommendation.lower()[:40]  # chronic failures aren't a retry-policy problem


def test_retry_incident_measures_wasted_time_directly():
    runs = sd.failures_runs()
    f = next(f for f in FailureRetryAnalyzer().analyze(runs) if "retry" in f.title.lower())
    assert f.metrics["retried_run_count"] >= 1
    assert f.metrics["total_wasted_seconds"] > 0
    assert f.estimated_savings_range.unknown is False


def test_always_passing_job_never_flagged():
    runs = sd.fast_pipeline_runs()
    findings = FailureRetryAnalyzer().analyze(runs)
    assert findings == []


def _job_names(findings, classification):
    return {f.metrics["job_name"] for f in findings if f.metrics.get("classification") == classification}


def test_flaky_needs_flaky_min_runs():
    # 12 runs, 4 failures (33%): the pre-evaluation rule called this flaky; with fewer than
    # flaky_min_runs conclusive runs the rate is too unreliable to rank (REPORT.md section e).
    runs = sd.failures_runs(n=12)
    assert "flaky_e2e" not in _job_names(FailureRetryAnalyzer().analyze(runs), "flaky")


def test_chronic_failure_is_not_gated_by_flaky_min_runs():
    runs = sd.failures_runs(n=12)
    assert "chronic_fail_job" in _job_names(FailureRetryAnalyzer().analyze(runs), "chronic_failure")


def _runs_with_failures(n, failing):
    from datetime import timedelta

    from adpo.models import Job, WorkflowRun

    out = []
    for i in range(n):
        t = sd.BASE_TIME + timedelta(hours=i)
        conc = "failure" if i in failing else "success"
        job = Job("j%d" % i, "e2e", "completed", conc, t, t + timedelta(seconds=60))
        out.append(WorkflowRun(str(i), "CI", i, "main", "push", t, conc, [job]))
    return out


def test_credible_lower_bound_blocks_rate_driven_by_few_failures():
    # 3 of 45 = 6.7% >= 5%, but the one-sided 95% Jeffreys bound is ~2.1% < 5%.
    runs = _runs_with_failures(45, {5, 20, 33})
    assert "e2e" not in _job_names(FailureRetryAnalyzer().analyze(runs), "flaky")
    legacy = FailureRetryConfig(flaky_min_runs=3, flaky_credible_lower_quantile=None)
    assert "e2e" in _job_names(FailureRetryAnalyzer(legacy).analyze(runs), "flaky")


def test_flaky_finding_reports_its_credible_bound():
    runs = _runs_with_failures(60, set(range(0, 60, 4)))  # 15/60 = 25%
    f = next(f for f in FailureRetryAnalyzer().analyze(runs) if f.metrics.get("classification") == "flaky")
    lb = f.metrics["failure_rate_credible_lower_bound"]
    assert 0.05 <= lb < 0.25
    assert any("credible bound" in e for e in f.evidence)


def test_empty_input_returns_no_findings():
    assert FailureRetryAnalyzer().analyze([]) == []


def test_finding_shape_is_complete():
    runs = sd.failures_runs()
    findings = FailureRetryAnalyzer().analyze(runs)
    assert findings
    for f in findings:
        assert f.analyzer_type == "failure_retry"
        assert isinstance(f.severity, Severity)
        assert isinstance(f.confidence, Confidence)
        assert f.estimated_savings_range is not None
