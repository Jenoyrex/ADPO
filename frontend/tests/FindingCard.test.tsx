import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { FindingCard } from "../src/components/FindingCard";
import type { FindingOut } from "../src/api/types";

function makeFinding(overrides: Partial<FindingOut> = {}): FindingOut {
  return {
    id: 1,
    analyzer_type: "slow_job",
    title: "Job 'build' is a significant contributor to pipeline duration",
    description: "The job takes a long time.",
    evidence: ["median duration 200s", "share of workflow 80%"],
    metrics: {},
    severity: "high",
    confidence: "medium",
    recommendation: "Investigate the slowest steps.",
    estimated_savings_range: { low_seconds: 30, high_seconds: 90, basis: "observed gap", unknown: false },
    ...overrides,
  };
}

describe("FindingCard", () => {
  it("renders title, severity, confidence, evidence, and recommendation", () => {
    render(<FindingCard finding={makeFinding()} />);

    expect(screen.getByText(makeFinding().title)).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("medium confidence")).toBeInTheDocument();
    expect(screen.getByText("median duration 200s")).toBeInTheDocument();
    expect(screen.getByText(/Investigate the slowest steps\./)).toBeInTheDocument();
  });

  it("shows a formatted savings range when known", () => {
    render(<FindingCard finding={makeFinding()} />);
    expect(screen.getByText(/~30s - 90s per run saved/)).toBeInTheDocument();
  });

  it("omits the savings line when the range is unknown", () => {
    render(
      <FindingCard
        finding={makeFinding({
          estimated_savings_range: { low_seconds: null, high_seconds: null, basis: null, unknown: true },
        })}
      />,
    );
    expect(screen.queryByText(/saved/)).not.toBeInTheDocument();
  });

  it("shows a human-readable analyzer label, not the raw analyzer_type", () => {
    render(<FindingCard finding={makeFinding({ analyzer_type: "historical_regression" })} />);
    expect(screen.getByText("Analyzer: Historical Regression")).toBeInTheDocument();
    expect(screen.queryByText(/HISTORICAL_REGRESSION/)).not.toBeInTheDocument();
  });

  it("shows the savings basis under an Impact section when present", () => {
    render(<FindingCard finding={makeFinding()} />);
    expect(screen.getByText("Impact")).toBeInTheDocument();
    expect(screen.getByText("observed gap")).toBeInTheDocument();
  });

  it("keeps technical metrics collapsed until the user asks for them", async () => {
    const user = userEvent.setup();
    render(<FindingCard finding={makeFinding({ metrics: { median_duration_seconds: 42.4 } })} />);

    expect(screen.queryByText("Median Duration Seconds")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "View technical details" }));
    expect(screen.getByText("Median Duration Seconds")).toBeInTheDocument();
    expect(screen.getByText("42.400")).toBeInTheDocument();
  });

  it("shows a friendly headline for a regression finding, with Baseline/Current/Change metric tiles from real metrics", () => {
    render(
      <FindingCard
        finding={makeFinding({
          analyzer_type: "historical_regression",
          title: "Job 'Historical regression probe (controlled, temporary, for ADPO analyzer testing)' has regressed",
          metrics: { baseline_median_seconds: 13, recent_median_seconds: 42, pct_increase: 2.231 },
        })}
      />,
    );
    expect(screen.getByText("CI performance regression detected")).toBeInTheDocument();
    expect(screen.queryByText(/probe|temporary|testing/i)).not.toBeInTheDocument();
    expect(screen.getByText("Baseline")).toBeInTheDocument();
    expect(screen.getByText("13s")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("42s")).toBeInTheDocument();
    expect(screen.getByText("Change")).toBeInTheDocument();
    expect(screen.getByText("+223%")).toBeInTheDocument();
  });
});
