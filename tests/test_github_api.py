from datetime import date, datetime, timezone

from githubactivitychart.github_api import GitHubClient, RepositoryRef, iter_month_windows


def test_iter_month_windows_caps_current_month_at_now() -> None:
    windows = iter_month_windows(
        start_month=date(2026, 2, 1),
        end_month=date(2026, 3, 1),
        now=datetime(2026, 3, 23, 12, 34, 56, tzinfo=timezone.utc),
    )

    assert [window.month_start for window in windows] == [date(2026, 2, 1), date(2026, 3, 1)]
    assert windows[0].to_datetime.isoformat() == "2026-02-28T23:59:59+00:00"
    assert windows[1].to_datetime.isoformat() == "2026-03-23T12:34:56+00:00"


def test_list_repositories_for_viewer_respects_private_flag(monkeypatch) -> None:
    client = GitHubClient("dummy-token")

    def fake_paginate(path: str, params=None, **kwargs):
        assert path == "/user/repos"
        visibility = params["visibility"]
        if visibility == "public":
            return [
                {
                    "full_name": "me/public-repo",
                    "owner": {"login": "me"},
                    "private": False,
                    "default_branch": "main",
                }
            ]
        return [
            {
                "full_name": "me/public-repo",
                "owner": {"login": "me"},
                "private": False,
                "default_branch": "main",
            },
            {
                "full_name": "me/private-repo",
                "owner": {"login": "me"},
                "private": True,
                "default_branch": "main",
            },
        ]

    monkeypatch.setattr(client, "fetch_viewer_login", lambda: "me")
    monkeypatch.setattr(client, "_paginate", fake_paginate)

    public_only = client.list_repositories_for_user("me", include_private=False)
    with_private = client.list_repositories_for_user("me", include_private=True)

    assert [repo.name_with_owner for repo in public_only] == ["me/public-repo"]
    assert [repo.name_with_owner for repo in with_private] == ["me/public-repo", "me/private-repo"]


def test_list_repositories_for_other_user_adds_accessible_private_repos(monkeypatch) -> None:
    client = GitHubClient("dummy-token")

    def fake_paginate(path: str, params=None, **kwargs):
        if path == "/users/octocat/repos":
            return [
                {
                    "full_name": "octocat/public-repo",
                    "owner": {"login": "octocat"},
                    "private": False,
                    "default_branch": "main",
                }
            ]
        if path == "/user/repos":
            return [
                {
                    "full_name": "octocat/private-repo",
                    "owner": {"login": "octocat"},
                    "private": True,
                    "default_branch": "main",
                },
                {
                    "full_name": "someone-else/private-repo",
                    "owner": {"login": "someone-else"},
                    "private": True,
                    "default_branch": "main",
                },
            ]
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(client, "fetch_viewer_login", lambda: "viewer")
    monkeypatch.setattr(client, "_paginate", fake_paginate)

    repositories = client.list_repositories_for_user("octocat", include_private=True)

    assert [repo.name_with_owner for repo in repositories] == [
        "octocat/public-repo",
        "octocat/private-repo",
    ]


def test_fetch_monthly_commit_counts_aggregates_direct_repo_commits(monkeypatch) -> None:
    client = GitHubClient("dummy-token")
    repositories = [
        RepositoryRef(
            name_with_owner="me/public-repo",
            owner_login="me",
            is_private=False,
            default_branch="main",
        ),
        RepositoryRef(
            name_with_owner="me/private-repo",
            owner_login="me",
            is_private=True,
            default_branch="main",
        ),
    ]

    def fake_list_repositories_for_user(username: str, include_private: bool):
        assert username == "me"
        assert include_private is True
        return repositories

    def fake_iter_repository_commits(repository: RepositoryRef, username: str, from_datetime, to_datetime):
        assert username == "me"
        if repository.name_with_owner == "me/public-repo":
            return [
                {"commit": {"author": {"date": "2026-01-22T05:23:06Z"}}},
                {"commit": {"author": {"date": "2026-03-11T12:26:35Z"}}},
            ]
        return [
            {"commit": {"author": {"date": "2026-01-02T00:00:00Z"}}},
            {"commit": {"author": {"date": "2026-01-15T00:00:00Z"}}},
        ]

    monkeypatch.setattr(client, "list_repositories_for_user", fake_list_repositories_for_user)
    monkeypatch.setattr(client, "iter_repository_commits", fake_iter_repository_commits)
    monkeypatch.setattr(
        client,
        "fetch_other_repository_contribution_counts",
        lambda **kwargs: {
            date(2026, 1, 1): {"someone-else/shared-repo": 4},
            date(2026, 2, 1): {},
            date(2026, 3, 1): {"someone-else/shared-repo": 2},
        },
    )

    counts = client.fetch_monthly_commit_counts(
        username="me",
        start_month=date(2026, 1, 1),
        end_month=date(2026, 3, 1),
        include_private=True,
    )

    assert counts == {
        date(2026, 1, 1): {"me/public-repo": 1, "me/private-repo": 2, "someone-else/shared-repo": 4},
        date(2026, 2, 1): {},
        date(2026, 3, 1): {"me/public-repo": 1, "someone-else/shared-repo": 2},
    }


def test_fetch_other_repository_contribution_counts_skips_owned_repos(monkeypatch) -> None:
    client = GitHubClient("dummy-token")

    def fake_execute_graphql(query: str, variables: dict[str, object]):
        return {
            "user": {
                "contributionsCollection": {
                    "commitContributionsByRepository": [
                        {
                            "repository": {"nameWithOwner": "me/owned-repo", "isPrivate": False},
                            "contributions": {"nodes": [{"occurredAt": "2026-01-01T00:00:00Z", "commitCount": 3}]},
                        },
                        {
                            "repository": {"nameWithOwner": "other/shared-repo", "isPrivate": False},
                            "contributions": {"nodes": [{"occurredAt": "2026-01-02T00:00:00Z", "commitCount": 5}]},
                        },
                    ]
                }
            }
        }

    monkeypatch.setattr(client, "execute_graphql", fake_execute_graphql)

    counts = client.fetch_other_repository_contribution_counts(
        username="me",
        start_month=date(2026, 1, 1),
        end_month=date(2026, 1, 1),
    )

    assert counts == {
        date(2026, 1, 1): {"other/shared-repo": 5},
    }
