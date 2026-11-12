"""
Read-only fetch layer for the public GitHub Actions REST API.

Used by the `adpo analyze` CLI and by `research/collect.py`. It turns a repo's
recent workflow runs into the raw run dicts `adpo.ingest` already understands:
one dict per *attempt*, so a re-run shows up as several attempts of the same
run_number, which is what the Failure/Retry analyzer expects.

Stdlib only (urllib). Network behaviour:
  * GITHUB_TOKEN from the environment is sent if present; otherwise requests
    are unauthenticated (60 requests/hour).
  * Pagination follows the `Link: rel="next"` header.
  * Rate limits: on 403/429 it sleeps for `Retry-After` if given, otherwise
    until `X-RateLimit-Reset` when `X-RateLimit-Remaining` is 0; 5xx and
    other 403/429 responses back off exponentially. Gives up after
    `max_retries`.
  * On-disk cache keyed by URL. Responses for completed runs (jobs, workflow
    files at a fixed SHA) never change, so they are cached forever. Run
    listings are cached for `list_ttl_seconds` so reruns are free but not
    stale forever.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

API_ROOT = "https://api.github.com"
USER_AGENT = "adpo-cli"


class GitHubFetchError(RuntimeError):
    pass


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: bytes


Transport = Callable[[str, Dict[str, str]], HttpResponse]


def _ssl_context() -> ssl.SSLContext:
    """Verify TLS against the OS trust store when `truststore` is installed
    (corporate proxies / AV often add a root only the OS knows about),
    otherwise Python's default CA bundle. Verification is never disabled."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def urllib_transport(url: str, headers: Dict[str, str]) -> HttpResponse:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:  # noqa: S310 - https API host
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read())
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        # Surface as a retryable 5xx-like response rather than a traceback.
        return HttpResponse(599, {}, str(exc).encode())


