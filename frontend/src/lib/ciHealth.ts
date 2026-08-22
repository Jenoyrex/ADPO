import type { Severity } from "../api/types";

export type CIHealthState = "healthy" | "needs-attention" | "critical";

export interface CIHealthResult {
  state: CIHealthState;
  label: string;
}

// Purely derived from real severity counts already returned by the API -
// no new data, no invented thresholds beyond "critical findings exist" /
// "any findings exist" / "no findings exist".
export function ciHealth(counts: Record<Severity, number>): CIHealthResult {
  if (counts.critical > 0) return { state: "critical", label: "Critical" };
  if (counts.high > 0 || counts.medium > 0 || counts.low > 0) {
    return { state: "needs-attention", label: "Needs Attention" };
  }
  return { state: "healthy", label: "Healthy" };
}

export const CI_HEALTH_STYLES: Record<CIHealthState, { dot: string; text: string; bg: string; border: string }> = {
  healthy: { dot: "bg-emerald-500", text: "text-emerald-800", bg: "bg-emerald-50", border: "border-emerald-200" },
  "needs-attention": { dot: "bg-amber-500", text: "text-amber-800", bg: "bg-amber-50", border: "border-amber-200" },
  critical: { dot: "bg-red-600", text: "text-red-800", bg: "bg-red-50", border: "border-red-200" },
};
