"""
Synthetic, realistic GitHub-Actions-shaped datasets used across the unit
test suite. Every function here is deterministic (seeded RNG) so tests
never flake.

Covers the scenarios called for by the ADPO spec:
  * fast_pipeline_runs            - a healthy pipeline (negative control)
  * slow_tests_runs                - a consistently slow (but stable) job/step
  * regression_runs                - a job that got reliably slower over time
  * dependency_reinstall_runs      - uncached vs cached dependency installs
  * failures_runs                  - flaky job, chronic failure, retry incident
  * potentially_parallel_runs      - independent jobs that run sequentially
  * edge_case_runs                 - odd-but-valid shapes (empty steps,
                                      in-progress jobs, dangling `needs`, etc.)
  * missing_data helpers           - empty history / single-run history
"""
from __future__ import annotations

import itertools
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from adpo.models import Job, Step, WorkflowRun

BASE_TIME = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

_run_number_counter = itertools.count(1)


def _next_run_number() -> int:
    return next(_run_number_counter)


def make_step(
    cursor: datetime,
    name: str,
    duration_seconds: Optional[float],
    number: int,
    conclusion: str = "success",
    status: str = "completed",
    uses: Optional[str] = None,
    with_params: Optional[dict] = None,
    run: Optional[str] = None,
    has_timestamps: bool = True,
):
    start = cursor if has_timestamps else None
    end = None
    if has_timestamps and duration_seconds is not None:
        end = cursor + timedelta(seconds=duration_seconds)
    return Step(
        name=name,
        number=number,
        status=status,
        conclusion=conclusion,
        started_at=start,
        completed_at=end,
        uses=uses,
        with_params=with_params or {},
        run=run,
    )


def make_job(
    name: str,
    start_time: datetime,
    step_specs: List[dict],
    conclusion: str = "success",
    needs: Optional[List[str]] = None,
    run_attempt: int = 1,
    runs_on: str = "ubuntu-latest",
    job_id: Optional[str] = None,
):
    """Lay `step_specs` out sequentially starting at `start_time`.

    Each spec: {"name", "duration" (seconds or None), "conclusion", "uses",
    "with_params", "run", "has_timestamps"}.
    Returns (Job, job_end_datetime).
    """
    cursor = start_time
    steps = []
    for i, spec in enumerate(step_specs, start=1):
        dur = spec.get("duration")
        step = make_step(
            cursor,
            spec["name"],
            dur,
            number=i,
            conclusion=spec.get("conclusion", "success"),
            status=spec.get("status", "completed"),
            uses=spec.get("uses"),
            with_params=spec.get("with_params"),
            run=spec.get("run"),
            has_timestamps=spec.get("has_timestamps", True),
        )
        steps.append(step)
        if dur is not None and spec.get("has_timestamps", True):
            cursor = cursor + timedelta(seconds=dur)

    job = Job(
        job_id=job_id or name,
        name=name,
        status="completed" if conclusion is not None else "in_progress",
        conclusion=conclusion,
        started_at=start_time,
        completed_at=cursor if conclusion is not None else None,
        steps=steps,
        needs=needs or [],
        runs_on=runs_on,
        run_attempt=run_attempt,
    )
    return job, cursor


def make_run(
    workflow_name: str,
    jobs: List[Job],
    branch: str = "main",
    event: str = "push",
    conclusion: Optional[str] = None,
    run_number: Optional[int] = None,
    run_attempt: int = 1,
    created_offset_hours: float = 0.0,
    run_id: Optional[str] = None,
) -> WorkflowRun:
    if conclusion is None:
        concs = [j.conclusion for j in jobs]
        conclusion = "failure" if "failure" in concs else ("success" if concs and all(c == "success" for c in concs) else None)
    rn = run_number if run_number is not None else _next_run_number()
    rid = run_id or f"run-{rn}-attempt-{run_attempt}"
    created = BASE_TIME + timedelta(hours=created_offset_hours)
    return WorkflowRun(
        run_id=rid,
        workflow_name=workflow_name,
        run_number=rn,
        branch=branch,
        event=event,
        created_at=created,
        conclusion=conclusion,
        jobs=jobs,
        run_attempt=run_attempt,
    )


