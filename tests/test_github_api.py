import json
import subprocess
from datetime import UTC, date, datetime, timedelta

import pytest
import requests

from githubactivitychart import github_api
from githubactivitychart.github_api import (
    GitHubAPIError,
    GitHubClient,
    IncompleteContributionDataError,
    iter_month_windows,
    parse_github_datetime,
    parse_retry_after,
)


def make_response(
    status_code: int,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    body = text if text is not None else json.dumps(payload if payload is not None else {})
    response._content = body.encode()
    return response


def contribution_payload(
    entries: list[tuple[str, bool, list[int]]],
) -> dict[str, object]:
    return {
        "user": {
            "contributionsCollection": {
                "commitContributionsByRepository": [
                    {
                        "repository": {
                            "nameWithOwner": name,
                            "isPrivate": is_private,
                        },
                        "contributions": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [{"commitCount": commit_count} for commit_count in commit_counts],
                        },
                    }
                    for name, is_private, commit_counts in entries
                ]
            }
        }
    }


def test_iter_month_windows_caps_current_month_at_now() -> None:
    now = datetime(2026, 3, 23, 12, 34, 56, 123456, tzinfo=UTC)
    windows = iter_month_windows(
        start_month=date(2026, 2, 1),
        end_month=date(2026, 3, 1),
        now=now,
    )

    assert [window.month_start for window in windows] == [
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert windows[0].to_datetime.isoformat() == "2026-02-28T23:59:59.999999+00:00"
    assert windows[1].to_datetime == now


@pytest.mark.parametrize(
    ("start_month", "end_month", "message"),
    [
        (date(2026, 1, 2), date(2026, 2, 1), "first day"),
        (date(2026, 2, 1), date(2026, 1, 1), "must not be earlier"),
        (date(2026, 1, 1), date(2026, 4, 1), "future"),
    ],
)
def test_iter_month_windows_rejects_invalid_ranges(
    start_month: date,
    end_month: date,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        iter_month_windows(
            start_month,
            end_month,
            now=datetime(2026, 3, 23, tzinfo=UTC),
        )


def test_iter_month_windows_requires_timezone_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone"):
        iter_month_windows(
            date(2026, 1, 1),
            date(2026, 1, 1),
            now=datetime(2026, 1, 20),  # noqa: DTZ001 - deliberately naive
        )


def test_parse_github_datetime_preserves_source_offset() -> None:
    parsed = parse_github_datetime("2026-02-01T00:30:00+09:00")

    assert parsed.date() == date(2026, 2, 1)
    assert parsed.utcoffset() == timedelta(hours=9)


def test_parse_github_datetime_rejects_naive_value() -> None:
    with pytest.raises(ValueError, match="timezone"):
        parse_github_datetime("2026-02-01T00:30:00")


def test_parse_retry_after_accepts_seconds_and_http_date() -> None:
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

    assert parse_retry_after("2.5", now=now) == 2.5
    assert parse_retry_after("Thu, 01 Jan 2026 00:00:03 GMT", now=now) == 3
    assert parse_retry_after("not-a-date", now=now) is None
    assert parse_retry_after(None, now=now) is None


def test_resolve_token_rejects_enterprise_gh_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "enterprise-token")
    monkeypatch.setenv("GH_HOST", "github.example.com")

    with pytest.raises(GitHubAPIError, match="non-github.com"):
        github_api.resolve_github_token()


def test_resolve_token_prefers_github_token_and_accepts_github_dot_com_gh_token(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "primary")
    monkeypatch.setenv("GH_TOKEN", "secondary")
    assert github_api.resolve_github_token() == "primary"

    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.setenv("GH_HOST", "github.com")
    assert github_api.resolve_github_token() == "secondary"


def test_client_rejects_empty_token() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        GitHubClient("")


def test_resolve_token_uses_explicit_github_hostname(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_HOST", raising=False)
    monkeypatch.setattr(github_api.shutil, "which", lambda command: "/usr/local/bin/gh")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="token\n", stderr="")

    monkeypatch.setattr(github_api.subprocess, "run", fake_run)

    assert github_api.resolve_github_token() == "token"
    assert calls[0][0] == [
        "/usr/local/bin/gh",
        "auth",
        "token",
        "--hostname",
        "github.com",
    ]
    assert calls[0][1]["timeout"] == github_api.GH_COMMAND_TIMEOUT_SECONDS


def test_resolve_token_reports_gh_timeout(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github_api.shutil, "which", lambda command: "/usr/local/bin/gh")
    monkeypatch.setattr(
        github_api.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("gh", 10)),
    )

    with pytest.raises(GitHubAPIError, match="timed out"):
        github_api.resolve_github_token()


