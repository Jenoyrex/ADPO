# ADPO — Deterministic Analysis Engine

The first deterministic analysis engine for ADPO: six independent, evidence-based
analyzers that turn structured GitHub Actions run history into concrete, defensible
findings — **no LLM involved anywhere in this package.**

## Contents

```
adpo/
  models.py                    # Step, Job, WorkflowRun, Finding, EstimatedSavings, Severity, Confidence
  stats_utils.py                # dependency-free statistics helpers (median, percentile, cv, trend slope...)
  ingest.py                     # raw GitHub-Actions-shaped JSON -> WorkflowRun objects
  engine.py                     # AnalysisEngine: orchestrates analyzers, isolates failures
  analyzers/
    base.py                     # BaseAnalyzer contract + per-workflow grouping helper
    slow_job.py                 # 1. Slow Job Analyzer
    slow_step.py                # 2. Slow Step Analyzer
    historical_regression.py    # 3. Historical Regression Analyzer
    dependency_install.py       # 4. Dependency Installation Analyzer
    failure_retry.py            # 5. Failure/Retry Analyzer
    parallelization.py          # 6. Potential Parallelization Analyzer (deliberately conservative)
tests/
  fixtures/synthetic_data.py    # realistic, deterministic synthetic datasets
  test_*.py                     # 124 unit/integration tests, 94% statement coverage
examples/
  demo.py                       # runnable end-to-end example
REASONING.md                    # detailed rationale for every detection algorithm
```

No third-party runtime dependencies — stdlib only. `pytest` is required only to run
the test suite.

## Quick start

```bash
python3 examples/demo.py
```

```python
from adpo import AnalysisEngine
from adpo.ingest import parse_workflow_runs

runs = parse_workflow_runs(raw_json_list)   # raw_json_list: List[dict], GH-Actions-shaped
engine = AnalysisEngine()                   # runs all 6 analyzers
findings = engine.run(runs)                 # List[Finding]
findings_json = engine.run_as_dicts(runs)   # JSON-serializable

for f in findings:
    print(f.analyzer_type, f.severity.value, f.confidence.value, f.title)
```

You can also run any analyzer standalone — they have no dependency on each other or
on the engine:

```python
from adpo.analyzers import SlowJobAnalyzer
findings = SlowJobAnalyzer().analyze(runs)
```

## Running the tests

```bash
pip install pytest --break-system-packages   # or use a venv
python3 -m pytest tests/ -v
```

124 tests, 94% statement coverage of `adpo/`. See `REASONING.md` for what each test
is actually checking for and why.

## The Finding contract

Every analyzer returns a list of `Finding` objects (see `adpo/models.py`), each with:

| Field | Meaning |
|---|---|
| `analyzer_type` | which analyzer produced this (e.g. `"slow_job"`) |
| `title` | one-line summary |
| `description` | plain-language explanation of what was detected |
| `evidence` | list of concrete, auditable statements citing actual observed numbers and run IDs |
| `metrics` | structured numeric data backing the finding (sample sizes, durations, rates...) |
| `severity` | `low` / `medium` / `high` / `critical` — impact magnitude |
| `confidence` | `low` / `medium` / `high` — statistical reliability given the sample |
| `recommendation` | what a human should do next |
| `estimated_savings_range` | `EstimatedSavings(low_seconds, high_seconds, basis, unknown)` |

### On `estimated_savings_range`

**ADPO never invents a savings number.** Every non-`unknown` estimate is derived from
values actually observed in the supplied run history (e.g. "gap between this job's
median and its own best-ever observed run", "measured wasted retry time"), and the
`basis` field always states, in plain language, exactly how it was derived. When the
data doesn't support an estimate (e.g. a job is consistently slow with no observed
faster run to anchor to), `unknown=True` and both bounds are `None` — the analyzer
says so explicitly rather than guessing.

## Design principles

1. **No LLM, anywhere.** Every finding is the output of arithmetic over structured
   data (medians, percentiles, variance, rate calculations, graph traversal) — fully
   deterministic and reproducible given the same input.
2. **Every number is traceable.** No finding contains a number that isn't computed
   directly from the `WorkflowRun` objects passed in.
3. **Confidence and severity are independent axes.** A finding can have high
   potential impact but low statistical confidence (small sample), or vice versa.
4. **Analyzers are workflow-scoped internally.** `BaseAnalyzer._group_by_workflow`
   ensures a multi-workflow history handed to the engine never has one workflow's
   job statistics silently blended with an unrelated workflow's.
5. **Conservatism where it matters.** The Parallelization Analyzer in particular is
   built to never claim two jobs are safe to run concurrently just because they
   happen to run sequentially today — see `REASONING.md` §6 for the full argument.
6. **Independently testable.** Every analyzer is a plain class with an `analyze(runs)`
   method and zero dependency on the engine, on I/O, or on any other analyzer.

See `REASONING.md` for a full, per-analyzer explanation of the detection algorithm,
the thresholds chosen, and their known limitations.
