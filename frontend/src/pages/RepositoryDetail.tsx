import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { FindingOut, Severity } from "../api/types";
import { FindingCard } from "../components/FindingCard";
import { SEVERITY_ORDER, SEVERITY_RANK } from "../components/SeverityBadge";
import { StatCard } from "../components/StatCard";
import { SkeletonList, Skeleton } from "../components/Skeleton";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { Spinner } from "../components/Spinner";
import { humanAnalyzerLabel } from "../lib/analyzers";
import { formatDateTime } from "../lib/format";
import { friendlyError } from "../lib/errors";
import { useCyclingMessage } from "../lib/useCyclingMessage";
import { CIHealthBanner } from "../components/CIHealthBanner";

const SYNC_MESSAGES = ["Fetching latest GitHub Actions data...", "Updating workflow history..."];
const ANALYZE_MESSAGES = ["Analyzing CI history...", "Running 6 analyzers..."];
const RUN_HISTORY_LIMIT = 20;
const RUN_STATS_LIMIT = 100;

function statusLabel(conclusion: string | null, status: string): string {
  return conclusion ?? status;
}

function statusColor(conclusion: string | null, status: string): string {
  const value = conclusion ?? status;
  if (value === "success") return "text-emerald-700";
  if (value === "failure") return "text-red-600";
  if (value === "in_progress" || value === "queued") return "text-amber-600";
  return "text-slate-500";
}

function statusIcon(conclusion: string | null, status: string): string {
  const value = conclusion ?? status;
  if (value === "success") return "✓"; // check mark
  if (value === "failure") return "✕"; // cross mark
  return "●"; // dot, for queued/in_progress/other
}

function totalKnownSavingsSeconds(findings: FindingOut[]): number | null {
  const known = findings
    .map((f) => f.estimated_savings_range)
    .filter((s) => s && !s.unknown && s.low_seconds != null);
  if (known.length === 0) return null;
  return known.reduce((sum, s) => sum + (s!.low_seconds ?? 0), 0);
}

