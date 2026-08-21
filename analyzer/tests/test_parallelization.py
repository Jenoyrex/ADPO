from adpo.analyzers.parallelization import ParallelizationAnalyzer, ParallelizationConfig
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_fast_pipeline_already_parallel_jobs_not_flagged():
    # build and test in fast_pipeline already overlap -> nothing to surface
    runs = sd.fast_pipeline_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    assert findings == []


def test_potentially_parallel_dataset_flags_lint_and_unit_tests_only():
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    pairs = {frozenset([f.metrics["job_a"], f.metrics["job_b"]]) for f in findings}
    assert frozenset(["lint", "unit_tests_parallel_candidate"]) in pairs
    # deploy has declared `needs` on both -> must never appear as a candidate
    assert not any("deploy" in pair for pair in pairs)


def test_declared_dependency_pairs_are_never_candidates():
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    for f in findings:
        pair = {f.metrics["job_a"], f.metrics["job_b"]}
        assert pair != {"lint", "deploy"}
        assert pair != {"unit_tests_parallel_candidate", "deploy"}


def test_finding_never_asserts_jobs_are_safe_to_parallelize():
    # This is the core conservatism requirement: the analyzer must not
    # claim safety merely because jobs currently run sequentially.
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    assert findings
    forbidden_phrases = [
        "safe to parallelize",
        "is safe to run in parallel",
        "can safely be parallelized",
        "guaranteed",
        "definitely safe",
    ]
    for f in findings:
        text = (f.title + " " + f.description + " " + f.recommendation).lower()
        for phrase in forbidden_phrases:
            assert phrase not in text, f"finding used disallowed confident-safety language: {phrase!r}"


def test_finding_explicitly_hedges_and_asks_for_verification():
    runs = sd.potentially_parallel_runs()
    f = ParallelizationAnalyzer().analyze(runs)[0]
    text = (f.description + " " + f.recommendation).lower()
    assert "does not establish" in text or "not a determination" in text
    assert "verify" in text or "confirm" in text


def test_confidence_is_capped_below_high():
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    for f in findings:
        assert f.confidence != Confidence.HIGH


def test_severity_is_capped_at_medium():
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    for f in findings:
        assert f.severity in (Severity.LOW, Severity.MEDIUM)


def test_savings_low_bound_is_always_zero():
    # Because the analyzer cannot confirm safety, the conservative floor
    # on potential savings must always be zero.
    runs = sd.potentially_parallel_runs()
    findings = ParallelizationAnalyzer().analyze(runs)
    for f in findings:
        assert f.estimated_savings_range.low_seconds == 0.0
        assert f.estimated_savings_range.high_seconds > 0


def test_inconsistent_ordering_does_not_get_flagged():
    # If two independent jobs sometimes overlap and sometimes don't
    # (inconsistent pattern), that is weaker evidence and should not
    # clear the default consistency threshold.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    rng = random.Random(21)
    runs = []
    for i in range(8):
        day = BASE_TIME + timedelta(days=i)
        a_job, a_end = make_job("a", day, [{"name": "Work", "duration": 60}], needs=[])
        # half the time b overlaps with a, half the time it's sequential
        b_start = day if i % 2 == 0 else a_end
        b_job, _ = make_job("b", b_start, [{"name": "Work", "duration": 60}], needs=[])
        runs.append(make_run("CI", [a_job, b_job], run_number=i + 9000, created_offset_hours=i * 24))

    findings = ParallelizationAnalyzer().analyze(runs)
    pairs = {frozenset([f.metrics["job_a"], f.metrics["job_b"]]) for f in findings}
    assert frozenset(["a", "b"]) not in pairs


def test_short_jobs_below_duration_floor_are_ignored():
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    runs = []
    for i in range(6):
        day = BASE_TIME + timedelta(days=i)
        a_job, a_end = make_job("tiny_a", day, [{"name": "Work", "duration": 3}], needs=[])
        b_job, _ = make_job("tiny_b", a_end, [{"name": "Work", "duration": 3}], needs=[])
        runs.append(make_run("CI", [a_job, b_job], run_number=i + 9100, created_offset_hours=i * 24))

    findings = ParallelizationAnalyzer().analyze(runs)
    assert findings == []


def test_transitive_dependency_excluded():
    # a <- b <- c (c needs b, b needs a): a and c are transitively related
    # and must never be proposed as a parallel candidate even though
    # neither directly lists the other.
    import random
    from datetime import timedelta
    from tests.fixtures.synthetic_data import BASE_TIME, make_job, make_run

    runs = []
    for i in range(6):
        day = BASE_TIME + timedelta(days=i)
        a_job, a_end = make_job("a", day, [{"name": "Work", "duration": 60}], needs=[])
        b_job, b_end = make_job("b", a_end, [{"name": "Work", "duration": 60}], needs=["a"])
        c_job, _ = make_job("c", b_end, [{"name": "Work", "duration": 60}], needs=["b"])
        runs.append(make_run("CI", [a_job, b_job, c_job], run_number=i + 9200, created_offset_hours=i * 24))

    findings = ParallelizationAnalyzer().analyze(runs)
    pairs = {frozenset([f.metrics["job_a"], f.metrics["job_b"]]) for f in findings}
    assert frozenset(["a", "c"]) not in pairs


def test_empty_input_returns_no_findings():
    assert ParallelizationAnalyzer().analyze([]) == []


def test_single_run_below_min_samples_not_flagged():
    runs = sd.potentially_parallel_runs(n=1)
    findings = ParallelizationAnalyzer(ParallelizationConfig(min_run_samples=2)).analyze(runs)
    assert findings == []


def test_finding_shape_is_complete():
    runs = sd.potentially_parallel_runs()
    f = ParallelizationAnalyzer().analyze(runs)[0]
    assert f.analyzer_type == "potential_parallelization"
    assert isinstance(f.severity, Severity)
    assert isinstance(f.confidence, Confidence)
    assert len(f.evidence) >= 3
