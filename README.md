# ADPO

Deterministic CI intelligence for GitHub Actions — connects to your repositories via a GitHub App, syncs workflow run history, and runs an evidence-based analysis engine to surface concrete, explainable CI problems (no LLM involved in the analysis itself).

## Overview

CI pipelines quietly waste time and money: slow jobs that used to be fast, dependency-install steps that dominate runtime, flaky tests masked by automatic retries, workflows that could run steps in parallel but don't. Spotting these usually means manually digging through Actions run history.

ADPO automates that. It connects to a repository's GitHub Actions history through a GitHub App, stores the run/job/step data, and runs it through a deterministic analysis engine — six independent analyzers built on plain statistics (medians, percentiles, variance, trend slopes), not a language model. Every finding cites the actual numbers and run IDs behind it, states a severity and a confidence level (these are independent axes — a finding can be high-impact but low-confidence on a small sample), and never invents a savings estimate it can't derive from observed data.

It exists as a full-stack, three-package portfolio project: a pure-Python analysis engine with no runtime dependencies, a FastAPI backend that connects that engine to live GitHub data, and a React dashboard — each independently testable and deployed.

## How It Works

```
GitHub Repository
      |
GitHub Actions (workflow runs, jobs, steps)
      |
GitHub App installation token  ---->  ADPO backend (FastAPI)
      |                                     |
      |                              sync: fetch + normalize + upsert
      |                                     |
      |                              PostgreSQL (workflows, runs, jobs, steps)
      |                                     |
      |                              analyze: adpo.AnalysisEngine
      |                                     |
      |                         6 deterministic analyzers (stats over stored runs)
      |                                     |
      |                              Findings (severity, confidence, evidence,
      |                               estimated savings) persisted to Postgres
      |                                     |
      +-------------------------->  Dashboard (React)
```

