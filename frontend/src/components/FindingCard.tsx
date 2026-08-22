import { useState } from "react";
import type { FindingOut } from "../api/types";
import { humanAnalyzerLabel, humanFindingHeadline, keyMetricTiles, sanitizeDisplayText } from "../lib/analyzers";
import { humanizeMetricKey, formatMetricValue } from "../lib/format";
import { MetricTileRow } from "./MetricTile";
import { ConfidenceBadge, SeverityBadge, SEVERITY_BORDER } from "./SeverityBadge";

function formatSavings(finding: FindingOut): string | null {
  const savings = finding.estimated_savings_range;
  if (!savings || savings.unknown || savings.low_seconds == null || savings.high_seconds == null) {
    return null;
  }
  const low = Math.round(savings.low_seconds);
  const high = Math.round(savings.high_seconds);
  return low === high ? `~${low}s per run` : `~${low}s - ${high}s per run`;
}

export function FindingCard({ finding }: { finding: FindingOut }) {
  const [showTechnical, setShowTechnical] = useState(false);
  const savings = formatSavings(finding);
  const metricEntries = Object.entries(finding.metrics ?? {});
  const tiles = keyMetricTiles(finding);

  return (
    <article
      className={`rounded-lg border border-l-4 border-slate-200 bg-white p-4 shadow-sm ${SEVERITY_BORDER[finding.severity]}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={finding.severity} />
          <ConfidenceBadge confidence={finding.confidence} />
        </div>
        <span className="text-xs text-slate-400">Analyzer: {humanAnalyzerLabel(finding.analyzer_type)}</span>
      </div>

      <h3 className="mt-2 text-base font-semibold text-slate-900">{humanFindingHeadline(finding)}</h3>
      <p className="mt-0.5 text-xs text-slate-400">{sanitizeDisplayText(finding.title)}</p>
      <p className="mt-2 text-sm text-slate-600">{sanitizeDisplayText(finding.description)}</p>

      <MetricTileRow tiles={tiles} />

      {finding.evidence.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Why ADPO flagged this</p>
          <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-slate-600">
            {finding.evidence.map((line, i) => (
              <li key={i}>{sanitizeDisplayText(line)}</li>
            ))}
          </ul>
        </div>
      )}

      {finding.estimated_savings_range?.basis && (
        <div className="mt-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Impact</p>
          <p className="mt-1 text-sm text-slate-600">{sanitizeDisplayText(finding.estimated_savings_range.basis)}</p>
        </div>
      )}

      <div className="mt-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Recommendation</p>
        <p className="mt-1 text-sm text-slate-700">{sanitizeDisplayText(finding.recommendation)}</p>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
        {savings ? (
          <p className="text-sm font-medium text-emerald-700">{savings} saved</p>
        ) : (
          <span />
        )}
        {metricEntries.length > 0 && (
          <button
            onClick={() => setShowTechnical((v) => !v)}
            aria-expanded={showTechnical}
            className="text-xs font-medium text-slate-500 underline hover:text-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
          >
            {showTechnical ? "Hide technical details" : "View technical details"}
          </button>
        )}
      </div>

      {showTechnical && metricEntries.length > 0 && (
        <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 rounded-md bg-slate-50 p-3 text-xs sm:grid-cols-2">
          {metricEntries.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-2">
              <dt className="text-slate-500">{humanizeMetricKey(key)}</dt>
              <dd className="font-medium text-slate-700">{formatMetricValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </article>
  );
}
