import { describe, expect, it } from "vitest";
import { ciHealth } from "../src/lib/ciHealth";

describe("ciHealth", () => {
  it("is healthy when there are no findings at all", () => {
    expect(ciHealth({ critical: 0, high: 0, medium: 0, low: 0 }).state).toBe("healthy");
  });

  it("needs attention when only lower-severity findings exist", () => {
    expect(ciHealth({ critical: 0, high: 0, medium: 1, low: 2 }).state).toBe("needs-attention");
    expect(ciHealth({ critical: 0, high: 2, medium: 0, low: 0 }).state).toBe("needs-attention");
  });

  it("is critical whenever at least one critical finding exists, regardless of the rest", () => {
    expect(ciHealth({ critical: 1, high: 0, medium: 0, low: 0 }).state).toBe("critical");
    expect(ciHealth({ critical: 1, high: 5, medium: 5, low: 5 }).state).toBe("critical");
  });
});
