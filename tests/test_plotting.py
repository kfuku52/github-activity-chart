import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from matplotlib.figure import Figure

from githubactivitychart import plotting
from githubactivitychart.plotting import (
    ChartRenderError,
    _build_tick_positions_and_labels,
    _normalize_monthly_counts,
    _repository_totals,
    _series_style,
    collapse_repositories,
    render_stacked_bar_chart,
    render_stacked_bar_charts,
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
        Figure,
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
    assert height <= 760


@pytest.mark.parametrize("suffixes", [("png", "pdf", "svg"), ("svg", "pdf", "png"), ("png", "png")])
def test_multiple_exports_match_independently_rendered_charts(tmp_path: Path, suffixes) -> None:
    paths = [tmp_path / f"shared-{index}.{suffix}" for index, suffix in enumerate(suffixes)]
    counts = sample_counts()
    counts[date(2026, 3, 1)] = {"repo-c": 1}
    as_of = datetime(2026, 3, 10, tzinfo=UTC)

    assert render_stacked_bar_charts(counts, "octocat", paths, as_of=as_of) == paths

    for index, suffix in enumerate(suffixes):
        separate = tmp_path / f"separate-{index}.{suffix}"
        render_stacked_bar_chart(counts, "octocat", separate, as_of=as_of)
        assert paths[index].read_bytes() == separate.read_bytes()


def test_multiple_exports_build_figure_once(monkeypatch, tmp_path: Path) -> None:
    original = plotting._build_chart_figure
    figures = []

    def build(*args, **kwargs):
        figure = original(*args, **kwargs)
        figures.append(figure)
        return figure

    monkeypatch.setattr(plotting, "_build_chart_figure", build)
    paths = [tmp_path / f"chart.{suffix}" for suffix in ("png", "pdf", "svg")]
    render_stacked_bar_charts(sample_counts(), "octocat", paths)

    assert len(figures) == 1
    assert not figures[0].axes


def test_multiple_exports_validate_all_suffixes_before_writing(tmp_path: Path) -> None:
    paths = [tmp_path / "valid.pdf", tmp_path / "invalid.jpg"]
    with pytest.raises(ChartRenderError, match="Unsupported"):
        render_stacked_bar_charts(sample_counts(), "octocat", paths)
    assert not paths[0].exists()
    assert render_stacked_bar_charts(sample_counts(), "octocat", []) == []


def test_chart_batches_rectangles_and_preserves_zero_height_outlines() -> None:
    counts = {date(2026, 1, 1): {"repo-a": 2}, date(2026, 2, 1): {"repo-b": 3}}
    figure = plotting._build_chart_figure(counts, "octocat")
    try:
        axis = figure.axes[0]
        assert not axis.patches
        assert len(axis.collections) == 2
        assert all(not collection.get_in_layout() for collection in axis.collections)
        assert all(len(collection.get_paths()) == 1 for collection in axis.collections)
        rectangles = [
            vertices
            for collection in axis.collections
            for vertices in collection.get_paths()[0].vertices.reshape(-1, 5, 2)
        ]
        assert len(rectangles) == 4
        assert all((vertices[0] == vertices[-1]).all() for vertices in rectangles)
        assert sum(vertices[:, 1].max() == vertices[:, 1].min() for vertices in rectangles) == 2
        assert [text.get_text() for text in axis.get_legend().texts] == ["repo-b (3)", "repo-a (2)"]
    finally:
        figure.clear()


def test_exports_select_compound_raster_and_individual_vector_paths(monkeypatch, tmp_path: Path) -> None:
    save = Figure.savefig
    paths_per_format = []

    def record_save(figure, *args, **kwargs):
        result = save(figure, *args, **kwargs)
        paths_per_format.append(
            (kwargs["format"], [len(collection.get_paths()) for collection in figure.axes[0].collections])
        )
        return result

    monkeypatch.setattr(Figure, "savefig", record_save)
    render_stacked_bar_charts(
        sample_counts(), "octocat", [tmp_path / f"chart.{suffix}" for suffix in ("svg", "png", "pdf")]
    )

    assert paths_per_format == [("svg", [2, 2]), ("png", [1, 1]), ("pdf", [2, 2])]


@pytest.mark.parametrize(
    "snap,sketch,narrow",
    [(True, None, False), (False, None, False), (True, (1, 100, 2), False), (True, None, True)],
)
def test_long_history_matches_individually_painted_rectangles(
    monkeypatch, tmp_path: Path, snap: bool, sketch, narrow: bool
) -> None:
    from matplotlib import rc_context
    from matplotlib.collections import PolyCollection

    counts = {}
    for index in range(206):
        year, month = divmod(2008 * 12 + index, 12)
        counts[date(year, month + 1, 1)] = {f"repo-{index % 3}": index % 10 + 1}
    build = plotting._build_chart_figure

    def build_sized(*args, **kwargs):
        figure = build(*args, **kwargs)
        if narrow:
            figure.set_size_inches(2, 3)
        return figure

    def build_reference(*args, **kwargs):
        figure = build_sized(*args, **kwargs)
        axis = figure.axes[0]
        for collection in list(axis.collections):
            rectangles = collection.get_paths()[0].vertices.reshape(-1, 5, 2)[:, :4]
            reference = PolyCollection(
                rectangles,
                facecolors=collection.get_facecolors(),
                hatch=collection.get_hatch(),
                edgecolors="#222222",
                linewidths=0.25,
                joinstyle="miter",
                capstyle="butt",
                antialiaseds=True,
                in_layout=False,
            )
            collection.remove()
            axis.add_collection(reference)
        return figure

    actual = tmp_path / "compound.png"
    expected = tmp_path / "individual.png"
    with rc_context({"path.snap": snap, "path.sketch": sketch}):
        monkeypatch.setattr(plotting, "_build_chart_figure", build_sized)
        render_stacked_bar_chart(counts, "octocat", actual)
        figure = build_reference(counts, "octocat")
        try:
            axis = figure.axes[0]
            axis.set_position(axis.get_position().frozen())
            axis.set_in_layout(True)
            figure.savefig(
                expected, format="png", dpi=200, bbox_inches="tight", metadata={"Software": "github-activity-chart"}
            )
        finally:
            figure.clear()

    assert actual.read_bytes() == expected.read_bytes()


def test_repeated_formats_are_encoded_once(monkeypatch, tmp_path: Path) -> None:
    save = Figure.savefig
    formats = []

    def record_save(*args, **kwargs):
        formats.append(kwargs["format"])
        return save(*args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", record_save)
    paths = [tmp_path / name for name in ("a.png", "nested/b.PNG", "c.pdf", "d.pdf", "e.svg", "f.svg")]
    render_stacked_bar_charts(sample_counts(), "octocat", paths)
    assert formats == ["png", "pdf", "svg"]
    for first, second in zip(paths[::2], paths[1::2], strict=True):
        assert first.read_bytes() == second.read_bytes()


def test_repeated_same_path_and_hard_links_are_supported(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"old")
    second.hardlink_to(first)
    render_stacked_bar_charts(sample_counts(), "octocat", [first, first, second])
    assert first.read_bytes().startswith(b"\x89PNG")
    assert first.read_bytes() == second.read_bytes()


def test_copy_failure_clears_figure_and_preserves_existing_outputs(monkeypatch, tmp_path: Path) -> None:
    from githubactivitychart.cli import render_outputs_atomically

    paths = [tmp_path / "first.png", tmp_path / "second.png"]
    for path in paths:
        path.write_bytes(b"old")
    figures = []
    build = plotting._build_chart_figure

    def record_build(*args, **kwargs):
        figure = build(*args, **kwargs)
        figures.append(figure)
        return figure

    def fail_copy(*args):
        raise OSError("copy failed")

    monkeypatch.setattr(plotting, "_build_chart_figure", record_build)
    monkeypatch.setattr(plotting.shutil, "copyfile", fail_copy)
    with pytest.raises(ChartRenderError, match="copy failed"):
        render_outputs_atomically(
            monthly_counts=sample_counts(), username="octocat", output_paths=paths, top_repos=20, as_of=None
        )
    assert all(path.read_bytes() == b"old" for path in paths)
    assert len(figures) == 1 and not figures[0].axes
    assert not list(tmp_path.glob(".*.tmp.*"))
