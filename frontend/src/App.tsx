import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { api } from "./api/client";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { RepositoryDetail } from "./pages/RepositoryDetail";
import { ErrorState } from "./components/EmptyState";
import { friendlyError } from "./lib/errors";

function AppLayout() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });

  async function handleLogout() {
    await api.logout();
    queryClient.clear();
    navigate("/login");
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:shadow"
      >
        Skip to main content
      </a>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <span className="font-bold text-slate-900">ADPO</span>
          {user && (
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-600">{user.github_login}</span>
              <button
                onClick={handleLogout}
                aria-label="Log out of ADPO"
                className="rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>
      <main id="main-content" className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}

function RequireAuth() {
  const { isPending, isError, error, refetch } = useQuery({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
  });

  if (isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500" role="status">
        Loading...
      </div>
    );
  }
  if (isError) {
    const info = friendlyError(error, "session");
    if (info.isSessionExpired) {
      return <Navigate to="/login" replace />;
    }
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
        <div className="w-full max-w-sm">
          <ErrorState message={info.message} detail={info.detail} onRetry={() => refetch()} />
        </div>
      </div>
    );
  }
  return <AppLayout />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/repositories/:id" element={<RepositoryDetail />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Route>
    </Routes>
  );
}
