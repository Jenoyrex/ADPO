import httpx
import pytest
import respx

from app.services.github.client import GitHubClient
from app.services.github.exceptions import GitHubAPIError, GitHubRateLimitError

BASE = "https://api.github.com"


@respx.mock
def test_pagination_follows_link_header():
    path = f"{BASE}/repos/octo/widgets/actions/workflows"
    page2_url = f"{path}?per_page=100&page=2"

    # A single route whose response depends on the "page" query param, so
    # matching is unambiguous (respx matches routes by path only unless a
    # query constraint is given, and a route with no query constraint would
    # otherwise also match the page-2 request - see the regression test
    # below, which pins down exactly that failure mode).
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 3}])  # last page: no Link header
        return httpx.Response(200, json=[{"id": 1}, {"id": 2}], headers={"Link": f'<{page2_url}>; rel="next"'})

    respx.get(path).mock(side_effect=responder)

    with GitHubClient("tok", base_url=BASE) as client:
        result = client.list_workflows("octo", "widgets")

    assert [item["id"] for item in result] == [1, 2, 3]


def test_pagination_link_header_matching_an_unconstrained_route_would_loop_forever():
    """Regression guard for the failure mode discovered while writing the
    pagination test above: if a mocked page's `Link: rel="next"` header
    points at a URL that a *different, unconstrained* respx route also
    matches (because that route ignores query strings), `_paginate` will
    follow it forever - each request succeeds, so this is a silent
    infinite loop, not an error. It is a property of URL/query matching,
    not of `GitHubClient`, so it is asserted here directly rather than by
    re-introducing the bug into `test_pagination_follows_link_header`."""
    path = f"{BASE}/repos/octo/widgets/actions/workflows"
    page2_url = f"{path}?per_page=100&page=2"

    with respx.mock:
        respx.get(path).mock(
            return_value=httpx.Response(
                200, json=[{"id": 1}], headers={"Link": f'<{page2_url}>; rel="next"'}
            )
        )
        with httpx.Client(timeout=5.0) as c:
            r1 = c.get(path, params={"per_page": 100})
            r2 = c.get(r1.links["next"]["url"])
            # The unconstrained route matches the page-2 request too, and
            # that response *also* carries a "next" link - proving the loop
            # would continue, without actually looping in the test itself.
            assert r2.json() == [{"id": 1}]
            assert "next" in r2.links


@respx.mock
def test_pagination_stops_at_max_pages_against_a_self_referencing_link(monkeypatch):
    """If a "next" link never terminates (buggy server, or a route-matching
    accident like the one in the regression test above), pagination must
    still stop - not loop forever - per `GitHubClient.MAX_PAGES`."""
    from app.services.github import client as client_module

    monkeypatch.setattr(client_module.GitHubClient, "MAX_PAGES", 3)
    path = f"{BASE}/repos/octo/widgets/actions/workflows"

    respx.get(path).mock(
        return_value=httpx.Response(200, json=[{"id": 1}], headers={"Link": f'<{path}>; rel="next"'})
    )

    with GitHubClient("tok", base_url=BASE) as client:
        result = client.list_workflows("octo", "widgets")

    assert result == [{"id": 1}, {"id": 1}, {"id": 1}]  # exactly MAX_PAGES items, then stops


@respx.mock
def test_pagination_unwraps_envelope_response():
    url = f"{BASE}/repos/octo/widgets/actions/runs"
    respx.get(url).mock(
        return_value=httpx.Response(200, json={"total_count": 2, "workflow_runs": [{"id": 10}, {"id": 11}]})
    )

    with GitHubClient("tok", base_url=BASE) as client:
        runs = list(client.list_workflow_runs("octo", "widgets"))

    assert [r["id"] for r in runs] == [10, 11]


