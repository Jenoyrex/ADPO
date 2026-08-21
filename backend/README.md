# ADPO Backend

FastAPI service that connects the locked, deterministic `adpo` analyzer
(`../analyzer`) to real GitHub Actions data.

## Architecture

```
GitHub API
  -> app/services/github/client.py        (GitHubClient: pagination, retries, rate limits)
  -> app/services/ingestion/sync_service.py (normalize + idempotent upsert)
  -> PostgreSQL                            (app/models/*)
  -> app/services/analysis/adapter.py      (DB rows -> adpo.models objects)
  -> adpo.AnalysisEngine                   (../analyzer - untouched)
  -> app/services/analysis/analysis_service.py (persist Analysis + Finding rows)
  -> app/api/v1/*                          (REST responses)
```

The analyzer package never imports anything from `app.*`, and nothing in
`app.services.github` or `app.services.ingestion` imports `adpo`. The only
file that imports both `app.models` and `adpo.models` is
`app/services/analysis/adapter.py` - that is the sole seam between the two
systems, by design.

## Authentication architecture

ADPO uses a **GitHub App**, not a classic OAuth App, specifically to get
fine-grained permissions (`Actions: read-only`, `Metadata: read-only`)
instead of the OAuth App's all-or-nothing `repo` scope. Two credential
types are involved and are never conflated:

| Credential | Proves | Lifetime | Stored? |
|---|---|---|---|
| User-to-server OAuth token | Who is signed in; used to call `/user`, `/user/installations` | Set by App config (typically 8h, refreshable) | Yes, encrypted at rest (`github_tokens` table, Fernet) |
| Installation access token | Which repos are reachable, scoped to exactly the App's granted permissions | 1 hour | No - minted on demand via `app/services/github/app_auth.py`, cached in memory only |

The frontend never sees either token: the browser only ever holds a signed,
httponly session cookie (`adpo_session`) that maps to a `User` row
server-side (`app/core/security.py`).

### GitHub App setup (one-time, manual)

1. Go to https://github.com/settings/apps/new (or your org's equivalent).
2. **GitHub App name**: anything unique, e.g. `adpo-local-dev`. This exact
   name goes in `GITHUB_APP_NAME`.
3. **Homepage URL**: `http://localhost:3000` (or anything for local dev).
4. **Callback URL**: the value of `GITHUB_OAUTH_REDIRECT_URI`
   (`http://localhost:8000/api/v1/auth/github/callback` by default).
   Check "Request user authorization (OAuth) during installation".
5. **Webhook**: uncheck "Active" - ADPO does not use webhooks in this phase.
6. **Permissions** (Repository permissions):
   - `Actions` -> Read-only
   - `Metadata` -> Read-only (mandatory minimum for every GitHub App)
   Leave every other permission at "No access".
7. **Where can this GitHub App be installed?**: "Only on this account" is
   fine for local dev.
8. Create the App, then:
   - Note the **App ID** -> `GITHUB_APP_ID`.
   - Note the **Client ID** -> `GITHUB_APP_CLIENT_ID`.
   - Generate a **Client secret** -> `GITHUB_APP_CLIENT_SECRET`.
   - Generate a **private key** (downloads a `.pem`) -> either point
     `GITHUB_APP_PRIVATE_KEY_PATH` at the file, or paste its contents into
     `GITHUB_APP_PRIVATE_KEY`.
9. Install the App on a test account/org (or use the in-app "Install App"
   flow ADPO exposes at `GET /api/v1/auth/github/install` once signed in).

## Running the backend locally

```bash
# 1. Start Postgres (host port 5433, to avoid clashing with any other
#    local Postgres on the default 5432)
docker compose up -d

# 2. Install both packages (analyzer must be installed first; the backend
#    imports it as `adpo`)
pip install -e ../analyzer
pip install -e ".[dev]"

# 3. Configure environment
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"        # -> SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> TOKEN_ENCRYPTION_KEY
# fill in GITHUB_APP_* values from the "GitHub App setup" section above

# 4. Apply migrations
alembic upgrade head

# 5. Run the API
uvicorn app.main:app --reload
```

Then visit `http://localhost:8000/health`, and start the login flow at
`http://localhost:8000/api/v1/auth/github/login`.

## Running the tests

```bash
pytest
```

Tests run against an isolated in-memory/temp SQLite database and mocked
HTTP (via `respx`) - they never touch the local Postgres container or the
real GitHub API. The Alembic migration itself is verified separately by
actually applying it to the local Postgres container (done once during
development; re-run `alembic upgrade head` any time the schema changes).

## API

See the project root `docs/` (or the auto-generated `http://localhost:8000/docs`
Swagger UI) for the full endpoint list and request/response shapes.

## Known limitations

- **`Job.needs` is always `[]` for real synced data.** GitHub's "list jobs
  for a workflow run" REST API does not return job dependency (`needs:`)
  information - that only exists in the workflow YAML. Parsing workflow
  YAML would mean fetching and handling repository source code, which this
  MVP intentionally avoids (see "Do not store repository source code" in
  the project constraints). As a direct consequence,
  `adpo.analyzers.ParallelizationAnalyzer` will not surface findings
  against live-synced data until a future, explicitly-scoped enrichment
  step is added.
- **`Step.uses`/`Step.with_params`/`Step.run` are always empty for real
  synced data**, for the same reason - the Jobs API's step objects carry
  only `name`/`status`/`conclusion`/timestamps. `adpo.analyzers.DependencyInstallAnalyzer`
  will therefore only match on step `name` text, not on action reference or
  shell command text, reducing its recall against live data (it is fully
  exercised, including `uses`/`run`, by the analyzer's own 124-test suite
  using synthetic fixtures).
- **Only the latest attempt of a rerun is synced.** GitHub's run-listing
  endpoint returns one row per run representing its current/latest attempt;
  backfilling every historical attempt would require one extra API call per
  rerun and was judged out of scope for this MVP.
- **Sync and analysis are synchronous HTTP requests**, not background jobs.
  Fine at MVP scale (a repo sync is a handful of paginated GitHub API
  calls); a large repo with thousands of runs would want a background task
  queue, deliberately not introduced here per the "do not over-engineer"
  constraint.
- **No webhook support.** Data is only as fresh as the last manual
  `POST /repositories/{id}/sync` call.
