from adpo.analyzers.slow_step import SlowStepAnalyzer
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_has_no_slow_step_findings():
    runs = sd.fast_pipeline_runs()
    findings = SlowStepAnalyzer().analyze(runs)
    assert findings == []


def test_slow_tests_dataset_localizes_run_tests_step():
    runs = sd.slow_tests_runs()
    findings = SlowStepAnalyzer().analyze(runs)
    assert len(findings) == 1
    f = findings[0]
    assert f.metrics["job_name"] == "unit_tests"
    assert f.metrics["step_name"] == "Run tests"
    assert f.metrics["share_of_job_median"] > 0.8  # dominates the job


def test_savings_unknown_when_step_is_stable():
    runs = sd.slow_tests_runs()
    f = SlowStepAnalyzer().analyze(runs)[0]
    assert f.estimated_savings_range.unknown is True


def test_regression_dataset_also_flags_slow_step_after_regression():
    # After the regression, the Build step's absolute duration should
    # itself become significant relative to its job.
    runs = sd.regression_runs()
    findings = SlowStepAnalyzer().analyze(runs)
    names = [(f.metrics["job_name"], f.metrics["step_name"]) for f in findings]
    assert ("build", "Build") in names


def test_empty_input_returns_no_findings():
    assert SlowStepAnalyzer().analyze([]) == []


def test_edge_case_zero_steps_job_does_not_crash():
    cases = sd.edge_case_runs()
    findings = SlowStepAnalyzer().analyze([cases["zero_steps"]])
    assert findings == []


def test_edge_case_in_progress_job_does_not_crash():
    cases = sd.edge_case_runs()
    findings = SlowStepAnalyzer().analyze([cases["job_in_progress"]])
    assert findings == []


def test_finding_shape_is_complete():
    runs = sd.slow_tests_runs()
    f = SlowStepAnalyzer().analyze(runs)[0]
    assert isinstance(f.severity, Severity)
    assert isinstance(f.confidence, Confidence)
    assert f.analyzer_type == "slow_step"
    assert f.estimated_savings_range is not None