def test_rest_request_retries_transient_http_and_transport_failures(
    monkeypatch,
) -> None:
    sleeps = []
    client = GitHubClient(
        "dummy-token",
        sleep=sleeps.append,
        random_uniform=lambda low, high: 0.0,
        max_retries=3,
    )
    responses: list[requests.Response | Exception] = [
        requests.Timeout("slow"),
        make_response(503),
        make_response(200, payload={"created_at": "2026-01-02T03:04:05Z"}),
    ]

    def fake_get(*args, **kwargs):
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client._session, "get", fake_get)

    assert client.fetch_user_created_at("me") == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert sleeps == [1.0, 2.0]


def test_transport_failure_exhaustion_is_typed(monkeypatch) -> None:
    client = GitHubClient(
        "dummy-token",
        sleep=lambda delay: None,
        random_uniform=lambda low, high: 0.0,
        max_retries=1,
    )
    monkeypatch.setattr(
        client._session,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )

    with pytest.raises(GitHubAPIError, match="ConnectionError"):
        client.fetch_user_created_at("me")


def test_secondary_rate_limit_waits_at_least_sixty_seconds(monkeypatch) -> None:
    sleeps = []
    client = GitHubClient(
        "dummy-token",
        sleep=sleeps.append,
        random_uniform=lambda low, high: 0.0,
        max_retries=1,
    )
    responses = [
        make_response(403, payload={"message": "secondary rate limit"}),
        make_response(200, payload={"created_at": "2026-01-02T03:04:05Z"}),
    ]
    monkeypatch.setattr(
        client._session,
        "get",
        lambda *args, **kwargs: responses.pop(0),
    )

    client.fetch_user_created_at("me")

    assert sleeps == [60.0]


def test_primary_rate_limit_long_wait_raises(monkeypatch) -> None:
    client = GitHubClient(
        "dummy-token",
        sleep=lambda delay: None,
        max_rate_limit_wait_seconds=0,
    )
    reset_at = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    monkeypatch.setattr(
        client._session,
        "get",
        lambda *args, **kwargs: make_response(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_at),
            },
        ),
    )

    with pytest.raises(GitHubAPIError, match="rate limit reached"):
        client.fetch_user_created_at("me")


def test_execute_graphql_retries_rate_limit_in_http_200(monkeypatch) -> None:
    sleeps = []
    client = GitHubClient(
        "dummy-token",
        sleep=sleeps.append,
        random_uniform=lambda low, high: 0.0,
        max_retries=1,
    )
    responses = [
        make_response(200, payload={"errors": [{"message": "API rate limit exceeded"}]}),
        make_response(200, payload={"data": {"user": {"login": "me"}}}),
    ]
    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: responses.pop(0),
    )

    assert client.execute_graphql("query {}", {}) == {"user": {"login": "me"}}
    assert sleeps == [60.0]


def test_execute_graphql_honors_rate_limit_headers_in_http_200(monkeypatch) -> None:
    sleeps = []
    client = GitHubClient(
        "dummy-token",
        sleep=sleeps.append,
        random_uniform=lambda low, high: 0.0,
        max_retries=1,
    )
    responses = [
        make_response(
            200,
            payload={"errors": [{"type": "RATE_LIMITED", "message": "slow down"}]},
            headers={"Retry-After": "2"},
        ),
        make_response(200, payload={"data": {"user": {"login": "me"}}}),
    ]
    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: responses.pop(0),
    )

    client.execute_graphql("query {}", {})

    assert sleeps == [2.0]


