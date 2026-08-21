# Reasoning behind each detection algorithm

This document explains *why* each analyzer works the way it does: what question it's
trying to answer, why the specific statistical approach was chosen over the
alternatives, how severity/confidence/savings are derived, and what its known
limitations are. It's meant to be read alongside the analyzer source, which contains
the same reasoning in a more compact form as module docstrings.

A theme running through all six: **an analyzer should only ever say something it can
back up with numbers taken directly from the input.** No analyzer assumes a "should
take" duration, applies an industry-benchmark percentage, or asserts something is
*definitely* true from a pattern that's merely *suggestive*. Where the data doesn't
support a confident claim, the analyzer says so (`confidence=LOW`, or
`estimated_savings_range.unknown=True`) instead of filling the gap with a guess.

---

## 1. Slow Job Analyzer

**Question:** which jobs are taking up a disproportionate amount of the pipeline's
wall-clock time?

**Why relative share, not just an absolute cutoff:** a hard absolute cutoff (e.g.
"flag anything over 5 minutes") would either be too strict for pipelines that are
inherently heavy (data science training jobs, large monorepo builds) or too lax for
pipelines that are inherently light (a 90-second job might be the single biggest
bottleneck in a 2-minute pipeline). So the primary signal is **share of the
workflow's own median total duration** — a job matters relative to *its own*
pipeline, not to a global constant. An absolute floor (`absolute_severe_seconds`,
default 10 minutes) is kept as a secondary trigger so a genuinely huge job doesn't
slip through in a workflow that has many other huge jobs (where no single job would
clear a 25% share threshold).

**Why median, not mean:** CI job durations are usually right-skewed (occasional slow
runs due to contention, cold caches, etc.), and a few outliers would drag the mean up
and make "typical" performance look worse than it usually is. Median is robust to
that skew.

**Confidence = sample size, full stop.** This is deliberately the simplest possible
rule: confidence is a statement about how much data backed the number, not about how
"interesting" the finding is. `n < 4` → LOW, `4 ≤ n < 10` → MEDIUM, `n ≥ 10` → HIGH.

**Savings — the "don't invent numbers" rule in action:** the estimate is anchored to
the gap between the job's own median and its own best-observed run
(`median − min`), scaled down for the low end (only assumes partial recovery) and
used as-is for the high end (assumes it could reliably repeat its own best time). If
the coefficient of variation (stdev/mean) across observed runs is low (`< 0.10`),
that means the job is *consistently* slow — there is no run in the sample that was
meaningfully faster, so there is no empirical basis for a number, and the analyzer
returns `unknown=True` with an explanation instead of fabricating one. This is the
literal implementation of "if the system cannot confidently estimate savings, mark it
as unknown."

**Known limitation:** if a job's slowness is caused by something structural that
never varies (e.g. a fixed sleep, a large but static dataset), CV will always be low
and savings will always read `unknown` — which is the correct, honest answer; a human
still needs to investigate the *cause* (this is why the recommendation always points
at the Slow Step Analyzer).

---

## 2. Slow Step Analyzer

**Question:** *within* a job, which specific step is the bottleneck?

This is structurally the same algorithm as the Slow Job Analyzer — median duration,
CV-gated savings, sample-size-gated confidence — but with the "relative to what"
denominator changed from *the whole workflow* to *the step's own parent job*. That
localizes the finding: a Slow Job finding says "this job is a problem"; a Slow Step
finding says "and here specifically is why." Running both is intentional and they're
meant to be read together — the demo output shows this pairing directly (a slow
`unit_tests` job paired with a slow `Run tests` step inside it).

**Why not roll this into the Slow Job Analyzer?** Separating them keeps each analyzer
answering exactly one question, which keeps the code (and the resulting findings)
easy to reason about independently, and keeps them independently testable — you can
verify step-localization logic without needing job-level share logic to also be
correct, and vice versa.

**Known limitation:** steps are matched by name across runs. If a step is renamed, or
a matrix/conditional job produces a different step list run-to-run, its history is
effectively split into two shorter series (or a series that never accumulates enough
samples), which biases toward *not* flagging rather than flagging incorrectly — a
deliberate false-negative-over-false-positive bias.

---

## 3. Historical Regression Analyzer

**Question:** has a job (or the workflow as a whole) gotten reliably *slower* over
time?

