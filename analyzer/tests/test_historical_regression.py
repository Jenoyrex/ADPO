from adpo.analyzers.historical_regression import (
    HistoricalRegressionAnalyzer,
    RegressionConfig,
    WORKFLOW_TOTAL_KEY,
)
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_has_no_regression_findings():
    runs = sd.fast_pipeline_runs()
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    assert findings == []


def test_regression_dataset_flags_build_job_and_workflow_total():
    runs = sd.regression_runs()
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    keys = {f.metrics["key"] for f in findings}
    assert "build" in keys
    assert WORKFLOW_TOTAL_KEY in keys


def test_regression_finding_has_defensible_savings():
    runs = sd.regression_runs()
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    build_finding = next(f for f in findings if f.metrics["key"] == "build")
    savings = build_finding.estimated_savings_range
    assert savings.unknown is False
    assert savings.low_seconds > 0
    assert savings.high_seconds >= savings.low_seconds
    # Should roughly equal the injected jump (~140s), not an invented number
    assert 100 < savings.low_seconds < 200


def test_slow_tests_dataset_does_not_falsely_flag_regression():
    # Consistently slow but stable across the whole history - not a regression.
    runs = sd.slow_tests_runs()
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    assert findings == []


def test_insufficient_history_produces_no_finding():
    runs = sd.regression_runs(n=4)  # below min_total_samples default of 6
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    assert findings == []


def test_single_run_does_not_crash():
    runs = sd.single_run_only()
    findings = HistoricalRegressionAnalyzer().analyze(runs)
    assert findings == []


def test_empty_input_returns_no_findings():
    assert HistoricalRegressionAnalyzer().analyze([]) == []


def test_a_single_outlier_run_is_not_flagged_as_regression():
    # One anomalously slow run amid an otherwise stable history should NOT
    # trigger a regression finding, since the "recent window" majority
    # will still look like baseline.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    rng = random.Random(123)
    runs = []
    for i in range(12):
        day = BASE_TIME + timedelta(days=i)
        duration = 500 if i == 10 else 60 + rng.uniform(-3, 3)  # one outlier near the end
        job, _ = make_job("build", day, [{"name": "Build", "duration": duration}], needs=[])
        runs.append(make_run("CI", [job], run_number=i + 6000, created_offset_hours=i * 24))

    findings = HistoricalRegressionAnalyzer().analyze(runs)
    keys = {f.metrics["key"] for f in findings}
    assert "build" not in keys


def test_confidence_requires_consistency_not_just_sample_size():
    runs = sd.regression_runs()
    build_finding = next(
        f for f in HistoricalRegressionAnalyzer().analyze(runs) if f.metrics["key"] == "build"
    )
    assert build_finding.confidence in (Confidence.MEDIUM, Confidence.HIGH)


def test_finding_shape_is_complete():
    runs = sd.regression_runs()
    f = HistoricalRegressionAnalyzer().analyze(runs)[0]
    assert f.analyzer_type == "historical_regression"
    assert isinstance(f.severity, Severity)
    assert isinstance(f.confidence, Confidence)
    assert len(f.evidence) >= 2