export function RepositoryDetail() {
  const { id } = useParams<{ id: string }>();
  const repositoryId = Number(id);
  const queryClient = useQueryClient();
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [analyzerFilter, setAnalyzerFilter] = useState<string | "all">("all");
  const [justAnalyzed, setJustAnalyzed] = useState(false);

  const repositories = useQuery({ queryKey: ["repositories"], queryFn: api.repositories });
  const repository = repositories.data?.find((r) => r.id === repositoryId);

  const workflows = useQuery({
    queryKey: ["workflows", repositoryId],
    queryFn: () => api.workflows(repositoryId),
  });
  const runs = useQuery({
    queryKey: ["runs", repositoryId],
    queryFn: () => api.runs(repositoryId, { limit: RUN_HISTORY_LIMIT }),
  });
  // Separate, larger-limit query purely for the "recent runs" stat, so it
  // reports the same real count as the Dashboard's aggregate (which also
  // fetches at this limit) instead of being silently capped at the smaller
  // limit used for the readable run-history table below.
  const runsForStats = useQuery({
    queryKey: ["runs", repositoryId, RUN_STATS_LIMIT],
    queryFn: () => api.runs(repositoryId, { limit: RUN_STATS_LIMIT }),
  });

  // Runs sharing a run_number with more than one attempt in the fetched
  // history - computed client-side from already-fetched data, no new
  // requests - used to flag retried runs in the table below.
  const retriedRunNumbers = useMemo(() => {
    const counts = new Map<number, number>();
    for (const run of runs.data ?? []) {
      counts.set(run.run_number, (counts.get(run.run_number) ?? 0) + 1);
    }
    return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([runNumber]) => runNumber));
  }, [runs.data]);
  const findings = useQuery({
    queryKey: ["findings", repositoryId],
    queryFn: () => api.findings(repositoryId),
  });

  const severityCounts = useMemo(() => {
    const counts: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const f of findings.data ?? []) counts[f.severity]++;
    return counts;
  }, [findings.data]);

  const analyzerTypes = useMemo(
    () => Array.from(new Set((findings.data ?? []).map((f) => f.analyzer_type))),
    [findings.data],
  );

  const sortedFindings = useMemo(() => {
    return [...(findings.data ?? [])].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  }, [findings.data]);

  const visibleFindings = sortedFindings
    .filter((f) => severityFilter === "all" || f.severity === severityFilter)
    .filter((f) => analyzerFilter === "all" || f.analyzer_type === analyzerFilter);

  const totalSavings = totalKnownSavingsSeconds(findings.data ?? []);

  function invalidateAfterSync() {
    queryClient.invalidateQueries({ queryKey: ["repositories"] });
    queryClient.invalidateQueries({ queryKey: ["workflows", repositoryId] });
    queryClient.invalidateQueries({ queryKey: ["runs", repositoryId] });
  }

  const sync = useMutation({
    mutationFn: () => api.sync(repositoryId),
    onSuccess: invalidateAfterSync,
  });
  const analyze = useMutation({
    mutationFn: () => api.analyze(repositoryId),
    onSuccess: () => {
      setJustAnalyzed(true);
      queryClient.invalidateQueries({ queryKey: ["findings", repositoryId] });
    },
  });

  const syncMessage = useCyclingMessage(SYNC_MESSAGES, sync.isPending);
  const analyzeMessage = useCyclingMessage(ANALYZE_MESSAGES, analyze.isPending);

  const syncErrorInfo = sync.error ? friendlyError(sync.error, "repository") : null;
  const analyzeErrorInfo = analyze.error ? friendlyError(analyze.error, "repository") : null;

  return (
    <div className="space-y-8">
      <div>
        <Link
          to="/dashboard"
          className="text-sm text-slate-500 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
        >
          &larr; Back to dashboard
        </Link>
        <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-bold text-slate-900">
              {repository?.full_name ?? `Repository ${repositoryId}`}
            </h1>
            {repository?.html_url && (
              <a
                href={repository.html_url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-slate-500 hover:underline"
              >
                View on GitHub &rarr;
              </a>
            )}
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              onClick={() => sync.mutate()}
              disabled={sync.isPending || analyze.isPending}
              aria-busy={sync.isPending}
              className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
            >
              {sync.isPending && <Spinner className="h-4 w-4" />}
              {sync.isPending ? "Syncing..." : "Sync now"}
            </button>
            <button
              onClick={() => analyze.mutate()}
              disabled={analyze.isPending || sync.isPending}
              aria-busy={analyze.isPending}
              className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
            >
              {analyze.isPending && <Spinner className="h-4 w-4" />}
              {analyze.isPending ? "Analyzing..." : "Run analysis"}
            </button>
          </div>
        </div>

        <div aria-live="polite" className="mt-2 space-y-2">
          {sync.isPending && <p className="text-sm text-slate-500">{syncMessage}</p>}
          {sync.isSuccess && !sync.isPending && (
            <p className="text-sm text-emerald-700">
              Sync complete - {sync.data.workflows_synced} workflow(s), {sync.data.runs_synced} run(s),{" "}
              {sync.data.jobs_synced} job(s), {sync.data.steps_synced} step(s).
            </p>
          )}
          {syncErrorInfo && (
            <ErrorState
              message={syncErrorInfo.message}
              detail={syncErrorInfo.detail}
              sessionExpired={syncErrorInfo.isSessionExpired}
              onRetry={() => sync.mutate()}
            />
          )}

          {analyze.isPending && <p className="text-sm text-slate-500">{analyzeMessage}</p>}
          {analyze.isSuccess && !analyze.isPending && (
            <p className="text-sm text-emerald-700">
              Analysis complete - {analyze.data.runs_analyzed_count} run(s) analyzed,{" "}
              {analyze.data.findings.length} finding(s).
            </p>
          )}
          {analyzeErrorInfo && (
            <ErrorState
              message={analyzeErrorInfo.message}
              detail={analyzeErrorInfo.detail}
              sessionExpired={analyzeErrorInfo.isSessionExpired}
              onRetry={() => analyze.mutate()}
            />
          )}
        </div>
      </div>

      <section aria-label="CI activity overview">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">CI activity</h2>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard label="Workflows" value={workflows.data?.length ?? "-"} />
          <StatCard label="Recent runs" value={runsForStats.data?.length ?? "-"} />
          <StatCard label="Last synced" value={formatDateTime(repository?.last_synced_at ?? null)} />
        </div>
      </section>

      {findings.data && findings.data.length > 0 && (
        <>
          <section aria-label="CI health">
            <CIHealthBanner counts={severityCounts} />
          </section>
          <section aria-label="Findings summary">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatCard label="Critical" value={severityCounts.critical} accent="critical" />
              <StatCard label="High" value={severityCounts.high} accent="high" />
              <StatCard label="Medium" value={severityCounts.medium} accent="medium" />
              <StatCard
                label="Est. savings/run"
                value={totalSavings != null ? `~${Math.round(totalSavings)}s` : "-"}
                accent="positive"
              />
            </div>
          </section>
        </>
      )}

      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900">
            Findings{findings.data && findings.data.length > 0 ? ` - ${findings.data.length} issue(s) detected` : ""}
          </h2>
        </div>

        {findings.data && findings.data.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by severity">
              {(["all", ...SEVERITY_ORDER] as const).map((option) => (
                <button
                  key={option}
                  onClick={() => setSeverityFilter(option)}
                  aria-pressed={severityFilter === option}
                  className={`rounded-full px-3 py-1 text-xs font-medium capitalize focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500 ${
                    severityFilter === option
                      ? "bg-slate-900 text-white"
                      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {option}
                  {option !== "all" ? ` (${severityCounts[option]})` : ` (${findings.data.length})`}
                </button>
              ))}
            </div>
            {analyzerTypes.length > 1 && (
              <select
                value={analyzerFilter}
                onChange={(e) => setAnalyzerFilter(e.target.value)}
                aria-label="Filter by analyzer"
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
              >
                <option value="all">All analyzers</option>
                {analyzerTypes.map((type) => (
                  <option key={type} value={type}>
                    {humanAnalyzerLabel(type)}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {findings.isPending && (
          <div className="mt-3">
            <SkeletonList rows={3} />
          </div>
        )}
        {findings.isError &&
          (() => {
            const e = friendlyError(findings.error, "findings");
            return (
              <div className="mt-3">
                <ErrorState
                  message={e.message}
                  detail={e.detail}
                  sessionExpired={e.isSessionExpired}
                  onRetry={() => findings.refetch()}
                />
              </div>
            );
          })()}
        {findings.data && findings.data.length === 0 && justAnalyzed && (
          <div className="mt-3">
            <EmptyState
              tone="positive"
              title="No issues detected."
              description="ADPO didn't identify any problems in the analyzed history."
            />
          </div>
        )}
        {findings.data && findings.data.length === 0 && !justAnalyzed && (
          <div className="mt-3">
            <EmptyState
              title="No findings yet"
              description="Sync the repository to pull in real GitHub Actions history, then run analysis to check for issues."
              action={
                <button
                  onClick={() => sync.mutate()}
                  disabled={sync.isPending}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
                >
                  {sync.isPending ? "Syncing..." : "Sync now"}
                </button>
              }
            />
          </div>
        )}
        {visibleFindings.length > 0 && (
          <div className="mt-3 space-y-3">
            {visibleFindings.map((finding) => (
              <FindingCard key={finding.id} finding={finding} />
            ))}
          </div>
        )}
        {findings.data && findings.data.length > 0 && visibleFindings.length === 0 && (
          <div className="mt-3">
            <EmptyState title="No matching findings" description="Try a different filter." />
          </div>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Workflows</h2>
        {workflows.isPending && (
          <div className="mt-3">
            <Skeleton className="h-6 w-64" />
          </div>
        )}
        {workflows.isError &&
          (() => {
            const e = friendlyError(workflows.error, "workflows");
            return (
              <div className="mt-3">
                <ErrorState
                  message={e.message}
                  detail={e.detail}
                  sessionExpired={e.isSessionExpired}
                  onRetry={() => workflows.refetch()}
                />
              </div>
            );
          })()}
        {workflows.data && workflows.data.length === 0 && (
          <div className="mt-3">
            <EmptyState
              title="No workflow history available yet."
              description="Run a GitHub Actions workflow and sync again."
            />
          </div>
        )}
        <ul className="mt-3 space-y-1">
          {workflows.data?.map((workflow) => (
            <li key={workflow.id} className="text-sm text-slate-700">
              {workflow.name} <span className="text-slate-400">({workflow.path})</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900">Recent workflow activity</h2>
          {runs.data && runs.data.length > 0 && (
            <span className="text-xs text-slate-400">Showing the most recent {runs.data.length} run(s)</span>
          )}
        </div>
        {runs.isPending && (
          <div className="mt-3">
            <SkeletonList rows={4} />
          </div>
        )}
        {runs.isError &&
          (() => {
            const e = friendlyError(runs.error, "runs");
            return (
              <div className="mt-3">
                <ErrorState
                  message={e.message}
                  detail={e.detail}
                  sessionExpired={e.isSessionExpired}
                  onRetry={() => runs.refetch()}
                />
              </div>
            );
          })()}
        {runs.data && runs.data.length === 0 && (
          <div className="mt-3">
            <EmptyState
              title="No workflow history available yet."
              description="Run a GitHub Actions workflow and sync again."
            />
          </div>
        )}
        {runs.data && runs.data.length > 0 && (
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <caption className="sr-only">Recent GitHub Actions workflow runs</caption>
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th scope="col" className="whitespace-nowrap px-3 py-2">
                    Run
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2">
                    Branch
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2">
                    Event
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2">
                    Status
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2">
                    Created
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.data.map((run) => (
                  <tr key={run.id} className="border-b border-slate-100 last:border-0">
                    <td className="whitespace-nowrap px-3 py-2">
                      #{run.run_number}
                      {run.run_attempt > 1 && (
                        <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                          attempt {run.run_attempt}
                        </span>
                      )}
                      {run.run_attempt === 1 && retriedRunNumbers.has(run.run_number) && (
                        <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800">
                          retried
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">{run.branch}</td>
                    <td className="whitespace-nowrap px-3 py-2">{run.event}</td>
                    <td className={`whitespace-nowrap px-3 py-2 font-medium ${statusColor(run.conclusion, run.status)}`}>
                      <span aria-hidden="true">{statusIcon(run.conclusion, run.status)}</span>{" "}
                      {statusLabel(run.conclusion, run.status)}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">{formatDateTime(run.run_created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
