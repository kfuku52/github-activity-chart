from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from githubactivitychart import update_charts


@pytest.mark.parametrize(
    ("now", "end_month", "recent_start"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), date(2025, 12, 1), date(2023, 1, 1)),
        (datetime(2026, 10, 15, tzinfo=UTC), date(2026, 9, 1), date(2023, 10, 1)),
        (datetime(2026, 1, 1, 1, tzinfo=timezone(timedelta(hours=9))), date(2025, 11, 1), date(2022, 12, 1)),
    ],
)
def test_generate_public_charts_reuses_fetch_and_preserves_ranges(
    monkeypatch, tmp_path: Path, now: datetime, end_month: date, recent_start: date
) -> None:
    fetches = []
    renders = []
    counts = {date(2020, 1, 1): {}, date(2021, 1, 1): {"me/repo": 1}, end_month: {"me/repo": 2}}

    class FakeClient:
        @classmethod
        def from_environment(cls):
            return cls()

        def fetch_user_created_at(self, username):
            assert username == "me"
            return datetime(2020, 1, 15, tzinfo=UTC)

        def fetch_monthly_commit_counts(self, *args, **kwargs):
            fetches.append((args, kwargs))
            return counts

    def render(**kwargs):
        renders.append(kwargs)
        return kwargs["output_paths"]

    monkeypatch.setattr(update_charts, "GitHubClient", FakeClient)
    monkeypatch.setattr(update_charts, "render_outputs_atomically", render)
    outputs = update_charts.generate_public_charts("me", tmp_path, now=now)

    assert len(fetches) == 1
    assert fetches[0][0] == ("me", date(2020, 1, 1), end_month)
    assert fetches[0][1]["include_private"] is False
    assert date(2020, 1, 1) not in renders[0]["monthly_counts"]
    recent = renders[1]["monthly_counts"]
    assert len(recent) == 36
    assert min(recent) == recent_start
    assert max(recent) == end_month
    assert recent[end_month] is counts[end_month]
    assert recent[recent_start] == {}
    assert all(render["as_of"] is None for render in renders)
    assert outputs == [tmp_path / f"me_{name}" for name in ("all_months.png", "all_months.pdf", "last_36_months.svg")]


def test_generate_public_charts_rejects_naive_now(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone"):
        update_charts.generate_public_charts("me", tmp_path, now=datetime(2026, 1, 1))  # noqa: DTZ001 - deliberately naive


def test_generate_public_charts_does_not_render_empty_data(monkeypatch, tmp_path: Path) -> None:
    class FakeClient:
        @classmethod
        def from_environment(cls):
            return cls()

        def fetch_user_created_at(self, username):
            return datetime(2020, 1, 1, tzinfo=UTC)

        def fetch_monthly_commit_counts(self, *args, **kwargs):
            return {date(2020, 1, 1): {}}

    monkeypatch.setattr(update_charts, "GitHubClient", FakeClient)
    with pytest.raises(RuntimeError, match="No commits"):
        update_charts.generate_public_charts("me", tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert not list(tmp_path.iterdir())


def test_main_reports_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(update_charts, "generate_public_charts", lambda *args: [Path("output/test.png")])
    assert update_charts.main() == 0
    assert "Saved chart to output/test.png" in capsys.readouterr().out


def test_main_reports_operational_errors(monkeypatch, capsys) -> None:
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(update_charts, "generate_public_charts", fail)
    assert update_charts.main() == 1
    assert "Error: disk full" in capsys.readouterr().err
