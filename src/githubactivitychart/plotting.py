from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

FONT_SIZE_PT = 8
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
    "#44AA99",
    "#999933",
    "#882255",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
]
SERIES_HATCHES = ["", "//", "\\\\", "xx", "++", "..", "oo", "**", "--", "||"]


def collapse_repositories(
    monthly_counts: dict[date, dict[str, int]],
    top_repos: int | None,
) -> dict[date, dict[str, int]]:
    if top_repos is None:
        return monthly_counts
    if top_repos < 1:
        raise ValueError("--top-repos must be 1 or greater.")

    repository_totals: dict[str, int] = defaultdict(int)
    for month_counts in monthly_counts.values():
        for repository, commit_count in month_counts.items():
            repository_totals[repository] += commit_count

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
            month.strftime("%Y Jan") if month.month == 1 else month.strftime("%Y-%m")
            for month in months
        ]

    positions: list[int] = []
    labels: list[str] = []
    for index, month in enumerate(months):
        if month.month == 1:
            positions.append(index)
            labels.append(f"{month.year} Jan")

    if positions:
        return positions, labels

    return list(range(len(months))), [month.strftime("%Y-%m") for month in months]


def _series_style(index: int) -> tuple[str, str]:
    color = SERIES_COLORS[index % len(SERIES_COLORS)]
    hatch = SERIES_HATCHES[(index // len(SERIES_COLORS)) % len(SERIES_HATCHES)] if index >= len(SERIES_COLORS) else ""
    return color, hatch


def render_stacked_bar_chart(
    monthly_counts: dict[date, dict[str, int]],
    username: str,
    output_path: Path,
    top_repos: int | None = None,
) -> Path:
    normalized_counts = collapse_repositories(monthly_counts, top_repos=top_repos)
    repository_totals = _repository_totals(normalized_counts)
    repositories = _ordered_repositories(normalized_counts)
    if not repositories:
        raise ValueError("No commit contributions were found for the selected period.")

    months = sorted(normalized_counts)
    x_positions = list(range(len(months)))
    stacked_base = [0] * len(months)
    tick_positions, tick_labels = _build_tick_positions_and_labels(months)
    bar_width = 1.0

    figure_width = min(max(6.5, len(tick_positions) * 0.55), 10) * 1.5
    figure, axis = plt.subplots(figsize=(figure_width, 4.875))
    for index, repository in enumerate(repositories):
        heights = [normalized_counts[month].get(repository, 0) for month in months]
        color, hatch = _series_style(index)
        axis.bar(
            x_positions,
            heights,
            bottom=stacked_base,
            label=f"{repository} ({repository_totals[repository]})",
            color=color,
            hatch=hatch,
            edgecolor="#222222" if hatch else "none",
            linewidth=0.3 if hatch else 0,
            width=bar_width,
        )
        stacked_base = [base + height for base, height in zip(stacked_base, heights, strict=True)]

    start_label = months[0].strftime("%Y-%m")
    end_label = months[-1].strftime("%Y-%m")
    total_commits = sum(stacked_base)

    axis.set_title(
        f"Monthly GitHub Commit Activity by Repository for {username}\n"
        f"{start_label} to {end_label} | Total commits: {total_commits}",
        fontsize=FONT_SIZE_PT,
    )
    axis.set_ylabel("Commits", fontsize=FONT_SIZE_PT)
    axis.set_xticks(tick_positions)
    axis.set_xticklabels(tick_labels, rotation=90, ha="center", va="top", fontsize=FONT_SIZE_PT)
    axis.tick_params(axis="y", labelsize=FONT_SIZE_PT)
    axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    axis.grid(axis="y", linestyle="--", linewidth=0.6, alpha=0.4)
    axis.set_axisbelow(True)
    axis.legend(
        title="Repository (Total commits)",
        bbox_to_anchor=(1.02, 1),
        loc="upper left",
        borderaxespad=0.0,
        fontsize=FONT_SIZE_PT,
        title_fontsize=FONT_SIZE_PT,
    )
    max_total = max(stacked_base)
    y_padding = max(1, max_total * 0.04)
    axis.set_ylim(0, max_total + y_padding)
    axis.set_xlim(-0.5 - bar_width / 2, len(months) - 0.5 + bar_width / 2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)

    return output_path
