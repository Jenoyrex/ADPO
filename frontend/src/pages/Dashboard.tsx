import { useMemo } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, GITHUB_INSTALL_URL } from "../api/client";
import type { FindingOut, Severity } from "../api/types";
import { EmptyState, ErrorState } from "../components/EmptyState";
import { SkeletonList } from "../components/Skeleton";
import { StatCard } from "../components/StatCard";
import { SEVERITY_ORDER, SEVERITY_RANK } from "../components/SeverityBadge";
import { CIHealthBanner } from "../components/CIHealthBanner";
import { FindingPreview } from "../components/FindingPreview";
import { friendlyError } from "../lib/errors";

const TOP_FINDINGS_LIMIT = 5;

const RECENT_RUNS_LIMIT = 100;

function formatLastSynced(value: string | null): string {
  if (!value) return "Never synced";
  return `Synced ${new Date(value).toLocaleString()}`;
}

function totalKnownSavingsSeconds(findings: FindingOut[]): number | null {
  const known = findings
    .map((f) => f.estimated_savings_range)
    .filter((s) => s && !s.unknown && s.low_seconds != null);
  if (known.length === 0) return null;
  return known.reduce((sum, s) => sum + (s!.low_seconds ?? 0), 0);
}

export function Dashboard() {
  const queryClient = useQueryClient();

  const repositories = useQuery({ queryKey: ["repositories"], queryFn: api.repositories });
  const githubRepositories = useQuery({
    queryKey: ["githubRepositories"],
    queryFn: api.githubRepositories,
  });

  const repoList = repositories.data ?? [];

  const findingsQueries = useQueries({
    queries: repoList.map((repo) => ({
      queryKey: ["findings", repo.id],
      queryFn: () => api.findings(repo.id),
    })),
  });
  const runsQueries = useQueries({
    queries: repoList.map((repo) => ({
      queryKey: ["runs", repo.id, RECENT_RUNS_LIMIT],
      queryFn: () => api.runs(repo.id, { limit: RECENT_RUNS_LIMIT }),
    })),
  });

  const findingsLoaded = repoList.length > 0 && findingsQueries.every((q) => q.isSuccess);
  const findingsSomeError = findingsQueries.some((q) => q.isError);
  const findingsUpdatedAtFingerprint = findingsQueries.map((q) => q.dataUpdatedAt).join(",");
  const allFindings = useMemo(
    () => findingsQueries.flatMap((q) => q.data ?? []),
    [findingsUpdatedAtFingerprint], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const totalRuns = runsQueries.reduce((sum, q) => sum + (q.data?.length ?? 0), 0);

  const severityCounts = useMemo(() => {
    const counts: Record<Severity, number> = { critical: 0, high: 0, medium: 0, low: 0 };
    for (const f of allFindings) counts[f.severity]++;
    return counts;
  }, [allFindings]);

  const topFindings = useMemo(() => {
    const withRepo = repoList.flatMap((repo, idx) =>
      (findingsQueries[idx]?.data ?? []).map((finding) => ({ finding, repoId: repo.id, repoFullName: repo.full_name })),
    );
    return withRepo
      .sort((a, b) => SEVERITY_RANK[a.finding.severity] - SEVERITY_RANK[b.finding.severity])
      .slice(0, TOP_FINDINGS_LIMIT);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoList, findingsUpdatedAtFingerprint]);

  const connect = useMutation({
    mutationFn: api.connectRepository,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["repositories"] });
      queryClient.invalidateQueries({ queryKey: ["githubRepositories"] });
    },
  });

  const connectableRepos = githubRepositories.data?.filter((repo) => !repo.already_connected) ?? [];
  const totalFindings = allFindings.length;
  const totalSavings = totalKnownSavingsSeconds(allFindings);
  const isHealthy = findingsLoaded && !findingsSomeError && totalFindings === 0;

  function findingsSummaryFor(repoId: number): { count: number; critical: number } | null {
    const idx = repoList.findIndex((r) => r.id === repoId);
    const query = findingsQueries[idx];
    if (!query?.isSuccess || !query.data) return null;
    return {
      count: query.data.length,
      critical: query.data.filter((f) => f.severity === "critical" || f.severity === "high").length,
    };
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">ADPO</h1>
        <p className="text-sm text-slate-500">CI Intelligence for GitHub Actions</p>
      </div>

      <div>
        <h2 className="text-base font-semibold text-slate-900">Your CI health at a glance</h2>
        <p className="text-sm text-slate-500">
          ADPO watches your GitHub Actions history, finds real CI problems, and explains why they matter.
        </p>
      </div>

      <section aria-label="Overview">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Repositories" value={repositories.data?.length ?? "-"} />
          <StatCard label="Workflow runs" value={repositories.isPending ? "-" : totalRuns} />
          <StatCard
            label="Findings"
            value={findingsLoaded ? totalFindings : "-"}
            accent={findingsLoaded && totalFindings > 0 ? "high" : "default"}
          />
          <StatCard
            label="Potential time savings"
            value={totalSavings != null ? `~${Math.round(totalSavings)}s/run` : "-"}
            accent="positive"
          />
        </div>
      </section>

      {findingsLoaded && !isHealthy && (
        <section aria-label="CI health">
          <CIHealthBanner counts={severityCounts} />
        </section>
      )}

      <section aria-label="What needs attention">
        <h2 className="text-lg font-semibold text-slate-900">What needs attention?</h2>
        {repoList.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">Connect a repository to see CI health here.</p>
        ) : !findingsLoaded ? (
          <div className="mt-3">
            <SkeletonList rows={1} />
          </div>
        ) : isHealthy ? (
          <div className="mt-3">
            <EmptyState
              tone="positive"
              title="Your CI looks healthy"
              description="ADPO analyzed the synced history for your connected repositories and didn't find any issues."
            />
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {SEVERITY_ORDER.map((severity) => (
                <StatCard
                  key={severity}
                  label={severity}
                  value={`${severityCounts[severity]} ${severityCounts[severity] === 1 ? "finding" : "findings"}`}
                  accent={severity === "low" ? "default" : severity}
                />
              ))}
            </div>
            <div className="mt-3 space-y-2">
              {topFindings.map(({ finding, repoId, repoFullName }) => (
                <FindingPreview key={finding.id} finding={finding} repoId={repoId} repoFullName={repoFullName} />
              ))}
            </div>
          </>
        )}
        {findingsSomeError && (
          <p className="mt-2 text-xs text-slate-400">
            Some repositories' findings couldn't be loaded, so these totals may be incomplete.
          </p>
        )}
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Your repositories</h2>
        {repositories.isPending && (
          <div className="mt-3">
            <SkeletonList rows={2} />
          </div>
        )}
        {repositories.isError &&
          (() => {
            const e = friendlyError(repositories.error, "repositories");
            return (
              <div className="mt-3">
                <ErrorState
                  message={e.message}
                  detail={e.detail}
                  sessionExpired={e.isSessionExpired}
                  onRetry={() => repositories.refetch()}
                />
              </div>
            );
          })()}
        {repositories.data && repositories.data.length === 0 && (
          <div className="mt-3">
            <EmptyState
              title="No repositories connected yet."
              description="Connect a GitHub repository to start analyzing CI performance."
            />
          </div>
        )}
        <ul className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {repositories.data?.map((repo) => {
            const summary = findingsSummaryFor(repo.id);
            return (
              <li key={repo.id}>
                <Link
                  to={`/repositories/${repo.id}`}
                  className="flex h-full flex-col justify-between rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm transition hover:border-slate-400 hover:shadow focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
                >
                  <span className="font-medium text-slate-900">{repo.full_name}</span>
                  <div className="mt-2 flex items-center justify-between">
                    <span className={`text-xs ${repo.last_synced_at ? "text-slate-500" : "text-amber-600"}`}>
                      {formatLastSynced(repo.last_synced_at)}
                    </span>
                    {summary && (
                      <span
                        className={`text-xs font-medium ${
                          summary.critical > 0
                            ? "text-red-600"
                            : summary.count > 0
                              ? "text-amber-600"
                              : "text-emerald-600"
                        }`}
                      >
                        {summary.critical > 0
                          ? `${summary.critical} critical/high`
                          : summary.count > 0
                            ? `${summary.count} findings`
                            : "No issues"}
                      </span>
                    )}
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </section>

      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900">Connect a repository</h2>
          <a
            href={GITHUB_INSTALL_URL}
            className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
          >
            Manage GitHub App installation
          </a>
        </div>

        {githubRepositories.isPending && (
          <div className="mt-3">
            <SkeletonList rows={2} />
          </div>
        )}
        {githubRepositories.isError &&
          (() => {
            const e = friendlyError(githubRepositories.error, "GitHub repository list");
            return (
              <div className="mt-3">
                <ErrorState
                  message={e.message}
                  detail={e.detail}
                  sessionExpired={e.isSessionExpired}
                  onRetry={() => githubRepositories.refetch()}
                />
              </div>
            );
          })()}
        {githubRepositories.data && connectableRepos.length === 0 && (
          <div className="mt-3">
            <EmptyState
              title="No repositories available to connect"
              description="Install the GitHub App on an account or org, or every reachable repository is already connected."
            />
          </div>
        )}
        <ul className="mt-3 space-y-2">
          {connectableRepos.map((repo) => (
            <li
              key={repo.github_repo_id}
              className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm"
            >
              <span className="text-slate-900">{repo.full_name}</span>
              <button
                onClick={() => connect.mutate(repo.github_repo_id)}
                disabled={connect.isPending}
                className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
              >
                {connect.isPending ? "Connecting..." : "Connect"}
              </button>
            </li>
          ))}
        </ul>
        {connect.isError &&
          (() => {
            const e = friendlyError(connect.error, "repository");
            return (
              <div className="mt-2">
                <ErrorState message={e.message} detail={e.detail} sessionExpired={e.isSessionExpired} />
              </div>
            );
          })()}
      </section>
    </div>
  );
}
