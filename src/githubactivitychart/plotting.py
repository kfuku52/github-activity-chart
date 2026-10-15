from __future__ import annotations

import hashlib
from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matplotlib.figure import Figure

FONT_SIZE_PT = 8
DEFAULT_TOP_REPOSITORIES = 20
SUPPORTED_OUTPUT_SUFFIXES = frozenset({".pdf", ".png", ".svg"})
SERIES_COLORS = [
    "#4477AA",
    "#EE6677",
    "#228833",
    "#CCBB44",
    "#66CCEE",
    "#AA3377",
    "#332288",
    "#DDCC77",
    "#117733",
    "#88CCEE",
    "#CC6677",
    "#AA4499",
]
SERIES_HATCHES = ["", "//", "\\\\", "xx", "++", "..", "oo", "**", "--", "||"]


class ChartRenderError(ValueError):
    """The supplied chart data or output target cannot be rendered safely."""


def _add_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def _normalize_monthly_counts(
    monthly_counts: dict[date, dict[str, int]],
) -> dict[date, dict[str, int]]:
    if not isinstance(monthly_counts, dict) or not monthly_counts:
        raise ChartRenderError("No monthly contribution data was supplied.")

    validated: dict[date, dict[str, int]] = {}
    for month, counts in monthly_counts.items():
        if not isinstance(month, date) or isinstance(month, datetime) or month.day != 1:
            raise ChartRenderError("Every month key must be a first-of-month date.")
        if not isinstance(counts, dict):
            raise ChartRenderError("Every month value must be a repository-count mapping.")

        validated_counts: dict[str, int] = {}
        for repository, commit_count in counts.items():
            if not isinstance(repository, str) or not repository.strip():
                raise ChartRenderError("Repository labels must be non-empty strings.")
            if isinstance(commit_count, bool) or not isinstance(commit_count, int):
                raise ChartRenderError("Contribution counts must be integers.")
            if commit_count < 0:
                raise ChartRenderError("Contribution counts must not be negative.")
            if commit_count:
                validated_counts[repository] = commit_count
        validated[month] = validated_counts

    first_month = min(validated)
    last_month = max(validated)
    normalized: dict[date, dict[str, int]] = {}
    month = first_month
    while month <= last_month:
        normalized[month] = dict(validated.get(month, {}))
        month = _add_month(month)

    if not any(normalized.values()):
        raise ChartRenderError("No commit contributions were found for the selected period.")
    return normalized


def collapse_repositories(
    monthly_counts: dict[date, dict[str, int]],
    top_repos: int | None,
) -> dict[date, dict[str, int]]:
    if top_repos is None:
        return {month: dict(counts) for month, counts in monthly_counts.items()}
    if isinstance(top_repos, bool) or not isinstance(top_repos, int) or top_repos < 1:
        raise ChartRenderError("--top-repos must be an integer of 1 or greater.")

    repository_totals = _repository_totals(monthly_counts)
    keep = {
        repository
        for repository, _ in sorted(
            repository_totals.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top_repos]
    }

    collapsed: dict[date, dict[str, int]] = {}
    for month, month_counts in monthly_counts.items():
        visible_counts: dict[str, int] = {}
        other_total = 0
        for repository, commit_count in month_counts.items():
            if repository in keep:
                visible_counts[repository] = commit_count
            else:
                other_total += commit_count
        if other_total:
            visible_counts["Other"] = other_total
        collapsed[month] = visible_counts
    return collapsed


