import type { Confidence, Severity } from "../api/types";

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];

export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const SEVERITY_STYLES: Record<Severity, string> = {
  low: "bg-slate-100 text-slate-600",
  medium: "bg-amber-100 text-amber-800",
  high: "bg-orange-100 text-orange-800",
  critical: "bg-red-100 text-red-700",
};

const SEVERITY_DOT: Record<Severity, string> = {
  low: "bg-slate-400",
  medium: "bg-amber-500",
  high: "bg-orange-500",
  critical: "bg-red-600",
};

export const SEVERITY_BORDER: Record<Severity, string> = {
  low: "border-l-slate-300",
  medium: "border-l-amber-400",
  high: "border-l-orange-500",
  critical: "border-l-red-600",
};

const CONFIDENCE_STYLES: Record<Confidence, string> = {
  low: "bg-slate-50 text-slate-500",
  medium: "bg-blue-50 text-blue-700",
  high: "bg-blue-100 text-blue-800",
};

function badgeClasses(extra: string): string {
  return `inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${extra}`;
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={badgeClasses(SEVERITY_STYLES[severity])}>
      <span className={`h-1.5 w-1.5 rounded-full ${SEVERITY_DOT[severity]}`} />
      {severity}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  return (
    <span className={badgeClasses(CONFIDENCE_STYLES[confidence])}>{confidence} confidence</span>
  );
}
