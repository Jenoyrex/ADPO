import { Link } from "react-router-dom";
import type { FindingOut } from "../api/types";
import { humanAnalyzerLabel, humanFindingHeadline } from "../lib/analyzers";
import { ConfidenceBadge, SeverityBadge } from "./SeverityBadge";

function formatSavings(finding: FindingOut): string | null {
  const savings = finding.estimated_savings_range;
  if (!savings || savings.unknown || savings.low_seconds == null || savings.high_seconds == null) return null;
  const low = Math.round(savings.low_seconds);
  const high = Math.round(savings.high_seconds);
  return low === high ? `~${low}s/run` : `~${low}-${high}s/run`;
}

export function FindingPreview({ finding, repoId, repoFullName }: { finding: FindingOut; repoId: number; repoFullName: string }) {
  const savings = formatSavings(finding);

  return (
    <Link
      to={`/repositories/${repoId}`}
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm transition hover:border-slate-400 hover:shadow focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <span className="text-xs text-slate-400">{humanAnalyzerLabel(finding.analyzer_type)}</span>
        </div>
        <p className="mt-1 truncate text-sm font-medium text-slate-900">{humanFindingHeadline(finding)}</p>
        <p className="text-xs text-slate-400">{repoFullName}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {savings && <span className="text-xs font-medium text-emerald-700">{savings}</span>}
        <ConfidenceBadge confidence={finding.confidence} />
      </div>
    </Link>
  );
}
