"""CLI + fetch layer tests. All HTTP is served from recorded, trimmed real API
responses (tests/fixtures/github_api/); no test touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adpo.cli import main, rank_findings
from adpo.github_fetch import GitHubClient, GitHubFetchError, HttpResponse, build_attempts, fetch_history
from adpo.ingest import parse_workflow_runs
from adpo.workflow_yaml import enrich_runs, resolve_job_key

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "github_api" / "jenoyrex_adpo.json").read_text())


class RecordedTransport:
    def __init__(self, responses=FIXTURE):
        self.responses = responses
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        rec = self.responses.get(url)
        if rec is None:
            return HttpResponse(404, {}, b'{"message": "Not Found"}')
        return HttpResponse(200, rec["headers"], json.dumps(rec["body"]).encode())


class ScriptedTransport:
    """Returns the given responses in order (for retry/rate-limit tests)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, url, headers):
        self.calls += 1
        return self.responses.pop(0)


def make_client(transport, cache_dir=None, **kw):
    sleeps = []
    client = GitHubClient(
        token=None, cache_dir=cache_dir, transport=transport, sleep=sleeps.append, log=lambda m: None, **kw
    )
    return client, sleeps


OK = HttpResponse(200, {}, b'{"workflow_runs": []}')


# ---- fetch layer ---------------------------------------------------------------


def test_fetch_history_follows_pagination_and_splits_attempts():
    client, _ = make_client(RecordedTransport())
    raw = fetch_history(client, "Jenoyrex", "ADPO", max_runs=10)
    run_ids = [r["run_id"] for r in raw]
    # 4 runs across two pages; the retried one contributes 2 attempts.
    assert len({rid.split("/")[0] for rid in run_ids}) == 4
    assert len(raw) == 5
    superseded = [r for r in raw if "/attempts/" in r["run_id"]]
    assert len(superseded) == 1 and superseded[0]["run_attempt"] == 1
    latest = next(r for r in raw if r["run_id"] == superseded[0]["run_id"].split("/")[0])
    assert latest["run_attempt"] == 2
    assert all(j["run_attempt"] == 1 for j in superseded[0]["jobs"])


def test_max_runs_stops_early():
    transport = RecordedTransport()
    client, _ = make_client(transport)
    raw = fetch_history(client, "Jenoyrex", "ADPO", max_runs=1, enrich=False)
    assert len(raw) == 1
    assert not any("page=2" in u for u in transport.calls)


def test_yaml_enrichment_adds_needs_and_step_commands():
    client, _ = make_client(RecordedTransport())
    raw = fetch_history(client, "Jenoyrex", "ADPO", max_runs=10)
    newest = raw[0]
    jobs = {j["name"]: j for j in newest["jobs"]}
    assert jobs["Backend tests"]["needs"] == ["Install dependencies"]
    assert jobs["Install dependencies"]["needs"] == []
    install = next(s for s in jobs["Install dependencies"]["steps"] if s["name"] == "Install backend dependencies")
    assert "pip install" in install["run"]
    setup = next(s for s in jobs["Install dependencies"]["steps"] if s["name"] == "Set up Python")
    assert setup["uses"].startswith("actions/setup-python") and setup["with"]["cache"] == "pip"
    # The enriched dicts must still be valid ingest input.
    runs = parse_workflow_runs(raw)
    assert runs[0].job_by_name("Backend tests").needs == ["Install dependencies"]


def test_cache_makes_reruns_free(tmp_path):
    client, _ = make_client(RecordedTransport(), cache_dir=tmp_path)
    first = fetch_history(client, "Jenoyrex", "ADPO", max_runs=10)

    def no_network(url, headers):
        raise AssertionError(f"unexpected network call: {url}")

    client2, _ = make_client(no_network, cache_dir=tmp_path)
    assert fetch_history(client2, "Jenoyrex", "ADPO", max_runs=10) == first