@respx.mock
def test_primary_rate_limit_retries_after_reset_then_succeeds():
    url = f"{BASE}/user"
    reset_at = 1_700_000_100
    route = respx.get(url)
    route.side_effect = [
        httpx.Response(
            403,
            json={"message": "rate limited"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_at)},
        ),
        httpx.Response(200, json={"id": 1, "login": "octocat"}),
    ]

    sleeps: list[float] = []
    with GitHubClient("tok", base_url=BASE, sleep_fn=sleeps.append) as client:
        # Freeze "now" relative to the mocked reset header by monkeypatching time.time
        import time as time_module

        orig_time = time_module.time
        time_module.time = lambda: reset_at - 30
        try:
            user = client.get_authenticated_user()
        finally:
            time_module.time = orig_time

    assert user["login"] == "octocat"
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 30


@respx.mock
def test_primary_rate_limit_beyond_max_wait_raises():
    url = f"{BASE}/user"
    far_future_reset = 1_700_010_000  # far beyond MAX_RATE_LIMIT_WAIT_SECONDS from "now"
    respx.get(url).mock(
        return_value=httpx.Response(
            403,
            json={"message": "rate limited"},
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(far_future_reset)},
        )
    )

    import time as time_module

    orig_time = time_module.time
    time_module.time = lambda: far_future_reset - 10_000
    try:
        with GitHubClient("tok", base_url=BASE, sleep_fn=lambda s: None) as client:
            with pytest.raises(GitHubRateLimitError):
                client.get_authenticated_user()
    finally:
        time_module.time = orig_time


@respx.mock
def test_secondary_rate_limit_uses_retry_after_header():
    url = f"{BASE}/user"
    route = respx.get(url)
    route.side_effect = [
        httpx.Response(429, json={"message": "secondary rate limit"}, headers={"Retry-After": "2"}),
        httpx.Response(200, json={"id": 1, "login": "octocat"}),
    ]

    sleeps: list[float] = []
    with GitHubClient("tok", base_url=BASE, sleep_fn=sleeps.append) as client:
        user = client.get_authenticated_user()

    assert user["login"] == "octocat"
    assert sleeps == [2.0]


@respx.mock
def test_retryable_5xx_then_success():
    url = f"{BASE}/user"
    route = respx.get(url)
    route.side_effect = [httpx.Response(503), httpx.Response(200, json={"id": 1, "login": "octocat"})]

    with GitHubClient("tok", base_url=BASE, sleep_fn=lambda s: None) as client:
        user = client.get_authenticated_user()

    assert user["login"] == "octocat"


@respx.mock
def test_non_retryable_4xx_raises_github_api_error():
    url = f"{BASE}/user"
    respx.get(url).mock(return_value=httpx.Response(404, json={"message": "not found"}))

    with GitHubClient("tok", base_url=BASE, sleep_fn=lambda s: None) as client:
        with pytest.raises(GitHubAPIError) as exc_info:
            client.get_authenticated_user()
    assert exc_info.value.status_code == 404


@respx.mock
def test_list_jobs_for_run_uses_attempt_scoped_path_when_given():
    url = f"{BASE}/repos/octo/widgets/actions/runs/55/attempts/2/jobs"
    respx.get(url).mock(return_value=httpx.Response(200, json={"jobs": [{"id": 1}]}))

    with GitHubClient("tok", base_url=BASE) as client:
        jobs = client.list_jobs_for_run("octo", "widgets", 55, run_attempt=2)

    assert jobs == [{"id": 1}]


@respx.mock
def test_get_workflow_run_attempt_uses_attempt_scoped_path():
    url = f"{BASE}/repos/octo/widgets/actions/runs/55/attempts/1"
    respx.get(url).mock(return_value=httpx.Response(200, json={"id": 55, "run_attempt": 1, "conclusion": "failure"}))

    with GitHubClient("tok", base_url=BASE) as client:
        run = client.get_workflow_run_attempt("octo", "widgets", 55, 1)

    assert run == {"id": 55, "run_attempt": 1, "conclusion": "failure"}