# ---------------------------------------------------------------------------
# 1. Fast, healthy pipeline (negative control - no findings expected)
# ---------------------------------------------------------------------------
def fast_pipeline_runs(n: int = 10, seed: int = 1) -> List[WorkflowRun]:
    # Job names are deliberately prefixed ("fast_*") so that concatenating
    # this dataset with the others in a cross-analyzer/engine test does not
    # silently blend unrelated job-name statistics together (see
    # REASONING.md, "A note on combining datasets").
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)
        build_job, _ = make_job(
            "fast_build",
            day,
            [
                {"name": "Checkout", "duration": 4 + rng.uniform(-1, 1), "uses": "actions/checkout@v4"},
                {"name": "Install dependencies", "duration": 8 + rng.uniform(-2, 2), "run": "npm ci"},
                {"name": "Build", "duration": 18 + rng.uniform(-3, 3), "run": "npm run build"},
            ],
            needs=[],
        )
        test_job, _ = make_job(
            "fast_test",
            day,  # starts at the same time as build -> already overlapping/parallel
            [
                {"name": "Checkout", "duration": 4 + rng.uniform(-1, 1), "uses": "actions/checkout@v4"},
                {"name": "Run unit tests", "duration": 15 + rng.uniform(-3, 3), "run": "npm test"},
            ],
            needs=[],
        )
        run = make_run(
            "CI-FastPipeline",
            [build_job, test_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# 2. Slow tests: a consistently slow (low-variance) job/step
# ---------------------------------------------------------------------------
def slow_tests_runs(n: int = 8, seed: int = 2) -> List[WorkflowRun]:
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)
        build_job, build_end = make_job(
            "build",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {"name": "Build", "duration": 25 + rng.uniform(-2, 2), "run": "npm run build"},
            ],
            needs=[],
        )
        unit_tests_job, _ = make_job(
            "unit_tests",
            build_end,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                # Consistently slow, low variance -> savings should be "unknown"
                {"name": "Run tests", "duration": 900 + rng.uniform(-8, 8), "run": "npm run test:full"},
            ],
            needs=["build"],
        )
        run = make_run(
            "CI-SlowTests",
            [build_job, unit_tests_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# 3. Regression: a job that reliably got slower partway through history
# ---------------------------------------------------------------------------
def regression_runs(n: int = 16, seed: int = 3, regress_at: int = 10) -> List[WorkflowRun]:
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)
        if i < regress_at:
            duration = 60 + rng.uniform(-5, 5)
        else:
            duration = 200 + rng.uniform(-8, 8)
        build_job, _ = make_job(
            "build",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {"name": "Build", "duration": duration, "run": "npm run build"},
            ],
            needs=[],
        )
        run = make_run(
            "CI-Regression",
            [build_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# 4. Dependency (re)installation: uncached vs cached
# ---------------------------------------------------------------------------
def dependency_reinstall_runs(n: int = 8, seed: int = 4) -> List[WorkflowRun]:
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)

        uncached_job, _ = make_job(
            "build_uncached",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {"name": "Install dependencies", "duration": 90 + rng.uniform(-15, 15), "run": "npm ci"},
            ],
            needs=[],
        )

        cached_job, _ = make_job(
            "build_cached",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {
                    "name": "Setup Node",
                    "duration": 3,
                    "uses": "actions/setup-node@v4",
                    "with_params": {"node-version": "20", "cache": "npm"},
                },
                {"name": "Install dependencies", "duration": 12 + rng.uniform(-1, 1), "run": "npm ci"},
            ],
            needs=[],
        )

        run = make_run(
            "CI-DependencyInstall",
            [uncached_job, cached_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)
    return runs


def dependency_ineffective_cache_runs(n: int = 8, seed: int = 40) -> List[WorkflowRun]:
    """A cache action is present, but install duration still varies a lot -
    the 'cache present but possibly not hitting reliably' branch."""
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)
        # Alternate between a "hit" (fast) and "miss" (slow) pattern.
        duration = (15 if i % 2 == 0 else 110) + rng.uniform(-3, 3)
        job, _ = make_job(
            "build",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {
                    "name": "Setup Python",
                    "duration": 3,
                    "uses": "actions/setup-python@v5",
                    "with_params": {"python-version": "3.12", "cache": "pip"},
                },
                {"name": "Install dependencies", "duration": duration, "run": "pip install -r requirements.txt"},
            ],
            needs=[],
        )
        runs.append(
            make_run(
                "CI-DependencyInstall-IneffectiveCache",
                [job],
                run_number=_next_run_number(),
                created_offset_hours=i * 24,
            )
        )
    return runs


