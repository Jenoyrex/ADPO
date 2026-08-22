import type { Severity } from "../api/types";
import { ciHealth, CI_HEALTH_STYLES } from "../lib/ciHealth";

export function CIHealthBanner({ counts }: { counts: Record<Severity, number> }) {
  const health = ciHealth(counts);
  const style = CI_HEALTH_STYLES[health.state];
  const total = counts.critical + counts.high + counts.medium + counts.low;

  return (
    <div className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${style.bg} ${style.border}`}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${style.dot}`} aria-hidden="true" />
      <div>
        <p className={`text-sm font-semibold ${style.text}`}>CI Health: {health.label}</p>
        <p className="text-xs text-slate-500">
          {total === 0
            ? "No issues found in the analyzed history."
            : `${total} finding${total === 1 ? "" : "s"} across ${counts.critical} critical, ${counts.high} high, ${counts.medium} medium, ${counts.low} low.`}
        </p>
      </div>
    </div>
  );
}
