from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from .github_api import GitHubAPIError, GitHubClient
from .plotting import (
    DEFAULT_TOP_REPOSITORIES,
    SUPPORTED_OUTPUT_SUFFIXES,
    ChartRenderError,
    render_stacked_bar_chart,
)

MIN_GITHUB_MONTH = date(2008, 1, 1)
USERNAME_PATTERN = re.compile(r"(?=.{1,39}\Z)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z")


def parse_month(value: str) -> date:
    if not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"Invalid month `{value}`. Use YYYY-MM.")
    try:
        parsed = date(int(value[:4]), int(value[5:7]), 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid month `{value}`. Use YYYY-MM.") from exc
    if parsed < MIN_GITHUB_MONTH:
        raise argparse.ArgumentTypeError(
            f"Invalid month `{value}`. GitHub activity cannot predate {MIN_GITHUB_MONTH:%Y-%m}."
        )
    return parsed


def github_username(value: str) -> str:
    if USERNAME_PATTERN.fullmatch(value) is None:
        raise argparse.ArgumentTypeError(
            "Invalid GitHub username. Use 1-39 letters, digits, or single hyphens; a hyphen cannot be first or last."
        )
    return value


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid integer `{value}`.") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Value must be 1 or greater.")
    return parsed


def contribution_repository_limit(value: str) -> int:
    parsed = positive_int(value)
    if parsed > 100:
        raise argparse.ArgumentTypeError("Value must not exceed GitHub's limit of 100.")
    return parsed


def print_progress(message: str) -> None:
    print(message, file=sys.stderr)


def resolve_end_month(
    to_month: date | None,
    today: date | None = None,
) -> date:
    today = today or datetime.now(UTC).date()
    current_month = today.replace(day=1)
    to_month = to_month or current_month
    if to_month > current_month:
        raise ValueError("--to cannot be in the future.")
    return to_month


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot monthly GitHub commit activity as a stacked bar chart by repository.",
    )
    parser.add_argument("username", type=github_username, help="GitHub account name")
    parser.add_argument(
        "--from",
        dest="from_month",
        type=parse_month,
        help="Start month in YYYY-MM format",
    )
    parser.add_argument(
        "--to",
        dest="to_month",
        type=parse_month,
        help="End month in YYYY-MM format",
    )
    repository_display_group = parser.add_mutually_exclusive_group()
    repository_display_group.add_argument(
        "--top-repos",
        type=positive_int,
        default=DEFAULT_TOP_REPOSITORIES,
        help=("Keep the top N repositories by total commit contributions and group the rest into Other"),
    )
    repository_display_group.add_argument(
        "--all-repositories",
        dest="top_repos",
        action="store_const",
        const=None,
        help="Display every repository (large legends may be difficult to read)",
    )
    parser.add_argument(
        "--max-contribution-repositories",
        type=contribution_repository_limit,
        default=100,
        help=("Maximum repositories to request from GitHub contribution data per month (1-100)"),
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="Include private repositories in the chart",
    )
    parser.add_argument(
        "--show-private-names",
        action="store_true",
        help=("Show private repository names instead of the safe aggregated label; requires --include-private"),
    )
    parser.add_argument(
        "--output",
        dest="output_paths",
        type=Path,
        action="append",
        help="Path to an output chart file. Can be used multiple times.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print a traceback for expected operational errors.",
    )
    return parser


def validate_output_paths(output_paths: list[Path]) -> list[Path]:
    normalized: list[Path] = []
    seen: set[Path] = set()
    supported = ", ".join(sorted(SUPPORTED_OUTPUT_SUFFIXES))
    for output_path in output_paths:
        if output_path.suffix.casefold() not in SUPPORTED_OUTPUT_SUFFIXES:
            raise ValueError(f"Unsupported output format for `{output_path}`. Use one of: {supported}.")
        absolute_path = output_path.expanduser().absolute()
        if absolute_path in seen:
            raise ValueError(f"Duplicate output path: `{output_path}`.")
        seen.add(absolute_path)
        normalized.append(output_path)
    return normalized


def render_outputs_atomically(
    *,
    monthly_counts: dict[date, dict[str, int]],
    username: str,
    output_paths: list[Path],
    top_repos: int | None,
    as_of: datetime | None,
) -> list[Path]:
    transaction_id = uuid.uuid4().hex
    staged: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []

    try:
        for output_path in output_paths:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = output_path.with_name(
                f".{output_path.stem}.{transaction_id}.tmp{output_path.suffix.casefold()}"
            )
            staged.append((temporary_path, output_path))
            render_stacked_bar_chart(
                monthly_counts=monthly_counts,
                username=username,
                output_path=temporary_path,
                top_repos=top_repos,
                as_of=as_of,
            )

        for temporary_path, output_path in staged:
            if output_path.exists():
                backup_path = output_path.with_name(f".{output_path.name}.{transaction_id}.bak")
                os.replace(output_path, backup_path)
                backups.append((backup_path, output_path))
            os.replace(temporary_path, output_path)
            installed.append(output_path)
    except Exception:
        for output_path in reversed(installed):
            output_path.unlink(missing_ok=True)
        for backup_path, output_path in reversed(backups):
            if backup_path.exists():
                os.replace(backup_path, output_path)
        raise
    finally:
        for temporary_path, _ in staged:
            temporary_path.unlink(missing_ok=True)

    for backup_path, _ in backups:
        backup_path.unlink(missing_ok=True)
    return output_paths


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.show_private_names and not args.include_private:
            raise ValueError("--show-private-names requires --include-private.")

        now = datetime.now(UTC)
        end_month = resolve_end_month(args.to_month, today=now.date())
        start_month = args.from_month
        if start_month is not None and start_month > end_month:
            raise ValueError("--from must not be later than --to.")
        output_paths = validate_output_paths(args.output_paths or [Path("output/monthly_commits.pdf")])

        client = GitHubClient.from_environment()
        if start_month is None:
            created_at = client.fetch_user_created_at(args.username)
            start_month = date(created_at.year, created_at.month, 1)
        if start_month > end_month:
            raise ValueError("--from must not be later than --to.")

        progress_callback = None if args.quiet else print_progress

        monthly_counts = client.fetch_monthly_commit_counts(
            username=args.username,
            start_month=start_month,
            end_month=end_month,
            include_private=args.include_private,
            show_private_names=args.show_private_names,
            max_contribution_repositories=args.max_contribution_repositories,
            progress_callback=progress_callback,
            now=now,
        )
        if args.from_month is None:
            non_empty_months = [month for month, counts in sorted(monthly_counts.items()) if counts]
            if not non_empty_months:
                raise RuntimeError(f"No commits were found for `{args.username}` in accessible repositories.")
            first_month = non_empty_months[0]
            monthly_counts = {month: counts for month, counts in monthly_counts.items() if month >= first_month}
        current_month = now.date().replace(day=1)
        rendered_output_paths = render_outputs_atomically(
            monthly_counts=monthly_counts,
            username=args.username,
            output_paths=output_paths,
            top_repos=args.top_repos,
            as_of=now if end_month == current_month else None,
        )
    except (ChartRenderError, GitHubAPIError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        return 1

    for output_path in rendered_output_paths:
        print(f"Saved chart to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