Sync and analysis are triggered explicitly (`POST /repositories/{id}/sync`, then `POST /repositories/{id}/analyze}`) rather than running on a background schedule or webhook — see [Roadmap](#roadmap).

## Key Features

- **GitHub App integration** (not a classic OAuth App) — installs with fine-grained, read-only `Actions` + `Metadata` permissions instead of the OAuth App's all-or-nothing `repo` scope.
- **Six deterministic analyzers**, each independently testable with zero dependency on the others or on the engine:
  - Slow Job — jobs that have gotten slower relative to their own history
  - Slow Step — same, at step granularity
  - Historical Regression — statistically significant runtime regressions over time
  - Dependency Installation — install steps that dominate run time
  - Failure/Retry — wasted time from failing runs that succeed on retry
  - Potential Parallelization — deliberately conservative; never claims two jobs are safe to parallelize just because they happen to run sequentially today
- **Evidence-backed findings** — every finding carries `evidence` (concrete statements citing observed numbers and run IDs), `metrics`, and a `basis` for any savings estimate; nothing is inferred without a number to point to.
- **Idempotent sync** — repeated syncs upsert cleanly; a single run's job-fetch failure doesn't abort the whole sync.
- **Session-based auth** — signed, httponly session cookie; the frontend never sees a GitHub token.
- **206 automated tests** across the three packages (see [Testing](#testing)).

## Architecture

Three independently-versioned packages in one repo:

| Package | Stack | Role |
|---|---|---|
| `analyzer/` | Pure Python, stdlib only | The deterministic analysis engine (`adpo`) — no dependency on the backend, database, or GitHub |
| `backend/` | FastAPI + SQLAlchemy + Alembic + PostgreSQL | Syncs GitHub Actions data, persists it, runs the analyzer against it, serves the REST API |
| `frontend/` | React + TypeScript + Vite + Tailwind + TanStack Query | Dashboard: connect repos, trigger sync/analyze, browse findings |

```mermaid
flowchart TD
    GH[GitHub API] -->|GitHubClient: pagination, retries, rate limits| SYNC[sync_service.py]
    SYNC -->|normalize + idempotent upsert| DB[(PostgreSQL)]
    DB -->|DB rows -> adpo.models| ADAPTER[adapter.py]
    ADAPTER --> ENGINE[adpo.AnalysisEngine]
    ENGINE -->|persist Analysis + Finding rows| DB
    DB --> API[FastAPI REST API]
    API -->|fetch, credentials: include| FE[React dashboard]
```

The analyzer package never imports anything from the backend, and nothing in the backend's GitHub/ingestion code imports the analyzer — `app/services/analysis/adapter.py` is the single, deliberate seam between the two systems.

## Authentication & GitHub App Flow

ADPO uses a **GitHub App**, not a classic OAuth App, specifically for fine-grained, read-only permissions. Two credential types exist and are never conflated:

| Credential | Proves | Lifetime | Stored? |
|---|---|---|---|
| User-to-server OAuth token | Who is signed in | App-configured (refreshable) | Yes, encrypted at rest (Fernet) |
| Installation access token | Which repos are reachable | 1 hour | No — minted on demand, cached in memory only |

Flow:
1. **Login** (`GET /auth/github/login`) — standard GitHub OAuth redirect with a signed, single-use `state` cookie (CSRF protection).
2. **Callback** (`GET /auth/github/callback`) — validates `state`, exchanges the OAuth `code`, creates/updates the `User`, sets a signed httponly session cookie.
3. **Install** (`GET /auth/github/install`) — signed-in user is sent to GitHub to install the App on an account/org. GitHub App installation is a separate GitHub concept from OAuth login, so this same callback route also handles the installation-only redirect GitHub sends back (`installation_id` + `setup_action`, with no `code` when it's an update to an existing installation) — it deliberately never uses that leg to establish or switch who is logged in, closing a login-CSRF hole where an attacker's own install could otherwise hijack a different, already-logged-in victim's session.
4. **Connect a repository** — the frontend lists repos reachable through the user's installation(s) live from the GitHub API, and `POST /repositories` persists the chosen one, scoped to that installation.
5. **Sync + Analyze** — explicit API calls fetch and store run/job/step history, then run it through the analyzer.

The frontend never sees a GitHub token — only the signed session cookie, sent with `credentials: "include"` on every API call.

## Security

- **No secrets are committed.** `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_CLIENT_SECRET`, `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, and `DATABASE_URL` are read from environment variables only (`app/config.py`); `.env` and `*.pem` are gitignored, and `.env.example` ships placeholders only.
- **GitHub App private key** is used only to mint short-lived (10-minute) App JWTs and installation access tokens (`app/services/github/app_auth.py`); installation tokens are never persisted, only cached in memory for their 1-hour lifetime.
- **OAuth tokens are encrypted at rest** (Fernet, via `TOKEN_ENCRYPTION_KEY`) in the database, and never serialized in any API response.
- **Session cookie**: signed (not just base64), httponly, `SameSite=None; Secure` in production (required for the cross-origin Vercel↔Render deployment), `Lax`/non-secure in local dev.
- **Least-privilege GitHub permissions**: `Actions: read-only` and `Metadata: read-only` only — no write access, no access to repository source code or secrets.
- **No repository source code or CI secrets are ever fetched or stored** — only structured run/job/step metadata from the Actions API.
- **Authorization boundary**: every `/repositories/{id}/...` endpoint checks the repository belongs to an installation the current session's user connected, returning `404` (not `403`) for someone else's repository, to avoid confirming it exists.

## Local Development

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker (for local Postgres)
- A GitHub account you can create a test GitHub App on

### 1. Analyzer
```bash
cd analyzer
pip install -e ".[dev]"
pytest
```

### 2. Backend
```bash
cd backend

# Start Postgres (host port 5433)
docker compose up -d

# Install (analyzer first — the backend imports it as `adpo`)
pip install -e ../analyzer
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"        # -> SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> TOKEN_ENCRYPTION_KEY
# create a GitHub App (see backend/README.md "GitHub App setup") and fill in GITHUB_APP_* values

# Apply migrations and run
alembic upgrade head
uvicorn app.main:app --reload
```
Backend runs at `http://localhost:8000` (interactive docs at `/docs`).

### 3. Frontend
```bash
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:3000` — must match the backend's `CORS_ORIGINS` and `FRONTEND_SUCCESS_REDIRECT_URL`. Set `VITE_API_BASE_URL` in a `.env.local` (not committed) if the backend isn't at the default `http://localhost:8000`.

## Environment Variables

Backend (`backend/.env`, see `backend/.env.example`):

```bash
# Database
DATABASE_URL=postgresql+psycopg://adpo:adpo@localhost:5433/adpo

# Session security
ENVIRONMENT=development                  # "development" or "production"
SECRET_KEY=your_random_secret            # signs session cookies
TOKEN_ENCRYPTION_KEY=your_fernet_key     # encrypts stored GitHub tokens

# CORS / host allowlist
CORS_ORIGINS=http://localhost:3000
ALLOWED_HOSTS=                           # set in production to the backend's own hostname(s)

# GitHub App
GITHUB_APP_ID=your_app_id
GITHUB_APP_CLIENT_ID=your_client_id
GITHUB_APP_CLIENT_SECRET=your_client_secret
GITHUB_APP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
GITHUB_APP_PRIVATE_KEY_PATH=             # alternative to the inline key above
GITHUB_APP_NAME=your-app-name
GITHUB_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/auth/github/callback
FRONTEND_SUCCESS_REDIRECT_URL=http://localhost:3000/dashboard
```

Frontend (`frontend/.env.local`, optional):
```bash
VITE_API_BASE_URL=http://localhost:8000  # defaults to this if unset
```

None of these have real values anywhere in this repository, in its history, or in `.env.example`.

## Testing

All 206 tests pass as of the last verified run:

| Package | Command | Result |
|---|---|---|
| `analyzer/` | `pytest tests/ -v` | 124 passed |
| `backend/` | `pytest` | 63 passed |
| `frontend/` | `npm run test` | 19 passed (5 test files) |

Backend tests run against an isolated in-memory/temp SQLite database with mocked HTTP (`respx`) — they never touch a real database or the live GitHub API. The Alembic migration itself is verified separately by applying it to a real local Postgres container.

## Deployment

Production architecture:
- **Frontend** — Vercel, static Vite build (`frontend/vercel.json` rewrites all paths to `index.html` for client-side routing): https://adpo-gilt.vercel.app
- **Backend** — Render (Docker/Python web service): https://adpo-backend.onrender.com
- **Database** — managed PostgreSQL (Render)

For a from-scratch production deploy: install from the pinned lockfile (`backend/requirements-lock.txt`) rather than `pyproject.toml` ranges, run without `--reload`, set `ENVIRONMENT=production` (marks the session cookie `Secure`) and `ALLOWED_HOSTS` to the backend's own hostname. See `backend/README.md` "Running in production" for the full command. No deployment credentials are included in this repository.

## Screenshots

None are checked into the repository yet. Useful additions here would be: the connected-repository dashboard view, the findings list for a repository with real findings, and the "connect a repository" flow. Not included to avoid fabricating an image that doesn't exist in the repo.

## Roadmap

Realistic next steps, based on documented current limitations (`backend/README.md` "Known limitations"):
- Background job queue for sync/analyze instead of synchronous HTTP requests, for larger repositories
- Webhook support, so data doesn't depend on a manual sync call
- Parse workflow YAML to populate `Job.needs` / `Step.uses` / `Step.run`, unlocking real `ParallelizationAnalyzer` and improving `DependencyInstallAnalyzer` recall against live-synced data (both are fully covered by the analyzer's own synthetic-fixture test suite already; only real-data enrichment is missing)
- Backfill every historical attempt of a rerun, not just the latest
- Broader frontend test coverage

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Jenoyrex.

## Contributing

This started as a solo/portfolio project, but issues and PRs are welcome:
- Open an issue describing the bug or proposal before a large PR.
- Match the existing style in the package you're touching (each of `analyzer/`, `backend/`, `frontend/` has its own conventions documented in its own `README.md`).
- Run the relevant package's test suite before submitting (see [Testing](#testing)) — all three must stay green.
- Keep the analyzer's "no LLM, every number traceable" design principle intact if touching `analyzer/`.

## Security / Reporting Vulnerabilities

If you find a security issue (an auth bypass, a way to see another user's data, a secret-handling flaw, etc.), please report it privately rather than opening a public issue: **jenoyrex95@gmail.com**. Include enough detail to reproduce it. Please don't test against the live production deployment (https://adpo-backend.onrender.com / https://adpo-gilt.vercel.app) beyond what's needed to demonstrate the issue — run it against a local instance instead where possible.
