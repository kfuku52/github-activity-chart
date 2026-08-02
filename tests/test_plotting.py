import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from githubactivitychart import plotting
from githubactivitychart.plotting import (
    ChartRenderError,
    _build_tick_positions_and_labels,
    _normalize_monthly_counts,
    _repository_totals,
    _series_style,
    collapse_repositories,
    render_stacked_bar_chart,
)


def sample_counts() -> dict[date, dict[str, int]]:
    return {
        date(2026, 1, 1): {"repo-a": 5, "repo-b": 2},
        date(2026, 2, 1): {"repo-a": 3, "repo-b": 4},
    }


def test_normalize_counts_fills_missing_months_without_mutating_input() -> None:
    monthly_counts = {
        date(2026, 1, 1): {"repo-a": 2, "zero": 0},
        date(2026, 3, 1): {"repo-a": 1},
    }

    normalized = _normalize_monthly_counts(monthly_counts)

    assert normalized == {
        date(2026, 1, 1): {"repo-a": 2},
        date(2026, 2, 1): {},
        date(2026, 3, 1): {"repo-a": 1},
    }
    assert monthly_counts[date(2026, 1, 1)]["zero"] == 0


@pytest.mark.parametrize(
    ("monthly_counts", "message"),
    [
        ({}, "No monthly"),
        ({date(2026, 1, 2): {"repo": 1}}, "first-of-month"),
        ({date(2026, 1, 1): {"repo": -1}}, "negative"),
        ({date(2026, 1, 1): {"repo": 1.5}}, "integers"),
        ({date(2026, 1, 1): {"repo": True}}, "integers"),
        ({date(2026, 1, 1): {"": 1}}, "non-empty"),
        ({date(2026, 1, 1): {"repo": 0}}, "No commit"),
        ({date(2026, 1, 1): ["not", "a", "mapping"]}, "mapping"),
    ],
)
def test_normalize_counts_rejects_malformed_input(
    monthly_counts,
    message: str,
) -> None:
    with pytest.raises(ChartRenderError, match=message):
        _normalize_monthly_counts(monthly_counts)


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


def test_collapse_none_returns_independent_copy() -> None:
    original = sample_counts()

    collapsed = collapse_repositories(original, top_repos=None)
    collapsed[date(2026, 1, 1)]["repo-a"] = 999

    assert original[date(2026, 1, 1)]["repo-a"] == 5


@pytest.mark.parametrize("top_repos", [0, -1, True, 1.5])
def test_collapse_repositories_rejects_invalid_limit(top_repos) -> None:
    with pytest.raises(ChartRenderError, match="integer"):
        collapse_repositories(sample_counts(), top_repos=top_repos)


def test_build_tick_positions_and_labels_uses_january_for_long_ranges() -> None:
    months = [date(year, month, 1) for year in range(2020, 2023) for month in range(1, 13)]

    positions, labels = _build_tick_positions_and_labels(months)

    assert positions == [0, 12, 24]
    assert labels == ["2020 Jan", "2021 Jan", "2022 Jan"]


def test_build_tick_positions_labels_every_month_for_short_range() -> None:
    months = [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]

    positions, labels = _build_tick_positions_and_labels(months)

    assert positions == [0, 1, 2]
    assert labels == ["2025-12", "2026 Jan", "2026-02"]


def test_build_tick_positions_falls_back_when_long_input_has_no_january() -> None:
    months = [date(2026, 2, 1)] * 25

    positions, labels = _build_tick_positions_and_labels(months)

    assert positions == list(range(25))
    assert labels == ["2026-02"] * 25


def test_repository_totals_sums_across_months() -> None:
    assert _repository_totals(sample_counts()) == {
        "repo-a": 8,
        "repo-b": 6,
    }


def test_series_style_is_stable_by_repository_not_rank() -> None:
    first = _series_style("owner/repository")

    assert first == _series_style("owner/repository")
    assert first != _series_style("different/repository")


@pytest.mark.parametrize("suffix", [".pdf", ".png", ".svg"])
def test_render_stacked_bar_chart_writes_supported_formats(
    tmp_path: Path,
    suffix: str,
) -> None:
    output_path = tmp_path / f"chart{suffix}"

    result = render_stacked_bar_chart(
        monthly_counts=sample_counts(),
        username="octocat",
        output_path=output_path,
    )

    assert result == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_render_rejects_extensionless_or_unsupported_output(tmp_path: Path) -> None:
    for name in ("chart", "chart.jpeg"):
        with pytest.raises(ChartRenderError, match="Unsupported output"):
            render_stacked_bar_chart(
                sample_counts(),
                "octocat",
                tmp_path / name,
            )
        assert not (tmp_path / f"{name}.png").exists()


def test_render_rejects_empty_username(tmp_path: Path) -> None:
    with pytest.raises(ChartRenderError, match="username"):
        render_stacked_bar_chart(sample_counts(), "", tmp_path / "chart.pdf")


def test_render_rejects_invalid_as_of_timestamp(tmp_path: Path) -> None:
    with pytest.raises(ChartRenderError, match="timezone"):
        render_stacked_bar_chart(
            sample_counts(),
            "octocat",
            tmp_path / "chart.pdf",
            as_of=datetime(2026, 2, 10),  # noqa: DTZ001 - deliberately naive
        )
    with pytest.raises(ChartRenderError, match="final displayed month"):
        render_stacked_bar_chart(
            sample_counts(),
            "octocat",
            tmp_path / "chart.pdf",
            as_of=datetime(2026, 3, 1, tzinfo=UTC),
        )


@pytest.mark.parametrize("suffix", [".pdf", ".png", ".svg"])
def test_output_is_byte_for_byte_deterministic(
    tmp_path: Path,
    suffix: str,
) -> None:
    first = tmp_path / f"first{suffix}"
    second = tmp_path / f"second{suffix}"

    render_stacked_bar_chart(sample_counts(), "octocat", first)
    render_stacked_bar_chart(sample_counts(), "octocat", second)

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_render_includes_month_to_date_annotation(tmp_path: Path) -> None:
    output_path = tmp_path / "mtd.svg"
    render_stacked_bar_chart(
        sample_counts(),
        "octocat",
        output_path,
        as_of=datetime(2026, 2, 20, 12, 30, tzinfo=UTC),
    )

    assert "month to date" in output_path.read_text()


def test_render_wraps_save_failures_as_chart_errors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        plotting.Figure,
        "savefig",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(ChartRenderError, match="disk full"):
        render_stacked_bar_chart(
            sample_counts(),
            "octocat",
            tmp_path / "chart.pdf",
        )


def test_default_limit_keeps_large_chart_dimensions_bounded(tmp_path: Path) -> None:
    monthly_counts = {date(2026, 1, 1): {f"owner/repository-{index:03d}": index + 1 for index in range(200)}}
    output_path = tmp_path / "large.png"

    render_stacked_bar_chart(monthly_counts, "octocat", output_path)

    from PIL import Image

    with Image.open(output_path) as image:
        width, height = image.size
    assert width <= 4000
    assert height <= 1000