def _ordered_repositories(monthly_counts: dict[date, dict[str, int]]) -> list[str]:
    repository_totals = _repository_totals(monthly_counts)
    return [
        repository
        for repository, _ in sorted(
            repository_totals.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _repository_totals(monthly_counts: dict[date, dict[str, int]]) -> dict[str, int]:
    repository_totals: dict[str, int] = defaultdict(int)
    for month_counts in monthly_counts.values():
        for repository, commit_count in month_counts.items():
            repository_totals[repository] += commit_count
    return dict(repository_totals)


def _build_tick_positions_and_labels(months: list[date]) -> tuple[list[int], list[str]]:
    if len(months) <= 24:
        return list(range(len(months))), [
            month.strftime("%Y Jan") if month.month == 1 else month.strftime("%Y-%m") for month in months
        ]

    positions = [index for index, month in enumerate(months) if month.month == 1]
    labels = [f"{months[index].year} Jan" for index in positions]
    if positions:
        return positions, labels
    return list(range(len(months))), [month.strftime("%Y-%m") for month in months]


def _series_style(repository: str) -> tuple[str, str]:
    digest = hashlib.sha256(repository.encode("utf-8")).digest()
    color = SERIES_COLORS[int.from_bytes(digest[:2], "big") % len(SERIES_COLORS)]
    hatch = SERIES_HATCHES[int.from_bytes(digest[2:4], "big") % len(SERIES_HATCHES)]
    return color, hatch


def _legend_label(repository: str, total: int) -> str:
    if len(repository) <= 54:
        shortened = repository
    else:
        shortened = f"{repository[:26]}…{repository[-27:]}"
    return f"{shortened} ({total})"


def _output_metadata(output_suffix: str) -> dict[str, object]:
    if output_suffix == ".pdf":
        return {
            "Creator": "github-activity-chart",
            "CreationDate": None,
            "ModDate": None,
        }
    if output_suffix == ".svg":
        return {"Date": None}
    return {"Software": "github-activity-chart"}


def _build_chart_figure(
    monthly_counts: dict[date, dict[str, int]],
    username: str,
    top_repos: int | None = DEFAULT_TOP_REPOSITORIES,
    as_of: datetime | None = None,
) -> Figure:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator

    if not isinstance(username, str) or not username.strip():
        raise ChartRenderError("GitHub username must be a non-empty string.")

    validated_counts = _normalize_monthly_counts(monthly_counts)
    normalized_counts = collapse_repositories(
        validated_counts,
        top_repos=top_repos,
    )
    repository_totals = _repository_totals(normalized_counts)
    repositories = sorted(repository_totals, key=lambda repository: (-repository_totals[repository], repository))
    months = sorted(normalized_counts)

    if as_of is not None:
        if as_of.tzinfo is None:
            raise ChartRenderError("The chart as-of time must include a timezone.")
        as_of = as_of.astimezone(UTC)
        if as_of.date().replace(day=1) != months[-1]:
            raise ChartRenderError("The chart as-of time must fall within the final displayed month.")

    x_positions = list(range(len(months)))
    stacked_base = [0] * len(months)
    tick_positions, tick_labels = _build_tick_positions_and_labels(months)
    figure_width = min(16.0, max(8.0, 6.0 + len(months) * 0.08))
    figure_height = min(3.5, max(2.5, 1.9 + len(repositories) * 0.08))
    figure = Figure(
        figsize=(figure_width, figure_height),
        constrained_layout=True,
    )
    FigureCanvasAgg(figure)

    try:
        axis = figure.subplots()
        for repository in repositories:
            heights = [normalized_counts[month].get(repository, 0) for month in months]
            color, hatch = _series_style(repository)
            axis.bar(
                x_positions,
                heights,
                bottom=stacked_base,
                label=_legend_label(repository, repository_totals[repository]),
                color=color,
                hatch=hatch,
                edgecolor="#222222",
                linewidth=0.25,
                width=0.82,
            )
            stacked_base = [base + height for base, height in zip(stacked_base, heights, strict=True)]

        start_label = months[0].strftime("%Y-%m")
        end_label = months[-1].strftime("%Y-%m")
        period_label = f"{start_label} to {end_label}"
        if as_of is not None:
            period_label += f" (month to date; as of {as_of:%Y-%m-%d %H:%M UTC})"
        total_commits = sum(stacked_base)

        axis.set_title(
            f"Monthly GitHub Commit Contributions by Repository for {username}\n"
            f"{period_label} | Total contributions: {total_commits}",
            fontsize=FONT_SIZE_PT,
        )
        axis.set_ylabel("Commit contributions", fontsize=FONT_SIZE_PT)
        axis.set_xticks(tick_positions)
        axis.set_xticklabels(
            tick_labels,
            rotation=90,
            ha="center",
            va="top",
            fontsize=FONT_SIZE_PT,
        )
        axis.tick_params(axis="y", labelsize=FONT_SIZE_PT)
        axis.yaxis.set_major_locator(MaxNLocator(integer=True))
        axis.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.4)
        axis.set_axisbelow(True)
        axis.legend(
            title="Repository (Total contributions)",
            bbox_to_anchor=(1.01, 1),
            loc="upper left",
            borderaxespad=0.0,
            labelspacing=0.3,
            fontsize=FONT_SIZE_PT,
            title_fontsize=FONT_SIZE_PT,
        )
        max_total = max(stacked_base)
        y_padding = max(1, max_total * 0.04)
        axis.set_ylim(0, max_total + y_padding)
        axis.set_xlim(-0.5, len(months) - 0.5)

        return figure
    except Exception:
        figure.clear()
        raise


def render_stacked_bar_charts(
    monthly_counts: dict[date, dict[str, int]],
    username: str,
    output_paths: list[Path],
    top_repos: int | None = DEFAULT_TOP_REPOSITORIES,
    as_of: datetime | None = None,
) -> list[Path]:
    """Build a chart once and export it to every requested destination."""
    from matplotlib import rc_context

    for output_path in output_paths:
        if output_path.suffix.casefold() not in SUPPORTED_OUTPUT_SUFFIXES:
            supported = ", ".join(sorted(SUPPORTED_OUTPUT_SUFFIXES))
            raise ChartRenderError(f"Unsupported output format for `{output_path}`. Use one of: {supported}.")
    if not output_paths:
        return output_paths

    figure = _build_chart_figure(monthly_counts, username, top_repos, as_of)
    axis = figure.axes[0]
    initial_position = axis.get_position().frozen()
    try:
        for output_path in output_paths:
            suffix = output_path.suffix.casefold()
            # Each backend must start from the same geometry as a fresh figure.
            axis.set_position(initial_position)
            axis.set_in_layout(True)
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                deterministic_svg = (
                    rc_context({"svg.hashsalt": "github-activity-chart"}) if suffix == ".svg" else nullcontext()
                )
                with deterministic_svg:
                    figure.savefig(
                        output_path,
                        format=suffix.removeprefix("."),
                        dpi=200,
                        bbox_inches="tight",
                        metadata=_output_metadata(suffix),
                    )
            except (OSError, ValueError) as exc:
                raise ChartRenderError(f"Could not render `{output_path}`: {exc}") from exc
    finally:
        figure.clear()
    return output_paths


def render_stacked_bar_chart(
    monthly_counts: dict[date, dict[str, int]],
    username: str,
    output_path: Path,
    top_repos: int | None = DEFAULT_TOP_REPOSITORIES,
    as_of: datetime | None = None,
) -> Path:
    render_stacked_bar_charts(monthly_counts, username, [output_path], top_repos, as_of)
    return output_path
