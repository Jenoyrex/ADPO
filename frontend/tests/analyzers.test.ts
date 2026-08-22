import { describe, expect, it } from "vitest";
import { humanAnalyzerLabel } from "../src/lib/analyzers";

describe("humanAnalyzerLabel", () => {
  it("maps every known backend analyzer_type to a human label", () => {
    expect(humanAnalyzerLabel("slow_job")).toBe("Slow Job");
    expect(humanAnalyzerLabel("slow_step")).toBe("Slow Step");
    expect(humanAnalyzerLabel("historical_regression")).toBe("Historical Regression");
    expect(humanAnalyzerLabel("dependency_install")).toBe("Dependency Installation");
    expect(humanAnalyzerLabel("failure_retry")).toBe("Failure & Retry");
    expect(humanAnalyzerLabel("potential_parallelization")).toBe("Parallelization");
  });

  it("falls back to a title-cased version for an unmapped analyzer_type", () => {
    expect(humanAnalyzerLabel("some_new_analyzer")).toBe("Some New Analyzer");
  });
});
