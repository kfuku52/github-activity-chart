from datetime import date
from pathlib import Path

from githubactivitychart import cli
from githubactivitychart.cli import resolve_end_month


def test_resolve_end_month_defaults_to_current_month() -> None:
    end_month = resolve_end_month(
        to_month=None,
        today=date(2026, 3, 23),
    )

    assert end_month == date(2026, 3, 1)


def test_main_renders_multiple_outputs_after_single_fetch(monkeypatch, tmp_path: Path) -> None:
    fetch_calls = []
    render_calls = []
    monthly_counts = {date(2026, 1, 1): {"me/repo": 2}}

    class FakeClient:
        def fetch_monthly_commit_counts(self, **kwargs):
            fetch_calls.append(kwargs)
            return monthly_counts

    fake_client = FakeClient()

    class FakeGitHubClient:
        @classmethod
        def from_environment(cls):
            return fake_client

    def fake_render_stacked_bar_chart(**kwargs):
        render_calls.append(kwargs)
        return kwargs["output_path"]

    first_output = tmp_path / "chart.png"
    second_output = tmp_path / "chart.pdf"

    monkeypatch.setattr(cli, "GitHubClient", FakeGitHubClient)
    monkeypatch.setattr(cli, "render_stacked_bar_chart", fake_render_stacked_bar_chart)

    exit_code = cli.main(
        [
            "me",
            "--from",
            "2026-01",
            "--to",
            "2026-01",
            "--max-contribution-repositories",
            "25",
            "--output",
            str(first_output),
            "--output",
            str(second_output),
        ]
    )

    assert exit_code == 0
    assert len(fetch_calls) == 1
    assert fetch_calls[0]["max_contribution_repositories"] == 25
    assert [call["output_path"] for call in render_calls] == [first_output, second_output]
    assert all(call["monthly_counts"] is monthly_counts for call in render_calls)
