from datetime import date
from pathlib import Path

from githubactivitychart.plotting import (
    _build_tick_positions_and_labels,
    _repository_totals,
    _series_style,
    collapse_repositories,
    render_stacked_bar_chart,
)


def test_collapse_repositories_groups_remaining_series() -> None:
    monthly_counts = {
        date(2026, 1, 1): {"repo-a": 5, "repo-b": 2, "repo-c": 1},
        date(2026, 2, 1): {"repo-a": 3, "repo-b": 4, "repo-c": 6},
    }

    collapsed = collapse_repositories(monthly_counts, top_repos=2)

    assert collapsed == {
        date(2026, 1, 1): {"repo-a": 5, "repo-c": 1, "Other": 2},
        date(2026, 2, 1): {"repo-a": 3, "repo-c": 6, "Other": 4},
    }


def test_build_tick_positions_and_labels_uses_january_for_long_ranges() -> None:
    months = [date(year, month, 1) for year in range(2020, 2023) for month in range(1, 13)]

    positions, labels = _build_tick_positions_and_labels(months)

    assert positions == [0, 12, 24]
    assert labels == ["2020 Jan", "2021 Jan", "2022 Jan"]


def test_repository_totals_sums_across_months() -> None:
    monthly_counts = {
        date(2026, 1, 1): {"repo-a": 5, "repo-b": 2},
        date(2026, 2, 1): {"repo-a": 3, "repo-b": 4, "repo-c": 1},
    }

    assert _repository_totals(monthly_counts) == {
        "repo-a": 8,
        "repo-b": 6,
        "repo-c": 1,
    }


def test_series_style_uses_unique_colors_before_hatching() -> None:
    first_ten = [_series_style(index) for index in range(10)]

    assert len({color for color, _ in first_ten}) == 10
    assert {hatch for _, hatch in first_ten} == {""}


def test_render_stacked_bar_chart_writes_pdf(tmp_path: Path) -> None:
    monthly_counts = {
        date(2026, 1, 1): {"repo-a": 5, "repo-b": 2},
        date(2026, 2, 1): {"repo-a": 3, "repo-b": 4},
    }
    output_path = tmp_path / "chart.pdf"

    result = render_stacked_bar_chart(
        monthly_counts=monthly_counts,
        username="octocat",
        output_path=output_path,
    )

    assert result == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0