**Why two windows instead of "yesterday vs. today" or a raw before/after diff:** a
single slow run is not a regression, it's noise (a busy runner, a flaky network call,
a large PR). Comparing two *windows* of runs — a baseline window (older, larger) and
a recent window (newer, more recent-fraction-sized) — and taking the **median** of
each means one or two anomalous runs on either side can't flip the result. The
analyzer additionally checks *consistency*: what fraction of the recent runs exceed
the 75th percentile of the baseline distribution. If only 1 of 6 "recent" runs is
actually slow, that's a single slow run wearing a two-window costume, not a real
regression, and consistency will be low, capping confidence at LOW.

**Why median-of-windows rather than a single statistical test (e.g. a t-test):** CI
durations are not normally distributed (right-skewed, sometimes bimodal from
cache-hit/cache-miss regimes), so a t-test's assumptions don't hold well without more
machinery than is justified here. A window-median-and-consistency-ratio approach is
simple, interpretable in the evidence text ("8/10 recent runs exceeded the 75th
percentile of baseline"), and doesn't require distributional assumptions.

**The supplementary trend slope:** in addition to the two-window comparison (the
*primary* detector), the analyzer also computes a simple least-squares slope of
duration vs. run index across the *entire* series and reports it as a metric. This is
not used as a trigger by itself (a slope alone can't distinguish "3 slow runs at the
very end" from "genuinely gradual drift"), but it's useful context in the output:
a strongly positive slope corroborates a two-window finding; a near-zero slope on
a flagged finding would be a signal to a human reviewer that the "regression" might
actually be a step change worth double-checking.

**Two granularities, one algorithm:** the same window-comparison function runs once
per job name *and* once on the overall workflow wall-clock duration (keyed internally
as `__workflow_total__`). A job-level regression and a workflow-level regression can
both fire from the same underlying cause (as in the demo output) — that's intentional
and not treated as redundant, since a workflow-level regression with no single
job-level regression would itself be an interesting, different finding (e.g. queue
time or a new job being added).

**Savings:** low end = `recent_median − baseline_median` (what's provably being lost
right now, per run, relative to what this exact job/workflow reliably achieved
before); high end = `recent_median − min(baseline)` (best case: full recovery to the
best time ever seen in the baseline window). Both bounds are real numbers that
happened, not assumptions.

**Known limitation:** if a regression happens very gradually across the *entire*
supplied history (no clear "before" window because it's been drifting since sample
1), the baseline window itself will already be partly degraded and the detector will
under-report the true magnitude. This is a real limitation of a windowed approach and
is called out here rather than silently accepted — a longer history reduces its
impact.

---

## 4. Dependency Installation Analyzer

**Question:** is CI time being spent repeatedly re-installing dependencies that could
be cached?

**Why keyword/pattern matching instead of executing or parsing the command:** the
analyzer only has structured metadata (step names, `run:` command text, `uses:`
action references) — it never executes anything. A fixed list of well-known
install-command substrings (`npm ci`, `pip install`, `bundle install`, `apt-get
install`, ...) is checked against the lowercased step name + command text. This is
plain string matching — the same category of technique a linter uses — not inference,
and it's fully deterministic and auditable (the matched command snippet is included
in the evidence).

**Detecting caching:** the analyzer looks, within the same job, for a step that
either uses the generic `actions/cache` action, or uses one of the common
`actions/setup-*` actions **with a truthy `cache` input** (e.g. `actions/setup-node`
with `cache: npm`). If no such step appears before (or anywhere relative to) the
matched install step in the most recent run, the install is classified `uncached`.

**Two classifications, two confidence ceilings:**
- `uncached` — no cache action detected at all. This is a straightforward, high-
  confidence structural observation (either the step is there or it isn't).
- `cache_possibly_ineffective` — a cache action *is* present, but duration still
  varies a lot run-to-run (CV above a threshold). This is a much weaker claim: run
  metadata alone can never show an actual cache hit/miss (that's in the job logs, not
  the structured data ADPO has access to), so this classification's confidence is
  **capped at MEDIUM** even with a large sample — the code enforces this explicitly
  rather than letting sample size alone drive it to HIGH, exactly because sample size
  isn't the limiting factor here; the *type* of evidence is.

**Savings:** identical empirical-gap logic to the Slow Job/Step analyzers — anchored
to `median − p25` (conservative) and `median − min` (optimistic) of the step's own
observed durations, only when CV is high enough to mean a faster run was actually
observed. A consistently-slow-and-uncached install still gets flagged (the *practice*
of not caching is worth flagging on its own), but its savings are marked `unknown` if
there's no faster run in the sample to anchor to — caching would still be expected to
help "in principle," and the recommendation says exactly that, but the analyzer
refuses to put a number on "in principle."

**Bonus signal — duplicated installs across jobs:** if the same step name/pattern
occurs independently in multiple jobs in the same run (e.g. a matrix build, or
several jobs that each re-resolve the same lockfile), that's flagged as a metric
(`duplicate_job_occurrences`) and called out in the evidence and recommendation,
since a shared cache key would compound the benefit — this is a pure counting
operation, not a further estimate.

**Known limitation:** mixing a period with no caching and a later period where
caching was added (under the same step name) will show up as high variance even if
each sub-period was individually stable, which could trigger the
`cache_possibly_ineffective` path when what's actually happened is "caching was
recently added and is working fine, older samples are just stale." This is the same
category of limitation as the Historical Regression Analyzer's window-drift issue,
and the fix is the same: this analyzer is meant to be read alongside the regression
analyzer's output, not in isolation.

---

## 5. Failure/Retry Analyzer

**Question:** which jobs fail often enough to matter, and how much compute time is
actually being burned on retries?

Two genuinely different problems are kept as two separate detection paths within one
analyzer, because they call for different fixes:

### 5a. Flaky vs. chronically-failing jobs

**Canonicalizing retries first:** runs are grouped by `run_number`, and within each
group only the **final** attempt (highest `run_attempt`) counts toward a job's
pass/fail rate. Without this step, a job that failed once and then passed on retry
would be counted as *both* a failure and a success for what is really one logical
CI run, inflating the apparent failure rate.

**Why a failure-rate *band*, not just "any failure":** a job that fails in 3 of 40
runs behaves very differently from a job that fails in 39 of 40 runs, even though
both have "some failures." The former is genuinely intermittent (worth calling
*flaky* — investigate for non-determinism); the latter is not intermittent at all,
it's *broken* (worth calling *chronic failure* — investigate for a real, reproducible
bug). The analyzer uses a rate band (`flaky_min_rate=0.05` to
`chronic_failure_rate=0.85`) to distinguish the two, and gives each its own title,
severity curve, and recommendation text — critically, the chronic-failure
recommendation explicitly says this doesn't look like a retry/flakiness problem, so a
team doesn't waste effort adding retries to a job that's simply broken.

**Savings here are literal, not modeled:** for both flaky and chronic jobs, the
"savings" is the actual measured compute time consumed by the observed *failing*
runs before they failed (`sum`/`avg`/`max` of their durations) — time that was really
spent and would be avoided (or at least not repeated) if the failure were fixed. This
is about as defensible as a number can be: it already happened.

### 5b. Retry-driven wasted compute time

Separately from the pass/fail-rate view, the analyzer looks at `run_number` groups
with **more than one attempt** and sums the job durations of every attempt *except*
the final (successful-or-not) one. That sum is directly-measured wasted compute — no
modeling, no assumption about what "should" happen, just "this time was spent on an
attempt that got superseded." `retry_rate` (fraction of runs needing >1 attempt) and
`average/max wasted seconds` come straight out of that sum.

**Why keep this separate from 5a:** a workflow can have a very low per-job failure
rate but still show meaningful retry waste (e.g. one expensive job that occasionally
needs a manual re-run), or vice versa (many flaky jobs that individually rarely
trigger a full workflow re-run because failures cluster). Reporting them separately
lets a reader see both "which job is unreliable" and "how much did unreliability
actually cost," which aren't always the same job.

**Known limitation:** the analyzer only sees `run_attempt` as recorded on each
`WorkflowRun`/`Job`. If a caller's ingestion doesn't correctly enrich multiple
attempts of the same logical run with matching `run_number`s and increasing
`run_attempt`s, retry waste won't be detected — this is documented as an ingestion
contract, not something the analyzer can infer from nothing.

---

## 6. Potential Parallelization Analyzer — and why it's deliberately conservative

**Question:** are there jobs that *could* be worth investigating for parallel
execution?

This is the analyzer where getting the epistemics right matters more than getting a
clever detection rule right, so it's worth spending the most space on.

### The core insight and its limits

GitHub Actions jobs run in whatever order the scheduler chooses, constrained only by
`needs:` edges. If two jobs have **no `needs` relationship, direct or transitive**,
the scheduler is free to run them concurrently — and normally will, if runner
capacity allows. So: if two such jobs are observed, consistently, across many runs,
to **never overlap in execution time**, that's a genuine anomaly worth a second
look. It is *not*, by itself, evidence that parallelizing them is safe. There are (at
least) three explanations for the same observed pattern, and the data alone cannot
distinguish which one applies:

1. **A real but undeclared dependency.** Job B might read a file, artifact, cache
   entry, or piece of external state that job A produces, without that being
   expressed as a `needs:` edge. If so, this pattern is actually surfacing a *latent
   correctness bug* (an implicit ordering requirement that isn't guaranteed by the
   scheduler and could silently break if these jobs ever *did* get scheduled
   concurrently) — which is valuable to know regardless of any speed benefit, but is
   the opposite of "safe to parallelize now."
2. **Runner contention.** A small self-hosted runner pool, or account-level
   concurrency limits, can force otherwise-independent jobs to queue behind each
   other. In this case the jobs genuinely are independent and parallelizing them
   (once capacity allows) would help — but the analyzer can't tell this case apart
   from case 1 using only job timestamps and a `needs:` graph.
3. **Coincidence.** With few observations, "always sequential so far" could just be
   luck. This is mitigated (not eliminated) by requiring a minimum sample size and a
   high consistency ratio, but it can't be ruled out entirely from run metadata.

Because the analyzer genuinely cannot tell these apart, the design commits, at every
layer, to never asserting the parallelization-safe conclusion:

- **`description`/`recommendation` text explicitly states** this is not a safety
  determination and lists the concrete things a human needs to check first (shared
  artifacts/cache/state, runner capacity) before touching the workflow.
- **`confidence` is capped at MEDIUM**, never HIGH, by design (`_confidence`'s
  docstring makes this explicit) — because HIGH confidence would read as "confident
  this is a real opportunity," which is exactly the claim this analyzer is not
  entitled to make. What *can* be stated with real confidence is the pattern itself
  (no declared edge + consistently no overlap); that pattern-confidence is what the
  MEDIUM/LOW scale actually measures here.
- **`severity` is capped at MEDIUM**, never HIGH/CRITICAL — this is framed
  consistently as an investigation opportunity, not a confirmed problem, so it should
  never crowd out findings from analyzers that measure an uncontested, already-paid
  cost (a chronic failure, a measured regression).
- **`estimated_savings_range.low_seconds` is always `0.0`.** The high end is the
  textbook best case for merging two sequential independent tasks into parallel
  execution — `min(median_A, median_B)`, since the critical path becomes
  `max(median_A, median_B)` instead of `median_A + median_B` — but the low end is
  pinned to zero, in writing, because the true achievable savings may genuinely be
  zero if it turns out these jobs cannot be safely parallelized at all.
- **A unit test (`test_finding_never_asserts_jobs_are_safe_to_parallelize`) actively
  greps every finding's text for confident-safety phrasing** ("safe to parallelize,"
  "guaranteed," "definitely safe") and fails the build if it ever appears — this
  requirement is enforced by the test suite, not just by convention in the prose.