def test_execute_graphql_rate_limit_exhaustion_is_typed(monkeypatch) -> None:
    client = GitHubClient("dummy-token", max_retries=0)
    monkeypatch.setattr(
        client._session,
        "post",
        lambda *args, **kwargs: make_response(
            200,
            payload={"errors": [{"message": "rate limit exceeded"}]},
        ),
    )

    with pytest.raises(GitHubAPIError, match="remained rate limited"):
        client.execute_graphql("query {}", {})


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (make_response(401), "HTTP 401"),
        (make_response(200, payload={"errors": [{"message": "bad query"}]}), "rejected"),
        (make_response(200, payload={"unexpected": True}), "unexpected"),
        (make_response(200, text="<html>"), "invalid JSON"),
    ],
)
def test_execute_graphql_reports_sanitized_errors(
    monkeypatch,
    response: requests.Response,
    message: str,
) -> None:
    client = GitHubClient("dummy-token", max_retries=0)
    monkeypatch.setattr(client._session, "post", lambda *args, **kwargs: response)

    with pytest.raises(GitHubAPIError, match=message) as caught:
        client.execute_graphql("query {}", {"sensitive": "private/repo"})

    assert "private/repo" not in str(caught.value)
    assert response.text not in str(caught.value)


def test_fetch_user_created_at_escapes_path(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    seen_paths = []

    def fake_get_json(path: str, **kwargs):
        seen_paths.append(path)
        return {"created_at": "2026-01-02T03:04:05+09:00"}

    monkeypatch.setattr(client, "_get_json", fake_get_json)

    result = client.fetch_user_created_at("name/with/slash")

    assert seen_paths == ["/users/name%2Fwith%2Fslash"]
    assert result.utcoffset() == timedelta(hours=9)


def test_fetch_user_created_at_handles_missing_or_incomplete_user(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    responses = [None, {}]
    monkeypatch.setattr(client, "_get_json", lambda *args, **kwargs: responses.pop(0))

    with pytest.raises(GitHubAPIError, match="not found"):
        client.fetch_user_created_at("missing")
    with pytest.raises(GitHubAPIError, match="incomplete"):
        client.fetch_user_created_at("incomplete")


def test_private_access_validates_classic_token_scopes(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(
        client,
        "_request",
        lambda path: make_response(200, headers={"X-OAuth-Scopes": "repo, gist"}),
    )

    with pytest.raises(GitHubAPIError, match="read:user"):
        client.validate_private_access()


def test_private_access_reports_all_missing_classic_scopes(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(
        client,
        "_request",
        lambda path: make_response(200, headers={"X-OAuth-Scopes": "gist"}),
    )

    with pytest.raises(GitHubAPIError, match="repo, read:user"):
        client.validate_private_access()


def test_private_access_allows_fine_grained_or_complete_classic_token(
    monkeypatch,
) -> None:
    client = GitHubClient("dummy-token")
    responses = [
        make_response(200),
        make_response(200, headers={"X-OAuth-Scopes": "repo, read:user"}),
    ]
    monkeypatch.setattr(client, "_request", lambda path: responses.pop(0))

    client.validate_private_access()
    client.validate_private_access()


def test_fetch_monthly_counts_uses_one_consistent_contribution_source(
    monkeypatch,
) -> None:
    client = GitHubClient("dummy-token")
    calls = []
    payloads = [
        contribution_payload(
            [
                ("me/owned", False, [2, 3]),
                ("other/shared", False, [4]),
                ("me/private-name", True, [7]),
            ]
        ),
        contribution_payload(
            [
                ("me/owned", False, [1]),
                ("me/private-name", True, [2, 3]),
            ]
        ),
    ]

    def fake_execute(query, variables):
        calls.append(variables)
        return payloads.pop(0)

    monkeypatch.setattr(client, "execute_graphql", fake_execute)
    monkeypatch.setattr(client, "validate_private_access", lambda: None)

    counts = client.fetch_monthly_commit_counts(
        username="me",
        start_month=date(2026, 1, 1),
        end_month=date(2026, 2, 1),
        include_private=True,
        now=datetime(2026, 3, 1, tzinfo=UTC),
    )

    assert counts == {
        date(2026, 1, 1): {
            "me/owned": 5,
            "other/shared": 4,
            "Private repositories": 7,
        },
        date(2026, 2, 1): {
            "me/owned": 1,
            "Private repositories": 5,
        },
    }
    assert all(call["maxRepositories"] == 100 for call in calls)


def test_fetch_monthly_counts_hides_private_data_unless_requested(
    monkeypatch,
) -> None:
    client = GitHubClient("dummy-token")
    payload = contribution_payload(
        [
            ("me/public", False, [2]),
            ("me/sensitive-private-name", True, [9]),
        ]
    )
    monkeypatch.setattr(client, "execute_graphql", lambda *args: payload)

    counts = client.fetch_monthly_commit_counts(
        "me",
        date(2026, 1, 1),
        date(2026, 1, 1),
        now=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert counts == {date(2026, 1, 1): {"me/public": 2}}


def test_fetch_monthly_counts_can_explicitly_show_private_names(
    monkeypatch,
) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(client, "validate_private_access", lambda: None)
    monkeypatch.setattr(
        client,
        "execute_graphql",
        lambda *args: contribution_payload([("me/private", True, [3])]),
    )

    counts = client.fetch_monthly_commit_counts(
        "me",
        date(2026, 1, 1),
        date(2026, 1, 1),
        include_private=True,
        show_private_names=True,
        now=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert counts == {date(2026, 1, 1): {"me/private": 3}}


def test_fetch_monthly_counts_progress_never_contains_repository_names(
    monkeypatch,
) -> None:
    progress = []
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(
        client,
        "execute_graphql",
        lambda *args: contribution_payload([("secret/private-name", True, [3])]),
    )

    client.fetch_monthly_commit_counts(
        "me",
        date(2026, 1, 1),
        date(2026, 1, 1),
        progress_callback=progress.append,
        now=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert progress == ["Fetching contribution data for 2026-01 (1/1)"]
    assert "private-name" not in "\n".join(progress)


def test_fetch_monthly_counts_fails_at_repository_cap(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(
        client,
        "execute_graphql",
        lambda *args: contribution_payload([(f"other/repo-{index}", False, [1]) for index in range(2)]),
    )

    with pytest.raises(IncompleteContributionDataError, match="No chart was written"):
        client.fetch_monthly_commit_counts(
            "me",
            date(2026, 1, 1),
            date(2026, 1, 1),
            max_contribution_repositories=2,
            now=datetime(2026, 2, 1, tzinfo=UTC),
        )


def test_fetch_monthly_counts_fails_when_repository_entries_are_paginated(
    monkeypatch,
) -> None:
    client = GitHubClient("dummy-token")
    payload = contribution_payload([("other/busy-repo", False, [100])])
    repository_entry = payload["user"]["contributionsCollection"]["commitContributionsByRepository"][0]
    repository_entry["contributions"]["pageInfo"]["hasNextPage"] = True
    monkeypatch.setattr(client, "execute_graphql", lambda *args: payload)

    with pytest.raises(IncompleteContributionDataError, match="per-repository"):
        client.fetch_monthly_commit_counts(
            "me",
            date(2026, 1, 1),
            date(2026, 1, 1),
            now=datetime(2026, 2, 1, tzinfo=UTC),
        )


def test_fetch_monthly_counts_rejects_private_names_without_private_mode() -> None:
    client = GitHubClient("dummy-token")

    with pytest.raises(ValueError, match="requires include_private"):
        client.fetch_monthly_commit_counts(
            "me",
            date(2026, 1, 1),
            date(2026, 1, 1),
            show_private_names=True,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"user": {}},
        {"user": {"contributionsCollection": {"commitContributionsByRepository": "not-a-list"}}},
        {"user": {"contributionsCollection": {"commitContributionsByRepository": [{"repository": {}}]}}},
    ],
)
def test_fetch_monthly_counts_rejects_malformed_contribution_data(
    monkeypatch,
    payload,
) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(client, "execute_graphql", lambda *args: payload)

    with pytest.raises(GitHubAPIError, match="contribution"):
        client.fetch_monthly_commit_counts(
            "me",
            date(2026, 1, 1),
            date(2026, 1, 1),
            now=datetime(2026, 2, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize("limit", [0, 101])
def test_fetch_monthly_counts_rejects_graphql_limit_outside_bounds(limit: int) -> None:
    client = GitHubClient("dummy-token")

    with pytest.raises(ValueError, match="between 1 and 100"):
        client.fetch_monthly_commit_counts(
            "me",
            date(2026, 1, 1),
            date(2026, 1, 1),
            max_contribution_repositories=limit,
        )


def test_fetch_monthly_counts_reports_missing_user(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    monkeypatch.setattr(client, "execute_graphql", lambda *args: {"user": None})

    with pytest.raises(GitHubAPIError, match="not found"):
        client.fetch_monthly_commit_counts(
            "missing",
            date(2026, 1, 1),
            date(2026, 1, 1),
            now=datetime(2026, 2, 1, tzinfo=UTC),
        )
