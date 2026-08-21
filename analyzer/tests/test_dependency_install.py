from adpo.analyzers.dependency_install import DependencyInstallAnalyzer
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_install_too_short_to_flag():
    # fast_pipeline's install step is ~8s, below the default 20s threshold
    runs = sd.fast_pipeline_runs()
    findings = DependencyInstallAnalyzer().analyze(runs)
    assert findings == []


def test_uncached_install_is_flagged_and_cached_is_not():
    runs = sd.dependency_reinstall_runs()
    findings = DependencyInstallAnalyzer().analyze(runs)
    job_names = {f.metrics["job_name"] for f in findings}
    assert "build_uncached" in job_names
    assert "build_cached" not in job_names


def test_uncached_finding_classification_and_evidence():
    runs = sd.dependency_reinstall_runs()
    f = next(f for f in DependencyInstallAnalyzer().analyze(runs) if f.metrics["job_name"] == "build_uncached")
    assert f.metrics["classification"] == "uncached"
    assert f.metrics["cache_action_detected"] is False
    assert any("no caching" in e.lower() or "no detected caching" in f.title.lower() for e in f.evidence + [f.title])


def test_ineffective_cache_dataset_flagged_with_capped_confidence():
    runs = sd.dependency_ineffective_cache_runs()
    findings = DependencyInstallAnalyzer().analyze(runs)
    assert len(findings) == 1
    f = findings[0]
    assert f.metrics["classification"] == "cache_possibly_ineffective"
    assert f.metrics["cache_action_detected"] is True
    # Confidence for "cache possibly ineffective" must never claim HIGH,
    # since a cache hit/miss can't be directly observed from this data.
    assert f.confidence != Confidence.HIGH


def test_savings_are_empirically_derived_from_variance():
    runs = sd.dependency_reinstall_runs()
    f = next(f for f in DependencyInstallAnalyzer().analyze(runs) if f.metrics["job_name"] == "build_uncached")
    savings = f.estimated_savings_range
    assert savings.unknown is False
    assert savings.low_seconds is not None and savings.high_seconds is not None
    assert savings.low_seconds <= savings.high_seconds


def test_stable_uncached_install_marks_savings_unknown():
    # If duration is uncached AND stable (no variance), we can't anchor a
    # savings number to any observed faster run.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    runs = []
    for i in range(6):
        day = BASE_TIME + timedelta(days=i)
        job, _ = make_job(
            "build",
            day,
            [{"name": "Install dependencies", "duration": 100.0, "run": "npm ci"}],
            needs=[],
        )
        runs.append(make_run("CI", [job], run_number=i + 7000, created_offset_hours=i * 24))

    f = DependencyInstallAnalyzer().analyze(runs)[0]
    assert f.estimated_savings_range.unknown is True


def test_duplicate_job_occurrence_detected():
    # Two jobs independently running the identical install step name -
    # this should be surfaced as a metric.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    rng = random.Random(11)
    runs = []
    for i in range(6):
        day = BASE_TIME + timedelta(days=i)
        job1, _ = make_job(
            "matrix_a", day,
            [{"name": "Install dependencies", "duration": 60 + rng.uniform(-10, 10), "run": "npm ci"}],
            needs=[],
        )
        job2, _ = make_job(
            "matrix_b", day,
            [{"name": "Install dependencies", "duration": 65 + rng.uniform(-10, 10), "run": "npm ci"}],
            needs=[],
        )
        runs.append(make_run("CI", [job1, job2], run_number=i + 8000, created_offset_hours=i * 24))

    findings = DependencyInstallAnalyzer().analyze(runs)
    assert len(findings) == 2
    assert all(f.metrics["duplicate_job_occurrences"] == 2 for f in findings)


def test_empty_input_returns_no_findings():
    assert DependencyInstallAnalyzer().analyze([]) == []


def test_finding_shape_is_complete():
    runs = sd.dependency_reinstall_runs()
    f = DependencyInstallAnalyzer().analyze(runs)[0]
    assert f.analyzer_type == "dependency_install"
    assert isinstance(f.severity, Severity)
    assert isinstance(f.confidence, Confidence)
    assert f.estimated_savings_range is not None
