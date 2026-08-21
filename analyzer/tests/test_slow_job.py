from adpo.analyzers.slow_job import SlowJobAnalyzer, SlowJobConfig
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_has_no_slow_job_findings():
    runs = sd.fast_pipeline_runs()
    findings = SlowJobAnalyzer().analyze(runs)
    assert findings == []


def test_slow_tests_dataset_flags_unit_tests_job():
    runs = sd.slow_tests_runs()
    findings = SlowJobAnalyzer().analyze(runs)
    assert len(findings) == 1
    f = findings[0]
    assert f.analyzer_type == "slow_job"
    assert f.metrics["job_name"] == "unit_tests"
    assert f.metrics["median_duration_seconds"] > 800
    assert f.severity in (Severity.HIGH, Severity.CRITICAL)


def test_slow_tests_dataset_savings_marked_unknown_for_stable_slow_job():
    # Low run-to-run variance -> no empirical evidence of a faster time ->
    # savings must be marked unknown, never invented.
    runs = sd.slow_tests_runs()
    findings = SlowJobAnalyzer().analyze(runs)
    f = findings[0]
    assert f.estimated_savings_range.unknown is True
    assert f.estimated_savings_range.low_seconds is None
    assert f.estimated_savings_range.high_seconds is None


def test_confidence_scales_with_sample_size():
    small = sd.slow_tests_runs(n=2)
    large = sd.slow_tests_runs(n=12)
    f_small = SlowJobAnalyzer().analyze(small)[0]
    f_large = SlowJobAnalyzer().analyze(large)[0]
    order = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}
    assert order[f_large.confidence] >= order[f_small.confidence]


def test_empty_input_returns_no_findings():
    assert SlowJobAnalyzer().analyze([]) == []


def test_single_run_below_min_sample_size_still_evaluated_but_low_confidence():
    runs = sd.single_run_only()
    findings = SlowJobAnalyzer(SlowJobConfig(min_duration_seconds=1, min_share_of_workflow=0.0)).analyze(runs)
    # with a permissive config even 1 sample can be flagged, but confidence must be LOW
    if findings:
        assert findings[0].confidence == Confidence.LOW


def test_finding_has_all_required_fields():
    runs = sd.slow_tests_runs()
    f = SlowJobAnalyzer().analyze(runs)[0]
    assert f.analyzer_type
    assert f.title
    assert f.description
    assert isinstance(f.evidence, list) and len(f.evidence) > 0
    assert isinstance(f.metrics, dict) and len(f.metrics) > 0
    assert isinstance(f.severity, Severity)
    assert isinstance(f.confidence, Confidence)
    assert f.recommendation
    assert f.estimated_savings_range is not None


def test_high_variance_job_gets_non_unknown_savings():
    # Build a job whose duration varies a lot across runs (cv >= 0.10) so the
    # analyzer should be able to anchor a savings estimate to real data.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    rng = random.Random(99)
    runs = []
    for i in range(10):
        day = BASE_TIME + timedelta(days=i)
        duration = 200 if i % 2 == 0 else 600  # highly variable
        job, _ = make_job("variable_job", day, [{"name": "Work", "duration": duration}], needs=[])
        runs.append(make_run("CI", [job], run_number=i + 5000, created_offset_hours=i * 24))

    findings = SlowJobAnalyzer().analyze(runs)
    assert len(findings) == 1
    savings = findings[0].estimated_savings_range
    assert savings.unknown is False
    assert savings.low_seconds is not None
    assert savings.high_seconds is not None
    assert savings.low_seconds <= savings.high_seconds
