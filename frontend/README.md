# ADPO Frontend

Vite + React + TypeScript SPA for the ADPO dashboard - connects to the
FastAPI backend in `../backend` via cookie-based sessions (see
`backend/README.md` "Authentication architecture").

## Running locally

```
npm install
npm run dev
```

Runs on `http://localhost:3000` by default - this must match the backend's
`CORS_ORIGINS` and `FRONTEND_SUCCESS_REDIRECT_URL` settings, since GitHub
OAuth redirects the browser back to this origin after login and the API
only accepts credentialed requests from an allowed CORS origin.

Set `VITE_API_BASE_URL` (in a `.env.local`, not committed) if the backend
isn't at the default `http://localhost:8000`.

## Structure

- `src/api/` - typed fetch client (`client.ts`) and response types
  (`types.ts`) mirroring `backend/app/schemas/*.py`. Keep these in sync if
  the backend schemas change.
- `src/pages/` - `Login`, `Dashboard` (connected repos + connect-new-repo),
  `RepositoryDetail` (sync/analyze actions, workflows, runs, findings).
- `src/components/` - `SeverityBadge`/`ConfidenceBadge`, `FindingCard`.

## Auth flow

Login and GitHub App installation are plain `<a href>` links to the
backend's redirect endpoints, not `fetch` calls - the OAuth dance needs the
browser itself to navigate so it can carry/receive cookies. Everything
else goes through `src/api/client.ts`, which always sends
`credentials: "include"`.

## Tests

```
npm run test
```

Vitest + React Testing Library.
