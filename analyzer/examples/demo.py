#!/usr/bin/env python3
"""
Demo: run the full ADPO deterministic analysis engine over a combined,
deliberately-flawed synthetic run history and print the findings as JSON.

Run with:  python3 examples/demo.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from adpo.engine import AnalysisEngine  # noqa: E402
from fixtures import synthetic_data as sd  # noqa: E402


def main() -> None:
    runs = (
        sd.slow_tests_runs()
        + sd.regression_runs()
        + sd.dependency_reinstall_runs()
        + sd.failures_runs()
        + sd.potentially_parallel_runs()
    )

    engine = AnalysisEngine()
    findings = engine.run(runs)

    print(f"Analyzed {len(runs)} workflow run(s); {len(findings)} finding(s) produced.")
    if engine.errors:
        print("Analyzer errors:", engine.errors)
    print()

    by_type = {}
    for f in findings:
        by_type.setdefault(f.analyzer_type, []).append(f)

    for analyzer_type, group in by_type.items():
        print(f"=== {analyzer_type} ({len(group)} finding(s)) ===")
        for f in group:
            print(f"  [{f.severity.value.upper():>8}] [{f.confidence.value:>6} confidence] {f.title}")
        print()

    print("--- Full JSON output (first finding) ---")
    if findings:
        print(json.dumps(findings[0].to_dict(), indent=2))


if __name__ == "__main__":
    main()
