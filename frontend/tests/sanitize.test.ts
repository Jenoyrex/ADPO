import { describe, expect, it } from "vitest";
import { humanFindingHeadline, sanitizeDisplayText } from "../src/lib/analyzers";
import type { FindingOut } from "../src/api/types";

describe("sanitizeDisplayText", () => {
  it("strips internal test-probe wording without corrupting normal quoted text", () => {
    const input =
      "Job 'Historical regression probe (controlled, temporary, for ADPO analyzer testing)' has regressed compared to its own history";
    const result = sanitizeDisplayText(input);
    expect(result).not.toMatch(/probe|temporary|testing/i);
    expect(result).toBe("Job 'Historical regression' has regressed compared to its own history");
  });

  it("leaves ordinary quoted job names completely untouched", () => {
    const input = "Job 'build' is a significant contributor to pipeline duration";
    expect(sanitizeDisplayText(input)).toBe(input);
  });

  it("handles the dependency-install probe step naming variant", () => {
    const input = "Step 'pip install backend deps (adpo probe, temporary)' in job 'Backend tests' matched...";
    const result = sanitizeDisplayText(input);
    expect(result).not.toMatch(/probe|temporary/i);
    expect(result).toBe("Step 'pip install backend deps' in job 'Backend tests' matched...");
  });
});

function makeFinding(overrides: Partial<FindingOut> = {}): FindingOut {
  return {
    id: 1,
    analyzer_type: "historical_regression",
    title: "regressed",
    description: "",
    evidence: [],
    metrics: {},
    severity: "critical",
    confidence: "medium",
    recommendation: "",
    estimated_savings_range: null,
    ...overrides,
  };
}

describe("humanFindingHeadline", () => {
  it("gives historical_regression a plain-English headline", () => {
    expect(humanFindingHeadline(makeFinding())).toBe("CI performance regression detected");
  });

  it("distinguishes flaky from retry-waste failure_retry findings", () => {
    expect(
      humanFindingHeadline(makeFinding({ analyzer_type: "failure_retry", metrics: { classification: "flaky" } })),
    ).toBe("A job is flaky");
    expect(
      humanFindingHeadline(
        makeFinding({ analyzer_type: "failure_retry", metrics: { retried_run_count: 2 } }),
      ),
    ).toBe("Workflow retries are wasting CI time");
  });
});