# ---------------------------------------------------------------------------
# 5. Failures: flaky job, chronic failure, and a retry incident
# ---------------------------------------------------------------------------
def failures_runs(n: int = 12, seed: int = 5) -> List[WorkflowRun]:
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)

        # flaky_e2e: fails roughly 1 in 3 times (intermittent, not chronic)
        e2e_fails = (i % 3 == 0)
        e2e_job, _ = make_job(
            "flaky_e2e",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {
                    "name": "Run e2e tests",
                    "duration": 45 + rng.uniform(-5, 5),
                    "conclusion": "failure" if e2e_fails else "success",
                    "run": "npm run test:e2e",
                },
            ],
            conclusion="failure" if e2e_fails else "success",
            needs=[],
        )

        # chronic_fail_job: fails in 11/12 runs (genuinely broken)
        chronic_fails = (i != 6)
        chronic_job, _ = make_job(
            "chronic_fail_job",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {
                    "name": "Run integration tests",
                    "duration": 20 + rng.uniform(-2, 2),
                    "conclusion": "failure" if chronic_fails else "success",
                    "run": "npm run test:integration",
                },
            ],
            conclusion="failure" if chronic_fails else "success",
            needs=[],
        )

        run = make_run(
            "CI-Failures",
            [e2e_job, chronic_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)

    # Explicit retry incident: run_number reused, attempt 1 fails, attempt 2 succeeds.
    retry_run_number = _next_run_number()
    day = BASE_TIME + timedelta(days=n)

    failing_job, _ = make_job(
        "flaky_e2e",
        day,
        [
            {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
            {"name": "Run e2e tests", "duration": 50, "conclusion": "failure", "run": "npm run test:e2e"},
        ],
        conclusion="failure",
        needs=[],
        run_attempt=1,
    )
    attempt1 = make_run(
        "CI-Failures",
        [failing_job],
        run_number=retry_run_number,
        run_attempt=1,
        created_offset_hours=n * 24,
        run_id=f"run-{retry_run_number}-attempt-1",
    )

    succeeding_job, _ = make_job(
        "flaky_e2e",
        day + timedelta(minutes=5),
        [
            {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
            {"name": "Run e2e tests", "duration": 42, "conclusion": "success", "run": "npm run test:e2e"},
        ],
        conclusion="success",
        needs=[],
        run_attempt=2,
    )
    attempt2 = make_run(
        "CI-Failures",
        [succeeding_job],
        run_number=retry_run_number,
        run_attempt=2,
        created_offset_hours=n * 24 + 0.1,
        run_id=f"run-{retry_run_number}-attempt-2",
    )
    runs.extend([attempt1, attempt2])
    return runs


# ---------------------------------------------------------------------------
# 6. Potentially-parallel jobs: independent jobs that run sequentially
# ---------------------------------------------------------------------------
def potentially_parallel_runs(n: int = 6, seed: int = 6) -> List[WorkflowRun]:
    rng = random.Random(seed)
    runs = []
    for i in range(n):
        day = BASE_TIME + timedelta(days=i)
        lint_job, lint_end = make_job(
            "lint",
            day,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {"name": "Run lint", "duration": 40 + rng.uniform(-4, 4), "run": "npm run lint"},
            ],
            needs=[],  # no declared dependency
        )
        # unit_tests_parallel_candidate always starts right when lint ends ->
        # consistently sequential. Named distinctly from other datasets'
        # "unit_tests" job so combining fixtures never blends statistics.
        unit_tests_job, ut_end = make_job(
            "unit_tests_parallel_candidate",
            lint_end,
            [
                {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
                {"name": "Run unit tests", "duration": 55 + rng.uniform(-5, 5), "run": "npm test"},
            ],
            needs=[],  # ALSO no declared dependency on lint -> real candidate pair
        )
        # deploy legitimately needs both -> must NOT be flagged as a candidate
        deploy_job, _ = make_job(
            "deploy",
            ut_end,
            [
                {"name": "Deploy", "duration": 20 + rng.uniform(-2, 2), "run": "./deploy.sh"},
            ],
            needs=["lint", "unit_tests_parallel_candidate"],
        )
        run = make_run(
            "CI-Parallel",
            [lint_job, unit_tests_job, deploy_job],
            run_number=_next_run_number(),
            created_offset_hours=i * 24,
        )
        runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# 7. Edge cases: odd-but-structurally-valid data
# ---------------------------------------------------------------------------
def edge_case_runs() -> Dict[str, WorkflowRun]:
    """Returns a dict of individually-named edge case runs so tests can pick
    exactly the one they need without depending on list ordering."""
    cases: Dict[str, WorkflowRun] = {}

    # (a) job with zero steps
    empty_steps_job = Job(
        job_id="j-empty-steps",
        name="noop",
        status="completed",
        conclusion="success",
        started_at=BASE_TIME,
        completed_at=BASE_TIME + timedelta(seconds=5),
        steps=[],
        needs=[],
    )
    cases["zero_steps"] = make_run(
        "EdgeCaseWF", [empty_steps_job], run_number=_next_run_number(), created_offset_hours=1000
    )

    # (b) job still in progress / cancelled mid-flight (no completed_at)
    inflight_job = Job(
        job_id="j-inflight",
        name="inflight",
        status="in_progress",
        conclusion=None,
        started_at=BASE_TIME,
        completed_at=None,
        steps=[
            Step(name="Checkout", number=1, status="completed", conclusion="success",
                 started_at=BASE_TIME, completed_at=BASE_TIME + timedelta(seconds=4)),
            Step(name="Long step", number=2, status="in_progress", conclusion=None,
                 started_at=BASE_TIME + timedelta(seconds=4), completed_at=None),
        ],
        needs=[],
    )
    cases["job_in_progress"] = make_run(
        "EdgeCaseWF", [inflight_job], run_number=_next_run_number(),
        conclusion=None, created_offset_hours=1001,
    )

    # (c) job referencing a `needs` target that doesn't exist in this run
    dangling_job = Job(
        job_id="j-dangling",
        name="child",
        status="completed",
        conclusion="success",
        started_at=BASE_TIME,
        completed_at=BASE_TIME + timedelta(seconds=10),
        steps=[],
        needs=["ghost_parent"],
    )
    cases["dangling_needs"] = make_run(
        "EdgeCaseWF", [dangling_job], run_number=_next_run_number(), created_offset_hours=1002
    )

    # (d) workflow run with no jobs at all
    cases["no_jobs"] = make_run(
        "EdgeCaseWF", [], run_number=_next_run_number(), conclusion="success", created_offset_hours=1003
    )

    # (e) step marked as skipped (no timestamps)
    skipped_step_job, _ = make_job(
        "conditional_job",
        BASE_TIME,
        [
            {"name": "Checkout", "duration": 4, "uses": "actions/checkout@v4"},
            {"name": "Deploy (skipped)", "duration": None, "conclusion": "skipped", "has_timestamps": False},
        ],
        needs=[],
    )
    cases["skipped_step"] = make_run(
        "EdgeCaseWF", [skipped_step_job], run_number=_next_run_number(), created_offset_hours=1004
    )

    return cases


# ---------------------------------------------------------------------------
# 8. Missing / absent data
# ---------------------------------------------------------------------------
def empty_runs() -> List[WorkflowRun]:
    return []


def single_run_only(seed: int = 8) -> List[WorkflowRun]:
    rng = random.Random(seed)
    job, _ = make_job(
        "build",
        BASE_TIME,
        [{"name": "Build", "duration": 60 + rng.uniform(-5, 5), "run": "npm run build"}],
        needs=[],
    )
    return [make_run("CI", [job], run_number=_next_run_number(), created_offset_hours=2000)]