### Why `needs:`-declared pairs are never candidates, ever

If a `needs:` edge exists (directly, or transitively through a chain), the pair is
excluded from consideration entirely — the analyzer never second-guesses a declared
dependency edge or suggests removing one. Those edges represent an explicit,
human-made statement of intent; ADPO's job is to surface *undeclared* patterns worth
reviewing, not to relitigate declared ones. (The transitive closure is computed with
a small DFS over the `needs:` graph of the *current* — i.e. most recent — run for
each workflow, so A→B→C excludes the A/C pair even though neither directly lists the
other.)

### Why consistency (not just "happened once") is required

The analyzer requires both a minimum number of observed run-pairs
(`min_run_samples`, default 2) and a high consistency ratio (`min_sequential_
consistency`, default 0.75) of "no overlap" occurrences before it will surface a
pair at all. A single sequential occurrence proves nothing; a pattern that holds in
9 of 10 runs is a real, repeatable behavior of the pipeline, which is the bar this
analyzer is trying to clear before asking a human to spend time on it.

### Known limitation

The "current job set" used to decide which pairs to even consider comes from the
**most recent run** of each workflow (by `created_at`). If a workflow's job
structure changes frequently, older run history for jobs that have since been
renamed or removed won't contribute to any pair's evidence — which biases toward
*fewer* findings, not incorrect ones, consistent with this analyzer's overall
conservative posture.

---

## A note on combining datasets

Every analyzer groups its input by `workflow_name` internally
(`BaseAnalyzer._group_by_workflow`) before computing any statistic, specifically so
that `AnalysisEngine.run()` is safe to call with a run history spanning multiple,
unrelated workflows (e.g. an entire repository's `CI.yml` *and* `Release.yml`
history) without one workflow's job named `"build"` being silently averaged together
with an unrelated workflow's job that happens to share the same name. The synthetic
test fixtures deliberately use distinct `workflow_name`s per scenario for the same
reason — see `tests/test_engine.py::test_engine_runs_all_analyzers_over_combined_
history` for a test that exercises all six analyzers over one combined, multi-
workflow history in a single `AnalysisEngine.run()` call.
