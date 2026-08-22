import { GITHUB_LOGIN_URL } from "../api/client";

export function Login() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-8 text-center shadow-sm">
        <h1 className="text-2xl font-bold text-slate-900">ADPO</h1>
        <p className="mt-2 text-sm text-slate-600">
          Deterministic, evidence-based analysis of your GitHub Actions CI/CD pipelines.
        </p>
        {/* Plain navigation, not a fetch call: the OAuth flow needs the
            browser itself to follow the redirect chain and carry cookies. */}
        <a
          href={GITHUB_LOGIN_URL}
          className="mt-6 inline-flex w-full items-center justify-center rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        >
          Sign in with GitHub
        </a>
      </div>
    </div>
  );
}
