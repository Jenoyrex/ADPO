"""
Cross-cutting edge-case and missing-data tests: every analyzer, run
individually, must survive odd-but-structurally-valid input without
raising and without fabricating findings from insufficient data.
"""
import pytest

from adpo.engine import default_analyzers
from tests.fixtures import synthetic_data as sd

ALL_ANALYZERS = default_analyzers()


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=lambda a: a.analyzer_type)
def test_analyzer_survives_empty_history(analyzer):
    assert analyzer.analyze([]) == []


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=lambda a: a.analyzer_type)
def test_analyzer_survives_single_run(analyzer):
    runs = sd.single_run_only()
    # must not raise; a single data point should never be enough for a
    # confident finding, but analyzers are allowed to return [].
    result = analyzer.analyze(runs)
    assert isinstance(result, list)


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=lambda a: a.analyzer_type)
def test_analyzer_survives_all_edge_cases_individually(analyzer):
    cases = sd.edge_case_runs()
    for name, run in cases.items():
        result = analyzer.analyze([run])
        assert isinstance(result, list), f"{analyzer.analyzer_type} failed on edge case {name!r}"


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=lambda a: a.analyzer_type)
def test_analyzer_survives_all_edge_cases_combined(analyzer):
    cases = sd.edge_case_runs()
    result = analyzer.analyze(list(cases.values()))
    assert isinstance(result, list)


def test_zero_steps_job_produces_no_step_level_findings():
    from adpo.analyzers.slow_step import SlowStepAnalyzer
    from adpo.analyzers.dependency_install import DependencyInstallAnalyzer

    cases = sd.edge_case_runs()
    assert SlowStepAnalyzer().analyze([cases["zero_steps"]]) == []
    assert DependencyInstallAnalyzer().analyze([cases["zero_steps"]]) == []


def test_in_progress_job_excluded_from_duration_based_analyzers():
    from adpo.analyzers.slow_job import SlowJobAnalyzer

    cases = sd.edge_case_runs()
    # job.conclusion is None and completed_at is None -> must not be
    # treated as a valid (fast or slow) successful duration sample.
    assert SlowJobAnalyzer().analyze([cases["job_in_progress"]]) == []


def test_dangling_needs_reference_does_not_crash_parallelization_analyzer():
    from adpo.analyzers.parallelization import ParallelizationAnalyzer

    cases = sd.edge_case_runs()
    result = ParallelizationAnalyzer().analyze([cases["dangling_needs"]])
    assert result == []


def test_no_jobs_run_does_not_crash_any_analyzer():
    cases = sd.edge_case_runs()
    for analyzer in ALL_ANALYZERS:
        assert analyzer.analyze([cases["no_jobs"]]) == []


def test_skipped_step_excluded_from_step_analyzers():
    from adpo.analyzers.slow_step import SlowStepAnalyzer

    cases = sd.edge_case_runs()
    result = SlowStepAnalyzer().analyze([cases["skipped_step"]])
    assert result == []


def test_mixed_valid_and_invalid_runs_still_analyzes_the_valid_ones():
    # A realistic scenario: most history is fine, one run has partial data.
    cases = sd.edge_case_runs()
    good_runs = sd.slow_tests_runs()
    from adpo.analyzers.slow_job import SlowJobAnalyzer

    combined = good_runs + [cases["job_in_progress"], cases["no_jobs"], cases["zero_steps"]]
    findings = SlowJobAnalyzer().analyze(combined)
    assert len(findings) == 1
    assert findings[0].metrics["job_name"] == "unit_tests"