def default_cache_dir() -> Path:
    env = os.environ.get("ADPO_CACHE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "adpo"


def _next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return m.group(1) if m else None


class GitHubClient:
    def __init__(
        self,
        token: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        max_retries: int = 5,
        list_ttl_seconds: float = 3600.0,
        log: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr),
    ):
        self.token = token
        self.cache_dir = cache_dir
        self.transport = transport
        self.sleep = sleep
        self.now = now
        self.max_retries = max_retries
        self.list_ttl_seconds = list_ttl_seconds
        self.log = log
        self.requests_made = 0

    @classmethod
    def from_env(cls, cache_dir: Optional[Path] = None, **kwargs: Any) -> "GitHubClient":
        return cls(token=os.environ.get("GITHUB_TOKEN") or None, cache_dir=cache_dir or default_cache_dir(), **kwargs)

    # ---- cache ---------------------------------------------------------------

    def _cache_path(self, url: str) -> Optional[Path]:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def _cache_get(self, url: str, ttl: Optional[float]) -> Optional[Tuple[Any, Optional[str]]]:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if ttl is not None and self.now() - entry["fetched_at"] > ttl:
            return None
        return entry["body"], entry.get("next")

    def _cache_put(self, url: str, body: Any, next_url: Optional[str]) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "fetched_at": self.now(), "body": body, "next": next_url}), "utf-8")
        tmp.replace(path)

    # ---- HTTP ----------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _wait_seconds(self, resp: HttpResponse, attempt: int) -> Optional[float]:
        """How long to wait before retrying, or None if the response is final."""
        h = resp.headers
        if resp.status in (403, 429):
            if "retry-after" in h:
                return float(h["retry-after"])
            if h.get("x-ratelimit-remaining") == "0" and "x-ratelimit-reset" in h:
                return max(1.0, float(h["x-ratelimit-reset"]) - self.now() + 1)
            if resp.status == 429 or b"secondary rate limit" in resp.body.lower():
                return float(2**attempt * 30)
            return None  # a plain 403 (e.g. forbidden resource) is final
        if resp.status >= 500:
            return float(2**attempt)
        return None

    def get_json(self, url: str, ttl: Optional[float] = None) -> Tuple[Any, Optional[str]]:
        """GET one URL. Returns (parsed body, next-page URL)."""
        if not url.startswith("http"):
            url = API_ROOT + url
        cached = self._cache_get(url, ttl)
        if cached is not None:
            return cached
        for attempt in range(self.max_retries + 1):
            resp = self.transport(url, self._headers())
            self.requests_made += 1
            if resp.status == 200:
                body = json.loads(resp.body.decode("utf-8"))
                nxt = _next_link(resp.headers.get("link"))
                self._cache_put(url, body, nxt)
                return body, nxt
            wait = self._wait_seconds(resp, attempt)
            if wait is None or attempt == self.max_retries:
                snippet = resp.body[:200].decode("utf-8", "replace")
                raise GitHubFetchError(f"GET {url} -> HTTP {resp.status}: {snippet}")
            self.log(f"adpo: HTTP {resp.status} from GitHub, waiting {wait:.0f}s before retrying")
            self.sleep(wait)
        raise GitHubFetchError(f"GET {url}: retries exhausted")  # pragma: no cover - loop always returns/raises

    def paginate(self, url: str, key: str, ttl: Optional[float] = None) -> Iterator[Dict[str, Any]]:
        next_url: Optional[str] = url
        while next_url:
            body, next_url = self.get_json(next_url, ttl=ttl)
            yield from body.get(key, [])

    # ---- Actions endpoints ---------------------------------------------------

    def list_runs(
        self,
        owner: str,
        repo: str,
        workflow: Optional[str] = None,
        max_runs: int = 100,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Most recent completed runs, newest first.

        `workflow` is either a workflow file name (``ci.yml``, uses the
        per-workflow endpoint) or a workflow display name (filtered here).
        """
        params = {"per_page": "100", "status": "completed"}
        if since:
            params["created"] = f">={since}"
        by_file = workflow is not None and workflow.endswith((".yml", ".yaml"))
        base = f"/repos/{owner}/{repo}/actions"
        path = f"{base}/workflows/{urllib.parse.quote(workflow or '')}/runs" if by_file else f"{base}/runs"
        url = f"{path}?{urllib.parse.urlencode(params)}"
        out: List[Dict[str, Any]] = []
        for run in self.paginate(url, "workflow_runs", ttl=self.list_ttl_seconds):
            if workflow and not by_file and run.get("name") != workflow:
                continue
            out.append(run)
            if len(out) >= max_runs:
                break
        return out

    def list_jobs_all_attempts(self, owner: str, repo: str, run_id: int) -> List[Dict[str, Any]]:
        url = f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100"
        return list(self.paginate(url, "jobs"))

    def get_file(self, owner: str, repo: str, path: str, ref: str) -> Optional[str]:
        url = f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}"
        try:
            body, _ = self.get_json(url)
        except GitHubFetchError:
            return None
        if body.get("encoding") != "base64":
            return None
        return base64.b64decode(body["content"]).decode("utf-8", "replace")


# ---- shaping into adpo.ingest raw dicts ---------------------------------------


def _attempt_conclusion(jobs: List[Dict[str, Any]]) -> Optional[str]:
    concs = [j.get("conclusion") for j in jobs]
    if "failure" in concs:
        return "failure"
    if "cancelled" in concs:
        return "cancelled"
    if concs and all(c in ("success", "skipped") for c in concs):
        return "success"
    return None


def build_attempts(run: Dict[str, Any], jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Split one run's jobs (fetched with filter=all) into one raw dict per attempt.

    The latest attempt keeps the run's own conclusion and plain run ID;
    superseded attempts get an ID of the form ``<run_id>/attempts/<n>`` (the
    URL suffix that opens that attempt on github.com) and a conclusion
    derived from their jobs.
    """
    latest = int(run.get("run_attempt") or 1)
    by_attempt: Dict[int, List[Dict[str, Any]]] = {}
    for job in jobs:
        by_attempt.setdefault(int(job.get("run_attempt") or 1), []).append(job)
    out = []
    for attempt in sorted(by_attempt):
        attempt_jobs = by_attempt[attempt]
        is_latest = attempt == latest
        out.append(
            {
                "run_id": str(run["id"]) if is_latest else f"{run['id']}/attempts/{attempt}",
                "workflow_name": run.get("name"),
                "workflow_path": run.get("path"),
                "head_sha": run.get("head_sha"),
                "run_number": run.get("run_number", 0),
                "run_attempt": attempt,
                "branch": run.get("head_branch") or "",
                "event": run.get("event", "unknown"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
                "conclusion": run.get("conclusion") if is_latest else _attempt_conclusion(attempt_jobs),
                "jobs": [
                    {
                        "job_id": str(j.get("id")),
                        "name": j.get("name"),
                        "status": j.get("status"),
                        "conclusion": j.get("conclusion"),
                        "started_at": j.get("started_at"),
                        "completed_at": j.get("completed_at"),
                        "run_attempt": attempt,
                        "runs_on": ",".join(j.get("labels") or []) or j.get("runner_name"),
                        "steps": j.get("steps") or [],
                    }
                    for j in attempt_jobs
                ],
            }
        )
    return out


def fetch_history(
    client: GitHubClient,
    owner: str,
    repo: str,
    workflow: Optional[str] = None,
    max_runs: int = 100,
    since: Optional[str] = None,
    enrich: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """Fetch runs + jobs (all attempts) and return raw dicts for `adpo.ingest`.

    With `enrich=True`, each workflow's YAML at its newest run's commit is
    fetched once and used to attach `needs` to jobs and `uses`/`run`/`with`
    to steps (see `adpo.workflow_yaml`). The Jobs API carries none of these.
    """
    runs = client.list_runs(owner, repo, workflow=workflow, max_runs=max_runs, since=since)
    raw: List[Dict[str, Any]] = []
    for i, run in enumerate(runs):
        raw.extend(build_attempts(run, client.list_jobs_all_attempts(owner, repo, run["id"])))
        if progress:
            progress(i + 1, len(runs))
    if enrich:
        from .workflow_yaml import enrich_runs

        newest_sha: Dict[str, str] = {}
        for run in runs:  # newest first, so the first SHA seen per path is the newest
            if run.get("path") and run.get("head_sha"):
                newest_sha.setdefault(run["path"], run["head_sha"])
        yaml_by_path = {}
        for path, sha in newest_sha.items():
            # Reusable/dynamic workflows (e.g. "dynamic/github-code-scanning/...") have no file.
            if path.startswith(".github/workflows/"):
                text = client.get_file(owner, repo, path.split("@")[0], sha)
                if text is not None:
                    yaml_by_path[path] = text
        enrich_runs(raw, yaml_by_path)
    return raw
