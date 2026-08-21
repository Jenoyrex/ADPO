# ADPO Backend API (Phase 2)

Base URL: `http://localhost:8000`. Interactive docs: `http://localhost:8000/docs`.

All `/api/v1/*` endpoints except `GET /api/v1/auth/github/login` and
`GET /api/v1/auth/github/callback` require an authenticated session (cookie
`adpo_session`, set after completing the GitHub login flow). Unauthenticated
requests get `401`.

## Health

### `GET /health`
No auth required. Returns app + database connectivity status.

```json
{ "status": "ok", "database": "ok" }
```

## Authentication

Two independent flows - see `backend/README.md` "Authentication architecture"
for why they're separate.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/auth/github/login` | Starts the GitHub OAuth (user-to-server) login flow. Redirects to GitHub. |
| `GET /api/v1/auth/github/callback` | OAuth callback. Creates/updates the `User`, sets the session cookie, redirects to the frontend. |
| `GET /api/v1/auth/github/install` | Sends a signed-in user to install/configure the GitHub App on an account or org. |
| `GET /api/v1/auth/github/installation/callback` | GitHub App installation callback. Links the installation to the signed-in user. |
| `GET /api/v1/auth/me` | Returns the current signed-in user. |
| `POST /api/v1/auth/logout` | Clears the session cookie. |

## GitHub (live)

### `GET /api/v1/github/repositories`
Lists repositories reachable through the current user's connected GitHub
App installation(s), fetched live from the GitHub API (not the DB). Each
entry is flagged `already_connected` if it has already been imported into
ADPO via `POST /api/v1/repositories`.

## Repositories (persisted)

### `GET /api/v1/repositories`
Lists repositories the current user has connected.

### `POST /api/v1/repositories`
Connects a repository the user has GitHub App access to.

Request:
```json
{ "github_repo_id": 123456789 }
```

Returns `201` with the created `Repository`, or `403` if the repo is not
reachable through any of the user's installations.

### `POST /api/v1/repositories/{repository_id}/sync`
Synchronizes workflows, runs, jobs, and steps from GitHub into the
database. Idempotent - safe to call repeatedly; safe against partial
failures (a single run's job-fetch failure does not abort the whole sync).
Synchronous (MVP): the request blocks until the sync completes.

Response:
```json
{
  "repository_id": 1,
  "status": "completed",
  "workflows_synced": 2,
  "runs_synced": 40,
  "jobs_synced": 120,
  "steps_synced": 640,
  "started_at": "...",
  "completed_at": "..."
}
```

### `GET /api/v1/repositories/{repository_id}/workflows`
Lists synced workflows for the repository.

### `GET /api/v1/repositories/{repository_id}/runs`
Lists synced workflow runs. Query params: `workflow_id` (filter),
`limit` (default 50, max 200), `offset`.

### `POST /api/v1/repositories/{repository_id}/analyze`
Runs the locked `adpo.AnalysisEngine` over every synced run for the
repository (spanning all its workflows - the engine groups per-workflow
internally) and persists the results as an `Analysis` + its `Finding`s.
Synchronous.

### `GET /api/v1/repositories/{repository_id}/findings`
Returns findings from the most recent completed analysis, or from a
specific one via `?analysis_id=`. Returns `[]` if no analysis has run yet.

Finding shape:
```json
{
  "id": 1,
  "analyzer_type": "slow_job",
  "title": "...",
  "description": "...",
  "evidence": ["..."],
  "metrics": { "...": "..." },
  "severity": "high",
  "confidence": "medium",
  "recommendation": "...",
  "estimated_savings_range": {
    "low_seconds": 120.0,
    "high_seconds": 300.0,
    "basis": "...",
    "unknown": false
  }
}
```

## Authorization model

Every `/repositories/{repository_id}/...` endpoint checks that the
repository belongs to a GitHub App installation the current session's user
connected; otherwise it returns `404` (not `403`, to avoid confirming
another user's repository exists).