def test_run_listing_cache_expires(tmp_path):
    clock = [1000.0]
    url = "https://api.github.com/x"
    t = ScriptedTransport([OK, OK])
    client, _ = make_client(t, cache_dir=tmp_path, now=lambda: clock[0])
    client.get_json(url, ttl=60)
    client.get_json(url, ttl=60)
    assert t.calls == 1
    clock[0] += 61
    client.get_json(url, ttl=60)
    assert t.calls == 2


def test_rate_limit_waits_until_reset():
    reset_at = 1000.0 + 30
    limited = HttpResponse(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)}, b"rate limit")
    client, sleeps = make_client(ScriptedTransport([limited, OK]), now=lambda: 1000.0)
    client.get_json("https://api.github.com/x")
    assert sleeps == [31.0]


def test_retry_after_is_respected():
    client, sleeps = make_client(ScriptedTransport([HttpResponse(429, {"retry-after": "7"}, b""), OK]))
    client.get_json("https://api.github.com/x")
    assert sleeps == [7.0]


def test_server_errors_back_off_exponentially_then_give_up():
    err = HttpResponse(502, {}, b"bad gateway")
    client, sleeps = make_client(ScriptedTransport([err] * 4), max_retries=3)
    with pytest.raises(GitHubFetchError):
        client.get_json("https://api.github.com/x")
    assert sleeps == [1.0, 2.0, 4.0]


def test_plain_403_is_not_retried():
    t = ScriptedTransport([HttpResponse(403, {"x-ratelimit-remaining": "4999"}, b"Resource not accessible")])
    client, sleeps = make_client(t)
    with pytest.raises(GitHubFetchError):
        client.get_json("https://api.github.com/x")
    assert sleeps == [] and t.calls == 1


def test_token_sent_only_as_header():
    seen = {}

    def capture(url, headers):
        seen["url"], seen["headers"] = url, headers
        return OK

    GitHubClient(token="t0k", transport=capture, log=lambda m: None).get_json("https://api.github.com/x")
    assert seen["headers"]["Authorization"] == "Bearer t0k" and "t0k" not in seen["url"]


def test_superseded_attempt_conclusion_derived_from_jobs():
    run = {
        "id": 9,
        "name": "CI",
        "run_number": 1,
        "run_attempt": 2,
        "conclusion": "success",
        "created_at": "2024-01-01T00:00:00Z",
    }
    jobs = [
        {"id": 1, "name": "test", "run_attempt": 1, "conclusion": "failure"},
        {"id": 2, "name": "test", "run_attempt": 2, "conclusion": "success"},
    ]
    a1, a2 = build_attempts(run, jobs)
    assert (a1["run_id"], a1["conclusion"]) == ("9/attempts/1", "failure")
    assert (a2["run_id"], a2["conclusion"]) == ("9", "success")


# ---- YAML name matching -----------------------------------------------------------


def test_resolve_job_key_handles_matrix_expression_and_reusable_names():
    spec = {
        "build": {},
        "test": {"name": "Test on ${{ matrix.os }}"},
        "lint": {"name": "Lint"},
        "release": {"uses": "./.github/workflows/release.yml"},
    }
    assert resolve_job_key("build", spec) == "build"
    assert resolve_job_key("build (3.11, ubuntu-latest)", spec) == "build"
    assert resolve_job_key("Test on windows-latest", spec) == "test"
    assert resolve_job_key("Lint", spec) == "lint"
    assert resolve_job_key("release / publish", spec) == "release"
    assert resolve_job_key("something-else", spec) is None


