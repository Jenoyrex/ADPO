import json

from adpo.engine import AnalysisEngine, default_analyzers
from adpo.models import Confidence, Severity
from tests.fixtures import synthetic_data as sd


def test_default_analyzers_returns_all_six():
    analyzers = default_analyzers()
    types = {a.analyzer_type for a in analyzers}
    assert types == {
        "slow_job",
        "slow_step",
        "historical_regression",
        "dependency_install",
        "failure_retry",
        "potential_parallelization",
    }


def test_engine_runs_all_analyzers_over_combined_history():
    runs = (
        sd.fast_pipeline_runs()
        + sd.slow_tests_runs()
        + sd.regression_runs()
        + sd.dependency_reinstall_runs()
        + sd.failures_runs()
        + sd.potentially_parallel_runs()
    )
    engine = AnalysisEngine()
    findings = engine.run(runs)
    assert engine.errors == []
    assert len(findings) > 0
    analyzer_types_seen = {f.analyzer_type for f in findings}
    # every analyzer should have found *something* across this combined,
    # deliberately-flawed history
    assert analyzer_types_seen == {
        "slow_job",
        "slow_step",
        "historical_regression",
        "dependency_install",
        "failure_retry",
        "potential_parallelization",
    }


def test_engine_findings_are_json_serializable():
    runs = sd.regression_runs()
    engine = AnalysisEngine()
    dicts = engine.run_as_dicts(runs)
    text = json.dumps(dicts)
    assert isinstance(text, str)
    reloaded = json.loads(text)
    assert reloaded == dicts


def test_engine_handles_empty_history_gracefully():
    engine = AnalysisEngine()
    findings = engine.run([])
    assert findings == []
    assert engine.errors == []


def test_engine_isolates_a_failing_analyzer_when_not_strict():
    class ExplodingAnalyzer:
        analyzer_type = "boom"

        def analyze(self, runs):
            raise RuntimeError("synthetic failure")

    good = default_analyzers()[0]
    engine = AnalysisEngine(analyzers=[ExplodingAnalyzer(), good], strict=False)
    findings = engine.run(sd.slow_tests_runs())
    assert len(engine.errors) == 1
    assert "boom" in engine.errors[0]
    # the good analyzer's findings should still come through
    assert any(f.analyzer_type == "slow_job" for f in findings)


def test_engine_strict_mode_reraises():
    class ExplodingAnalyzer:
        analyzer_type = "boom"

        def analyze(self, runs):
            raise RuntimeError("synthetic failure")

    engine = AnalysisEngine(analyzers=[ExplodingAnalyzer()], strict=True)
    try:
        engine.run([])
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass


def test_every_finding_has_required_fields_across_full_engine_run():
    runs = (
        sd.fast_pipeline_runs()
        + sd.slow_tests_runs()
        + sd.regression_runs()
        + sd.dependency_reinstall_runs()
        + sd.dependency_ineffective_cache_runs()
        + sd.failures_runs()
        + sd.potentially_parallel_runs()
    )
    engine = AnalysisEngine()
    findings = engine.run(runs)
    assert findings
    for f in findings:
        assert f.analyzer_type
        assert f.title
        assert f.description
        assert isinstance(f.evidence, list) and len(f.evidence) > 0
        assert isinstance(f.metrics, dict) and len(f.metrics) > 0
        assert isinstance(f.severity, Severity)
        assert isinstance(f.confidence, Confidence)
        assert f.recommendation
        # estimated_savings_range must be present as an object, either with
        # concrete bounds or explicitly marked unknown - never silently absent
        assert f.estimated_savings_range is not None
        assert f.estimated_savings_range.unknown or (
            f.estimated_savings_range.low_seconds is not None
            and f.estimated_savings_range.high_seconds is not None
        )
