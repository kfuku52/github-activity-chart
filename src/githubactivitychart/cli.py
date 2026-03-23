from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .github_api import GitHubClient
from .plotting import render_stacked_bar_chart

MONTH_FORMAT = "%Y-%m"


def parse_month(value: str) -> date:
    try:
        return datetime.strptime(value, MONTH_FORMAT).date().replace(day=1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid month `{value}`. Use YYYY-MM.") from exc


def resolve_end_month(
    to_month: date | None,
    today: date | None = None,
) -> date:
    today = today or datetime.now(timezone.utc).date()
    current_month = today.replace(day=1)
    to_month = to_month or current_month
    if to_month > current_month:
        raise ValueError("--to cannot be in the future.")
    return to_month


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot monthly GitHub commit activity as a stacked bar chart by repository.",
    )
    parser.add_argument("username", help="GitHub account name")
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
    parser.add_argument(
        "--top-repos",
        type=int,
        default=None,
        help="Keep the top N repositories by total commits and group the rest into Other",
    )
    parser.add_argument(
        "--include-private",
        action="store_true",
        help="Include private repositories in the chart",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/monthly_commits.pdf"),
        help="Path to the output chart file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        client = GitHubClient.from_environment()
        end_month = resolve_end_month(args.to_month)
        start_month = args.from_month
        if start_month is None:
            created_at = client.fetch_user_created_at(args.username)
            start_month = date(created_at.year, created_at.month, 1)
        if start_month > end_month:
            raise ValueError("--from must not be later than --to.")
        monthly_counts = client.fetch_monthly_commit_counts(
            username=args.username,
            start_month=start_month,
            end_month=end_month,
            include_private=args.include_private,
        )
        if args.from_month is None:
            non_empty_months = [month for month, counts in sorted(monthly_counts.items()) if counts]
            if not non_empty_months:
                raise RuntimeError(f"No commits were found for `{args.username}` in accessible repositories.")
            first_month = non_empty_months[0]
            monthly_counts = {
                month: counts
                for month, counts in monthly_counts.items()
                if month >= first_month
            }
        output_path = render_stacked_bar_chart(
            monthly_counts=monthly_counts,
            username=args.username,
            output_path=args.output,
            top_repos=args.top_repos,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved chart to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
