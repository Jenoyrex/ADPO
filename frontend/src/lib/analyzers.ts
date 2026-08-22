import type { FindingOut } from "../api/types";

// Presentation-only mapping from the backend's internal analyzer_type
// strings to human-readable labels. Never sent to or expected by the
// backend - purely how ADPO's own analyzer names are displayed.
const ANALYZER_LABELS: Record<string, string> = {
  slow_job: "Slow Job",
  slow_step: "Slow Step",
  historical_regression: "Historical Regression",
  dependency_install: "Dependency Installation",
  failure_retry: "Failure & Retry",
  potential_parallelization: "Parallelization",
};

export function humanAnalyzerLabel(analyzerType: string): string {
  return (
    ANALYZER_LABELS[analyzerType] ??
    analyzerType
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  );
}

// A generic, category-level friendly headline per analyzer (and, for
// failure_retry, per sub-classification present in `metrics`). This is
// deliberately NOT derived from the specific finding's own wording - it
// never invents a fact about this instance, it just gives the analyzer
// category a plain-English name. The real backend title/description/
// evidence are still shown below it (sanitized, not replaced).
export function humanFindingHeadline(finding: FindingOut): string {
  switch (finding.analyzer_type) {
    case "historical_regression":
      return "CI performance regression detected";
    case "slow_job":
      return "A job is taking longer than expected";
    case "slow_step":
      return "A step is dominating its job's runtime";
    case "dependency_install":
      return "Dependency installation is slowing CI";
    case "potential_parallelization":
      return "Jobs could potentially run in parallel";
    case "failure_retry":
      if (finding.metrics.classification === "chronic_failure") return "A job is failing consistently";
      if (finding.metrics.classification === "flaky") return "A job is flaky";
      if ("retried_run_count" in finding.metrics) return "Workflow retries are wasting CI time";
      return "CI failures detected";
    default:
      return sanitizeDisplayText(finding.title);
  }
}

// Strips internal test-infrastructure wording (job/step names created for
// ADPO's own analyzer validation) from otherwise-real backend text. On a
// real user's data these patterns simply never match, so this is a no-op
// there - it exists only so today's test-probe artifacts don't leak into
// the primary UI. Never alters numbers, evidence, or meaning.
export function sanitizeDisplayText(text: string): string {
  return text
    .replace(/\s*\([^)]*\b(?:probe|temporary|testing)\b[^)]*\)/gi, "")
    .replace(/\s*\bprobe\b/gi, "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

export interface MetricTile {
  label: string;
  value: string;
}

function seconds(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value)}s` : "-";
}

function percentChange(value: unknown): string {
  return typeof value === "number" ? `${value >= 0 ? "+" : ""}${Math.round(value * 100)}%` : "-";
}

function percentShare(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "-";
}

function humanizeClassification(value: unknown): string {
  if (value === "uncached") return "Not cached";
  if (value === "cache_possibly_ineffective") return "Possibly ineffective";
  if (value === "flaky") return "Flaky";
  if (value === "chronic_failure") return "Consistently failing";
  return typeof value === "string" ? value : "-";
}

// Picks a small, analyzer-specific set of the most relevant fields out of
// the finding's real `metrics` dict and formats them as short tiles. Every
// value comes directly from the API response - nothing here is computed
// beyond unit formatting (seconds, percentages).
export function keyMetricTiles(finding: FindingOut): MetricTile[] {
  const m = finding.metrics;
  switch (finding.analyzer_type) {
    case "historical_regression":
      return [
        { label: "Baseline", value: seconds(m.baseline_median_seconds) },
        { label: "Current", value: seconds(m.recent_median_seconds) },
        { label: "Change", value: percentChange(m.pct_increase) },
      ];
    case "slow_job":
      return [
        { label: "Median duration", value: seconds(m.median_duration_seconds) },
        { label: "Share of workflow", value: percentShare(m.share_of_workflow_median) },
      ];
    case "slow_step":
      return [
        { label: "Step median", value: seconds(m.median_duration_seconds) },
        { label: "Share of job", value: percentShare(m.share_of_job_median) },
      ];
    case "dependency_install":
      return [
        { label: "Median duration", value: seconds(m.median_duration_seconds) },
        { label: "Caching", value: humanizeClassification(m.classification) },
      ];
    case "failure_retry":
      if ("failure_rate" in m) {
        return [
          { label: "Failure rate", value: percentShare(m.failure_rate) },
          { label: "Samples", value: typeof m.total_conclusive_runs === "number" ? String(m.total_conclusive_runs) : "-" },
        ];
      }
      if ("retry_rate" in m) {
        return [
          { label: "Retry rate", value: percentShare(m.retry_rate) },
          { label: "Wasted time", value: seconds(m.total_wasted_seconds) },
        ];
      }
      return [];
    case "potential_parallelization":
      return [
        { label: "Ran sequentially", value: percentShare(m.sequential_consistency) },
        { label: "Theoretical savings", value: seconds(m.theoretical_best_case_savings_seconds) },
      ];
    default:
      return [];
  }
}
