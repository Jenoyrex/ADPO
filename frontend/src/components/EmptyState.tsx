import { useState, type ReactNode } from "react";
import { GITHUB_LOGIN_URL } from "../api/client";

export function EmptyState({
  title,
  description,
  action,
  tone = "neutral",
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  tone?: "neutral" | "positive";
}) {
  return (
    <div
      className={`rounded-lg border px-6 py-10 text-center ${
        tone === "positive" ? "border-emerald-200 bg-emerald-50" : "border-dashed border-slate-300 bg-white"
      }`}
    >
      <p className={`font-medium ${tone === "positive" ? "text-emerald-800" : "text-slate-700"}`}>{title}</p>
      {description && (
        <p className={`mt-1 text-sm ${tone === "positive" ? "text-emerald-700" : "text-slate-500"}`}>
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({
  message,
  detail,
  sessionExpired = false,
  onRetry,
}: {
  message: string;
  detail?: string | null;
  sessionExpired?: boolean;
  onRetry?: () => void;
}) {
  const [showDetail, setShowDetail] = useState(false);

  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3" role="alert">
      <p className="text-sm text-red-700">{message}</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        {sessionExpired ? (
          <a
            href={GITHUB_LOGIN_URL}
            className="text-sm font-medium text-red-700 underline hover:text-red-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
          >
            Sign in again
          </a>
        ) : (
          onRetry && (
            <button
              onClick={onRetry}
              className="text-sm font-medium text-red-700 underline hover:text-red-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700"
            >
              Try again
            </button>
          )
        )}
        {detail && (
          <button
            onClick={() => setShowDetail((v) => !v)}
            className="text-xs text-red-500 underline hover:text-red-700"
          >
            {showDetail ? "Hide details" : "Show details"}
          </button>
        )}
      </div>
      {showDetail && detail && (
        <pre className="mt-2 overflow-x-auto rounded bg-red-100 p-2 text-xs text-red-800">{detail}</pre>
      )}
    </div>
  );
}