def test_enrich_matches_default_step_names_and_skips_unmatched():
    yaml_text = """
jobs:
  test:
    needs: [build]
    steps:
      - uses: actions/checkout@v4
      - run: |
          npm ci --prefer-offline
          npm test
  build:
    steps: [{run: make}]
"""
    raw = [
        {
            "workflow_path": "ci.yml",
            "jobs": [
                {"name": "build", "steps": [{"name": "Run make"}]},
                {
                    "name": "test",
                    "steps": [
                        {"name": "Set up job"},
                        {"name": "Run actions/checkout@v4"},
                        {"name": "Run npm ci --prefer-offline"},
                        {"name": "Post Run actions/checkout@v4"},
                    ],
                },
                {"name": "mystery", "steps": []},
            ],
        }
    ]
    stats = enrich_runs(raw, {"ci.yml": yaml_text})
    build, test, mystery = raw[0]["jobs"]
    assert test["needs"] == ["build"] and build["needs"] == []
    assert test["steps"][1]["uses"] == "actions/checkout@v4"
    assert test["steps"][2]["run"].startswith("npm ci")
    assert "uses" not in test["steps"][3]
    assert "needs" not in mystery
    assert stats == {"jobs_total": 3, "jobs_matched": 2, "steps_matched": 3}


# ---- CLI ----------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "json", "md"])
def test_cli_renders_every_format(fmt, capsys, tmp_path):
    client, _ = make_client(RecordedTransport())
    assert main(["analyze", "Jenoyrex/ADPO", "--format", fmt, "--runs", "10"], client=client) == 0
    out = capsys.readouterr().out
    if fmt == "json":
        data = json.loads(out)
        assert data["summary"]["runs"] == 4 and data["summary"]["attempts"] == 5
        assert isinstance(data["findings"], list)
    else:
        assert "Jenoyrex/ADPO" in out


def test_cli_json_findings_cite_fetched_run_ids(capsys):
    client, _ = make_client(RecordedTransport())
    main(["analyze", "Jenoyrex/ADPO", "--format", "json"], client=client)
    data = json.loads(capsys.readouterr().out)
    fixture_runs = [r for rec in FIXTURE.values() for r in rec["body"].get("workflow_runs", [])]
    known = {str(r["id"]) for r in fixture_runs} | {str(r["run_number"]) for r in fixture_runs}
    citing = [e for f in data["findings"] for e in f["evidence"] if "run IDs" in e or "run numbers" in e]
    assert citing, "expected at least one finding that cites runs"
    for line in citing:
        cited = {tok.strip(".,") for tok in line.split(":", 1)[1].split()} - {"..."}
        assert cited <= known, line


@pytest.mark.parametrize("argv", [["analyze", "not-a-repo"], ["analyze", "a/b/c"], ["analyze", "a/b", "--runs", "0"]])
def test_cli_rejects_bad_arguments(argv):
    assert main(argv, client=make_client(RecordedTransport())[0]) == 2


def test_cli_reports_fetch_errors(capsys):
    client, _ = make_client(RecordedTransport({}))
    assert main(["analyze", "nobody/nothing"], client=client) == 1
    assert "404" in capsys.readouterr().err


def test_rank_orders_by_severity_then_confidence():
    from adpo.models import Confidence, Finding, Severity

    def f(sev, conf, title):
        return Finding("x", title, "", [], {}, sev, conf, "")

    ranked = rank_findings(
        [
            f(Severity.LOW, Confidence.HIGH, "a"),
            f(Severity.CRITICAL, Confidence.LOW, "b"),
            f(Severity.CRITICAL, Confidence.HIGH, "c"),
        ]
    )
    assert [x.title for x in ranked] == ["c", "b", "a"]


def test_run_with_unfetchable_jobs_is_skipped_not_partially_used():
    responses = dict(FIXTURE)
    bad = next(u for u in responses if "/jobs?" in u)
    del responses[bad]

    def transport(url, headers):
        if "/jobs?" in url and url.split("/jobs?")[0] == bad.split("/jobs?")[0]:
            return HttpResponse(502, {}, b"Server Error")
        return RecordedTransport(responses)(url, headers)

    client, _ = make_client(transport, max_retries=0)
    raw = fetch_history(client, "Jenoyrex", "ADPO", max_runs=10, enrich=False)
    skipped_id = bad.split("/runs/")[1].split("/")[0]
    assert client.skipped_runs == [skipped_id]
    assert all(r["run_id"].split("/")[0] != skipped_id for r in raw)
