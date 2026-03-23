from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests

GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

MONTHLY_COMMITS_QUERY = """
query MonthlyCommitContributions(
  $username: String!
  $from: DateTime!
  $to: DateTime!
  $maxRepositories: Int!
) {
  user(login: $username) {
    contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: $maxRepositories) {
        repository {
          nameWithOwner
          isPrivate
        }
        contributions(first: 100) {
          nodes {
            occurredAt
            commitCount
          }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class MonthWindow:
    month_start: date
    from_datetime: datetime
    to_datetime: datetime


@dataclass(frozen=True)
class RepositoryRef:
    name_with_owner: str
    owner_login: str
    is_private: bool
    default_branch: str | None


def add_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def parse_github_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def build_month_window(
    month_start: date,
    now: datetime | None = None,
) -> MonthWindow:
    if month_start.day != 1:
        raise ValueError("Month boundaries must be the first day of each month.")

    now = now or datetime.now(timezone.utc)
    current_month = date(now.year, now.month, 1)

    from_datetime = datetime.combine(month_start, time.min, tzinfo=timezone.utc)
    if month_start == current_month:
        to_datetime = now
    else:
        to_datetime = datetime.combine(add_month(month_start), time.min, tzinfo=timezone.utc) - timedelta(seconds=1)

    return MonthWindow(
        month_start=month_start,
        from_datetime=from_datetime,
        to_datetime=to_datetime,
    )


def iter_month_windows(
    start_month: date,
    end_month: date,
    now: datetime | None = None,
) -> list[MonthWindow]:
    if start_month.day != 1 or end_month.day != 1:
        raise ValueError("Month boundaries must be the first day of each month.")
    if end_month < start_month:
        raise ValueError("The end month must not be earlier than the start month.")

    now = now or datetime.now(timezone.utc)
    current_month = date(now.year, now.month, 1)
    if end_month > current_month:
        raise ValueError("The end month cannot be in the future.")

    windows: list[MonthWindow] = []
    month = start_month
    while month <= end_month:
        windows.append(build_month_window(month, now=now))
        month = add_month(month)

    return windows


def resolve_github_token() -> str:
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        token = os.getenv(env_name)
        if token:
            return token

    gh_path = shutil.which("gh")
    if gh_path:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
        )
        token = result.stdout.strip()
        if result.returncode == 0 and token:
            return token

    raise RuntimeError(
        "GitHub token not found. Set GITHUB_TOKEN/GH_TOKEN or run `gh auth login` first."
    )


class GitHubClient:
    def __init__(self, token: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        self._viewer_login: str | None = None

    @classmethod
    def from_environment(cls) -> "GitHubClient":
        return cls(resolve_github_token())

    def _request(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
        allow_conflict: bool = False,
    ) -> requests.Response | None:
        response = self._session.get(
            f"{GITHUB_API_URL}{path}",
            params=params,
            timeout=30,
        )
        if allow_not_found and response.status_code == 404:
            return None
        if allow_conflict and response.status_code == 409:
            return None
        if response.status_code != 200:
            raise RuntimeError(
                f"GitHub REST API returned HTTP {response.status_code} for {path}: {response.text}"
            )
        return response

    def _get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
        allow_conflict: bool = False,
    ) -> Any:
        response = self._request(
            path,
            params=params,
            allow_not_found=allow_not_found,
            allow_conflict=allow_conflict,
        )
        if response is None:
            return None
        return response.json()

    def _paginate(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
        allow_conflict: bool = False,
    ) -> list[dict[str, Any]]:
        base_params = dict(params or {})
        per_page = int(base_params.pop("per_page", 100))
        page = 1
        items: list[dict[str, Any]] = []

        while True:
            page_params = {**base_params, "per_page": per_page, "page": page}
            payload = self._get_json(
                path,
                params=page_params,
                allow_not_found=allow_not_found,
                allow_conflict=allow_conflict,
            )
            if payload is None:
                return items
            if not isinstance(payload, list):
                raise RuntimeError(f"Expected a list response from {path}, got {type(payload).__name__}.")
            if not payload:
                return items

            items.extend(payload)
            if len(payload) < per_page:
                return items
            page += 1

    def execute_graphql(self, query: str, variables: dict[str, object]) -> dict[str, Any]:
        response = self._session.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"GitHub GraphQL API returned HTTP {response.status_code}: {response.text}"
            )

        payload = response.json()
        if payload.get("errors"):
            messages = ", ".join(error.get("message", "Unknown GraphQL error") for error in payload["errors"])
            raise RuntimeError(f"GitHub GraphQL API error: {messages}")

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("GitHub GraphQL API returned an unexpected response body.")
        return data

    def fetch_viewer_login(self) -> str:
        if self._viewer_login is None:
            payload = self._get_json("/user")
            self._viewer_login = payload["login"]
        return self._viewer_login

    def fetch_user_created_at(self, username: str) -> datetime:
        viewer_login = self.fetch_viewer_login()
        if username.casefold() == viewer_login.casefold():
            payload = self._get_json("/user")
        else:
            payload = self._get_json(f"/users/{username}", allow_not_found=True)
            if payload is None:
                raise RuntimeError(f"GitHub user `{username}` was not found.")
        return parse_github_datetime(payload["created_at"])

    def _repository_from_payload(self, payload: dict[str, Any]) -> RepositoryRef | None:
        default_branch = payload.get("default_branch")
        if default_branch is None:
            return None
        return RepositoryRef(
            name_with_owner=payload["full_name"],
            owner_login=payload["owner"]["login"],
            is_private=bool(payload["private"]),
            default_branch=default_branch,
        )

    def _dedupe_repositories(self, payloads: list[dict[str, Any]]) -> list[RepositoryRef]:
        repositories: list[RepositoryRef] = []
        seen: set[str] = set()
        for payload in payloads:
            repository = self._repository_from_payload(payload)
            if repository is None or repository.name_with_owner in seen:
                continue
            repositories.append(repository)
            seen.add(repository.name_with_owner)
        return repositories

    def list_repositories_for_user(
        self,
        username: str,
        include_private: bool,
    ) -> list[RepositoryRef]:
        viewer_login = self.fetch_viewer_login()

        if username.casefold() == viewer_login.casefold():
            visibility = "all" if include_private else "public"
            payloads = self._paginate(
                "/user/repos",
                {
                    "visibility": visibility,
                    "affiliation": "owner,collaborator,organization_member",
                    "sort": "updated",
                    "per_page": 100,
                },
            )
            return [
                repo
                for repo in self._dedupe_repositories(payloads)
                if repo.owner_login.casefold() == username.casefold()
            ]

        public_payloads = self._paginate(
            f"/users/{username}/repos",
            {
                "type": "owner",
                "sort": "updated",
                "per_page": 100,
            },
            allow_not_found=True,
        )
        if public_payloads is None:
            raise RuntimeError(f"GitHub user `{username}` was not found.")

        if not include_private:
            return self._dedupe_repositories(public_payloads)

        accessible_payloads = self._paginate(
            "/user/repos",
            {
                "visibility": "all",
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "per_page": 100,
            },
        )
        extra_payloads = [
            payload
            for payload in accessible_payloads
            if payload["owner"]["login"].casefold() == username.casefold()
        ]
        return [
            repo
            for repo in self._dedupe_repositories(public_payloads + extra_payloads)
            if repo.owner_login.casefold() == username.casefold()
        ]

    def iter_repository_commits(
        self,
        repository: RepositoryRef,
        username: str,
        from_datetime: datetime,
        to_datetime: datetime,
    ) -> list[dict[str, Any]]:
        return self._paginate(
            f"/repos/{repository.name_with_owner}/commits",
            {
                "author": username,
                "since": from_datetime.isoformat(),
                "until": to_datetime.isoformat(),
                "per_page": 100,
            },
            allow_conflict=True,
        )

    def fetch_other_repository_contribution_counts(
        self,
        username: str,
        start_month: date,
        end_month: date,
        include_private: bool = False,
        max_repositories: int = 100,
    ) -> dict[date, dict[str, int]]:
        monthly_counts: dict[date, dict[str, int]] = {}

        for window in iter_month_windows(start_month, end_month):
            data = self.execute_graphql(
                MONTHLY_COMMITS_QUERY,
                {
                    "username": username,
                    "from": window.from_datetime.isoformat(),
                    "to": window.to_datetime.isoformat(),
                    "maxRepositories": max_repositories,
                },
            )
            user = data.get("user")
            if user is None:
                raise RuntimeError(f"GitHub user `{username}` was not found.")

            repositories = user["contributionsCollection"]["commitContributionsByRepository"]
            month_totals: dict[str, int] = {}
            for repository_entry in repositories:
                repository_name = repository_entry["repository"]["nameWithOwner"]
                owner_login = repository_name.split("/", 1)[0]
                is_private = bool(repository_entry["repository"]["isPrivate"])
                if owner_login.casefold() == username.casefold():
                    continue
                if is_private and not include_private:
                    continue

                commit_total = sum(node["commitCount"] for node in repository_entry["contributions"]["nodes"])
                if commit_total > 0:
                    month_totals[repository_name] = month_totals.get(repository_name, 0) + commit_total

            monthly_counts[window.month_start] = month_totals

        return monthly_counts

    def fetch_monthly_commit_counts(
        self,
        username: str,
        start_month: date,
        end_month: date,
        include_private: bool = False,
    ) -> dict[date, dict[str, int]]:
        windows = iter_month_windows(start_month, end_month)
        monthly_counts: dict[date, dict[str, int]] = {
            window.month_start: {} for window in windows
        }
        repositories = self.list_repositories_for_user(
            username=username,
            include_private=include_private,
        )
        if not repositories:
            return monthly_counts

        from_datetime = windows[0].from_datetime
        to_datetime = windows[-1].to_datetime

        for repository in repositories:
            commits = self.iter_repository_commits(
                repository=repository,
                username=username,
                from_datetime=from_datetime,
                to_datetime=to_datetime,
            )
            for commit in commits:
                commit_meta = commit.get("commit") or {}
                author_meta = commit_meta.get("author") or {}
                committed_at = author_meta.get("date")
                if committed_at is None:
                    committer_meta = commit_meta.get("committer") or {}
                    committed_at = committer_meta.get("date")
                if committed_at is None:
                    continue

                commit_month = parse_github_datetime(committed_at).date().replace(day=1)
                month_counts = monthly_counts.get(commit_month)
                if month_counts is None:
                    continue
                month_counts[repository.name_with_owner] = month_counts.get(repository.name_with_owner, 0) + 1

        other_repository_counts = self.fetch_other_repository_contribution_counts(
            username=username,
            start_month=start_month,
            end_month=end_month,
            include_private=include_private,
        )
        for month, repository_counts in other_repository_counts.items():
            month_counts = monthly_counts.setdefault(month, {})
            for repository_name, commit_count in repository_counts.items():
                month_counts[repository_name] = month_counts.get(repository_name, 0) + commit_count

        return monthly_counts


GitHubGraphQLClient = GitHubClient
