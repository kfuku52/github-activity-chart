"""Generate the repository's public examples from one contribution-data fetch."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .cli import print_progress, render_outputs_atomically
from .github_api import GitHubAPIError, GitHubClient, iter_month_windows
from .plotting import DEFAULT_TOP_REPOSITORIES, ChartRenderError


def generate_public_charts(username: str, output_dir: Path, *, now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("`now` must include timezone information.")
    now = now.astimezone(UTC)
    end_month = (now.date().replace(day=1) - timedelta(days=1)).replace(day=1)
    client = GitHubClient.from_environment()
    created_at = client.fetch_user_created_at(username)
    counts = client.fetch_monthly_commit_counts(
        username,
        created_at.date().replace(day=1),
        end_month,
        include_private=False,
        progress_callback=print_progress,
        now=now,
    )
    first_month = min((month for month, repositories in counts.items() if repositories), default=None)
    if first_month is None:
        raise RuntimeError(f"No commits were found for `{username}` in accessible repositories.")
    full_counts = {month: repositories for month, repositories in counts.items() if month >= first_month}
    recent_year, recent_month = divmod(end_month.year * 12 + end_month.month - 1 - 35, 12)
    recent_counts = {
        window.month_start: counts.get(window.month_start, {})
        for window in iter_month_windows(date(recent_year, recent_month + 1, 1), end_month, now=now)
    }
    outputs = render_outputs_atomically(
        monthly_counts=full_counts,
        username=username,
        output_paths=[output_dir / f"{username}_all_months.{suffix}" for suffix in ("png", "pdf")],
        top_repos=DEFAULT_TOP_REPOSITORIES,
        as_of=None,
    )
    outputs += render_outputs_atomically(
        monthly_counts=recent_counts,
        username=username,
        output_paths=[output_dir / f"{username}_last_36_months.svg"],
        top_repos=DEFAULT_TOP_REPOSITORIES,
        as_of=None,
    )
    return outputs


def main() -> int:
    try:
        outputs = generate_public_charts("kfuku52", Path("output"))
    except (ChartRenderError, GitHubAPIError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for output in outputs:
        print(f"Saved chart to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
