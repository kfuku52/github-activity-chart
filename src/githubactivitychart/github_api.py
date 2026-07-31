from __future__ import annotations

import email.utils
import os
import random
import shutil
import subprocess
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote

import requests

GITHUB_HOSTNAME = "github.com"
GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = f"{GITHUB_API_URL}/graphql"
REQUEST_TIMEOUT_SECONDS = 30
GH_COMMAND_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 300
MIN_SECONDARY_RATE_LIMIT_WAIT_SECONDS = 60
MAX_CONTRIBUTION_REPOSITORIES = 100
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
PRIVATE_REPOSITORY_LABEL = "Private repositories"

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
          pageInfo {
            hasNextPage
          }
          nodes {
            commitCount
          }
        }
      }
    }
  }
}
""".strip()


class GitHubAPIError(RuntimeError):
    """An expected GitHub authentication, transport, or API failure."""


class IncompleteContributionDataError(GitHubAPIError):
    """GitHub reached a result cap, so publishing the chart would be misleading."""


@dataclass(frozen=True)
class MonthWindow:
    month_start: date
    from_datetime: datetime
    to_datetime: datetime


ProgressCallback = Callable[[str], None]


def add_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def parse_github_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("GitHub returned a datetime without a timezone.")
    return parsed


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if value is None:
        return None

    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        delay = (retry_at.astimezone(UTC) - now).total_seconds()

    return max(0.0, delay)


def build_month_window(
    month_start: date,
    now: datetime | None = None,
) -> MonthWindow:
    if month_start.day != 1:
        raise ValueError("Month boundaries must be the first day of each month.")

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("`now` must include timezone information.")
    now = now.astimezone(UTC)
    current_month = date(now.year, now.month, 1)
    if month_start > current_month:
        raise ValueError("The month cannot be in the future.")

    from_datetime = datetime.combine(month_start, time.min, tzinfo=UTC)
    if month_start == current_month:
        to_datetime = now
    else:
        to_datetime = datetime.combine(
            add_month(month_start),
            time.min,
            tzinfo=UTC,
        ) - timedelta(microseconds=1)

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

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("`now` must include timezone information.")
    current_month = now.astimezone(UTC).date().replace(day=1)
    if end_month > current_month:
        raise ValueError("The end month cannot be in the future.")

    windows: list[MonthWindow] = []
    month = start_month
    while month <= end_month:
        windows.append(build_month_window(month, now=now))
        month = add_month(month)
    return windows


def resolve_github_token() -> str:
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return token

    gh_host = os.getenv("GH_HOST")
    gh_token = os.getenv("GH_TOKEN")
    if gh_token:
        if gh_host and gh_host.casefold() != GITHUB_HOSTNAME:
            raise GitHubAPIError(
                "GH_TOKEN is configured for a non-github.com GH_HOST. Use GITHUB_TOKEN for github.com or clear GH_HOST."
            )
        return gh_token

    gh_path = shutil.which("gh")
    if gh_path:
        try:
            result = subprocess.run(
                [gh_path, "auth", "token", "--hostname", GITHUB_HOSTNAME],
                check=False,
                capture_output=True,
                text=True,
                timeout=GH_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubAPIError(f"`gh auth token --hostname {GITHUB_HOSTNAME}` timed out.") from exc
        token = result.stdout.strip()
        if result.returncode == 0 and token:
            return token

    raise GitHubAPIError(
        "GitHub token not found. Set GITHUB_TOKEN/GH_TOKEN or run `gh auth login --hostname github.com` first."
    )


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        sleep: Callable[[float], None] = time_module.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        max_retries: int = MAX_RETRIES,
        max_rate_limit_wait_seconds: float = MAX_RATE_LIMIT_WAIT_SECONDS,
    ) -> None:
        if not token:
            raise ValueError("GitHub token must not be empty.")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-activity-chart",
            }
        )
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._max_retries = max_retries
        self._max_rate_limit_wait_seconds = max_rate_limit_wait_seconds

    @classmethod
    def from_environment(cls) -> GitHubClient:
        return cls(resolve_github_token())

    def _transient_backoff_delay(self, attempt: int) -> float:
        base = min(2.0**attempt, 10.0)
        return base + self._random_uniform(0.0, min(1.0, base * 0.25))

    def _secondary_rate_limit_delay(self, attempt: int) -> float:
        base = MIN_SECONDARY_RATE_LIMIT_WAIT_SECONDS * (2.0**attempt)
        return base + self._random_uniform(0.0, min(10.0, base * 0.1))

    def _rate_limit_header_delay(self, response: requests.Response) -> float | None:
        retry_after = parse_retry_after(response.headers.get("Retry-After"))
        if retry_after is not None:
            return retry_after

        if response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            if reset is not None:
                try:
                    reset_epoch = int(reset)
                except ValueError:
                    return None
                return max(0.0, reset_epoch - time_module.time() + 1)
        return None

    def _rate_limit_delay(self, response: requests.Response) -> float | None:
        if response.status_code not in {403, 429}:
            return None
        return self._rate_limit_header_delay(response)

    @staticmethod
    def _looks_like_secondary_rate_limit(response: requests.Response) -> bool:
        if response.status_code not in {403, 429}:
            return False
        response_text = response.text.casefold()
        return (
            "secondary rate limit" in response_text
            or "rate limit exceeded" in response_text
            or "too many requests" in response_text
        )

    @staticmethod
    def _graphql_rate_limited(payload: object) -> bool:
        if not isinstance(payload, dict):
            return False
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return False
        return any(
            isinstance(error, dict)
            and (error.get("type") == "RATE_LIMITED" or "rate limit" in str(error.get("message", "")).casefold())
            for error in errors
        )

    def _wait_or_raise(
        self,
        *,
        description: str,
        response: requests.Response,
        delay_seconds: float,
    ) -> None:
        if delay_seconds > self._max_rate_limit_wait_seconds:
            reset = response.headers.get("X-RateLimit-Reset")
            reset_detail = ""
            if reset is not None:
                try:
                    reset_at = datetime.fromtimestamp(int(reset), tz=UTC)
                except ValueError:
                    reset_detail = ""
                else:
                    reset_detail = f" Reset at {reset_at.isoformat()}."
            raise GitHubAPIError(
                f"GitHub rate limit reached for {description}; retry after about "
                f"{int(delay_seconds)} seconds.{reset_detail}"
            )
        self._sleep(delay_seconds)

    def _send_with_retries(
        self,
        request: Callable[[], requests.Response],
        description: str,
    ) -> requests.Response:
        last_transport_error: requests.RequestException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = request()
            except requests.RequestException as exc:
                last_transport_error = exc
                if attempt >= self._max_retries:
                    break
                self._sleep(self._transient_backoff_delay(attempt))
                continue

            if attempt >= self._max_retries:
                return response

            rate_limit_delay = self._rate_limit_delay(response)
            if rate_limit_delay is not None:
                self._wait_or_raise(
                    description=description,
                    response=response,
                    delay_seconds=rate_limit_delay,
                )
                continue

            if self._looks_like_secondary_rate_limit(response):
                self._wait_or_raise(
                    description=description,
                    response=response,
                    delay_seconds=self._secondary_rate_limit_delay(attempt),
                )
                continue

            if response.status_code in RETRY_STATUS_CODES:
                self._sleep(self._transient_backoff_delay(attempt))
                continue
            return response

        raise GitHubAPIError(
            f"GitHub request failed after {self._max_retries + 1} attempts for "
            f"{description}: {type(last_transport_error).__name__}."
        ) from last_transport_error

    @staticmethod
    def _request_id(response: requests.Response) -> str:
        request_id = response.headers.get("X-GitHub-Request-Id")
        return f" Request ID: {request_id}." if request_id else ""

    @staticmethod
    def _decode_json(response: requests.Response, description: str) -> Any:
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            raise GitHubAPIError(f"GitHub returned invalid JSON for {description}.") from exc

    def _request(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> requests.Response | None:
        response = self._send_with_retries(
            lambda: self._session.get(
                f"{GITHUB_API_URL}{path}",
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            ),
            "REST API",
        )
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise GitHubAPIError(f"GitHub REST API returned HTTP {response.status_code}.{self._request_id(response)}")
        return response

    def _get_json(
        self,
        path: str,
        params: dict[str, object] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> Any:
        response = self._request(
            path,
            params=params,
            allow_not_found=allow_not_found,
        )
        if response is None:
            return None
        return self._decode_json(response, "REST API")

    def execute_graphql(
        self,
        query: str,
        variables: dict[str, object],
    ) -> dict[str, Any]:
        for graphql_attempt in range(self._max_retries + 1):
            response = self._send_with_retries(
                lambda: self._session.post(
                    GITHUB_GRAPHQL_URL,
                    json={"query": query, "variables": variables},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ),
                "GraphQL API",
            )
            if response.status_code != 200:
                raise GitHubAPIError(
                    f"GitHub GraphQL API returned HTTP {response.status_code}.{self._request_id(response)}"
                )

            payload = self._decode_json(response, "GraphQL API")
            if self._graphql_rate_limited(payload):
                if graphql_attempt >= self._max_retries:
                    raise GitHubAPIError(
                        f"GitHub GraphQL API remained rate limited after retries.{self._request_id(response)}"
                    )
                delay = self._rate_limit_header_delay(response)
                if delay is None:
                    delay = self._secondary_rate_limit_delay(graphql_attempt)
                self._wait_or_raise(
                    description="GraphQL API",
                    response=response,
                    delay_seconds=delay,
                )
                continue

            if not isinstance(payload, dict):
                raise GitHubAPIError("GitHub GraphQL API returned an unexpected response body.")
            errors = payload.get("errors")
            if errors:
                raise GitHubAPIError(f"GitHub GraphQL API rejected the contribution query.{self._request_id(response)}")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise GitHubAPIError("GitHub GraphQL API returned an unexpected response body.")
            return data

        raise AssertionError("GraphQL retry loop terminated unexpectedly.")

    def fetch_user_created_at(self, username: str) -> datetime:
        escaped_username = quote(username, safe="")
        payload = self._get_json(
            f"/users/{escaped_username}",
            allow_not_found=True,
        )
        if payload is None:
            raise GitHubAPIError(f"GitHub user `{username}` was not found.")
        try:
            created_at = payload["created_at"]
        except (KeyError, TypeError) as exc:
            raise GitHubAPIError("GitHub returned incomplete user metadata.") from exc
        return parse_github_datetime(created_at)

    def validate_private_access(self) -> None:
        response = self._request("/user")
        if response is None:
            raise AssertionError("Authenticated user request unexpectedly returned no response.")

        raw_scopes = response.headers.get("X-OAuth-Scopes")
        if raw_scopes is None or not raw_scopes.strip():
            return
        scopes = {scope.strip().casefold() for scope in raw_scopes.split(",")}
        has_repository_access = "repo" in scopes
        has_user_access = "read:user" in scopes or "user" in scopes
        if not has_repository_access or not has_user_access:
            missing = []
            if not has_repository_access:
                missing.append("repo")
            if not has_user_access:
                missing.append("read:user")
            raise GitHubAPIError(
                f"The classic GitHub token is missing scope(s) required for --include-private: {', '.join(missing)}."
            )

    def fetch_monthly_commit_counts(
        self,
        username: str,
        start_month: date,
        end_month: date,
        include_private: bool = False,
        show_private_names: bool = False,
        max_contribution_repositories: int = MAX_CONTRIBUTION_REPOSITORIES,
        progress_callback: ProgressCallback | None = None,
        now: datetime | None = None,
    ) -> dict[date, dict[str, int]]:
        if not 1 <= max_contribution_repositories <= MAX_CONTRIBUTION_REPOSITORIES:
            raise ValueError("max_contribution_repositories must be between 1 and 100.")
        if show_private_names and not include_private:
            raise ValueError("show_private_names requires include_private.")
        if include_private:
            self.validate_private_access()

        windows = iter_month_windows(start_month, end_month, now=now)
        monthly_counts: dict[date, dict[str, int]] = {}
        for index, window in enumerate(windows, start=1):
            if progress_callback is not None:
                progress_callback(f"Fetching contribution data for {window.month_start:%Y-%m} ({index}/{len(windows)})")
            data = self.execute_graphql(
                MONTHLY_COMMITS_QUERY,
                {
                    "username": username,
                    "from": window.from_datetime.isoformat(),
                    "to": window.to_datetime.isoformat(),
                    "maxRepositories": max_contribution_repositories,
                },
            )
            user = data.get("user")
            if user is None:
                raise GitHubAPIError(f"GitHub user `{username}` was not found.")
            try:
                repositories = user["contributionsCollection"]["commitContributionsByRepository"]
            except (KeyError, TypeError) as exc:
                raise GitHubAPIError("GitHub returned incomplete contribution data.") from exc
            if not isinstance(repositories, list):
                raise GitHubAPIError("GitHub returned malformed contribution repository data.")
            if len(repositories) >= max_contribution_repositories:
                raise IncompleteContributionDataError(
                    "GitHub contribution data reached the repository limit "
                    f"({max_contribution_repositories}) for "
                    f"{window.month_start:%Y-%m}. No chart was written because "
                    "the result may be incomplete."
                )

            month_totals: dict[str, int] = {}
            for repository_entry in repositories:
                try:
                    repository = repository_entry["repository"]
                    is_private = bool(repository["isPrivate"])
                    repository_name = str(repository["nameWithOwner"])
                    contributions = repository_entry["contributions"]
                    if contributions["pageInfo"]["hasNextPage"]:
                        raise IncompleteContributionDataError(
                            "GitHub contribution entries reached the per-repository "
                            f"limit for {window.month_start:%Y-%m}. No chart was "
                            "written because the result may be incomplete."
                        )
                    nodes = contributions["nodes"]
                    commit_total = sum(int(node["commitCount"]) for node in nodes)
                except IncompleteContributionDataError:
                    raise
                except (KeyError, TypeError, ValueError) as exc:
                    raise GitHubAPIError("GitHub returned malformed contribution data.") from exc
                if commit_total <= 0 or (is_private and not include_private):
                    continue
                if is_private and not show_private_names:
                    repository_name = PRIVATE_REPOSITORY_LABEL
                month_totals[repository_name] = month_totals.get(repository_name, 0) + commit_total
            monthly_counts[window.month_start] = month_totals

        return monthly_counts


GitHubGraphQLClient = GitHubClient
