from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from githubactivitychart import cli
from githubactivitychart.cli import (
    parse_month,
    render_outputs_atomically,
    resolve_end_month,
)


def test_parse_month_is_strict_and_rejects_pre_github_dates() -> None:
    assert parse_month("2026-01") == date(2026, 1, 1)
    for invalid in ("2026-1", "26-01", "2026-13", "0001-01", "0000-01"):
        with pytest.raises(Exception, match="Invalid month"):
            parse_month(invalid)


def test_resolve_end_month_defaults_to_current_month() -> None:
    assert resolve_end_month(None, today=date(2026, 3, 23)) == date(2026, 3, 1)


def test_resolve_end_month_rejects_future_month() -> None:
    with pytest.raises(ValueError, match="future"):
        resolve_end_month(date(2026, 4, 1), today=date(2026, 3, 23))


@pytest.mark.parametrize(
    "username",
    ["-leading", "trailing-", "double--hyphen", "has space", "x" * 40],
)
def test_parser_rejects_invalid_username(username: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([username])


def test_parser_rejects_invalid_contribution_repository_limit() -> None:
    for invalid in ("101", "0", "not-a-number"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["octocat", "--max-contribution-repositories", invalid])


def fake_renderer_that_writes(render_calls):
    def fake_renderer(**kwargs):
        render_calls.append(kwargs)
        kwargs["output_path"].write_bytes(b"rendered")
        return kwargs["output_path"]

    return fake_renderer


def test_main_renders_multiple_outputs_after_single_fetch(monkeypatch, tmp_path: Path) -> None:
    fetch_calls = []
    render_calls = []
    monthly_counts = {date(2026, 1, 1): {"me/repo": 2}}

    class FakeClient:
        def fetch_monthly_commit_counts(self, **kwargs):
            fetch_calls.append(kwargs)
            return monthly_counts

    class FakeGitHubClient:
        @classmethod
        def from_environment(cls):
            return FakeClient()

    monkeypatch.setattr(cli, "GitHubClient", FakeGitHubClient)
    monkeypatch.setattr(
        cli,
        "render_stacked_bar_chart",
        fake_renderer_that_writes(render_calls),
    )
    first_output = tmp_path / "chart.png"
    second_output = tmp_path / "chart.pdf"

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
    assert fetch_calls[0]["progress_callback"] is cli.print_progress
    assert fetch_calls[0]["show_private_names"] is False
    assert first_output.read_bytes() == b"rendered"
    assert second_output.read_bytes() == b"rendered"
    assert [call["output_path"].suffix for call in render_calls] == [".png", ".pdf"]
    assert all(".tmp" in call["output_path"].name for call in render_calls)


def test_main_quiet_suppresses_progress_and_marks_current_month_mtd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fetch_calls = []
    render_calls = []
    current_month = datetime.now(UTC).date().replace(day=1)
    monthly_counts = {current_month: {"me/repo": 2}}

    class FakeClient:
        def fetch_monthly_commit_counts(self, **kwargs):
            fetch_calls.append(kwargs)
            return monthly_counts

    class FakeGitHubClient:
        @classmethod
        def from_environment(cls):
            return FakeClient()

    monkeypatch.setattr(cli, "GitHubClient", FakeGitHubClient)
    monkeypatch.setattr(
        cli,
        "render_stacked_bar_chart",
        fake_renderer_that_writes(render_calls),
    )

    exit_code = cli.main(
        [
            "me",
            "--from",
            current_month.strftime("%Y-%m"),
            "--quiet",
            "--output",
            str(tmp_path / "chart.pdf"),
        ]
    )

    assert exit_code == 0
    assert fetch_calls[0]["progress_callback"] is None
    assert render_calls[0]["as_of"] is not None


def test_main_validates_range_and_output_before_resolving_token(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class MustNotAuthenticate:
        @classmethod
        def from_environment(cls):
            raise AssertionError("authentication should not have been attempted")

    monkeypatch.setattr(cli, "GitHubClient", MustNotAuthenticate)

    assert (
        cli.main(
            [
                "me",
                "--from",
                "2026-02",
                "--to",
                "2026-01",
                "--output",
                str(tmp_path / "chart.pdf"),
            ]
        )
        == 1
    )
    assert (
        cli.main(
            [
                "me",
                "--from",
                "2026-01",
                "--to",
                "2026-01",
                "--output",
                str(tmp_path / "chart"),
            ]
        )
        == 1
    )


def test_main_requires_include_private_for_show_private_names(
    monkeypatch,
) -> None:
    class MustNotAuthenticate:
        @classmethod
        def from_environment(cls):
            raise AssertionError("authentication should not have been attempted")

    monkeypatch.setattr(cli, "GitHubClient", MustNotAuthenticate)

    assert cli.main(["me", "--show-private-names"]) == 1


def test_main_discovers_and_trims_to_first_nonempty_month(
    monkeypatch,
    tmp_path: Path,
) -> None:
    render_calls = []

    class FakeClient:
        def fetch_user_created_at(self, username):
            return datetime(2025, 12, 20, tzinfo=UTC)

        def fetch_monthly_commit_counts(self, **kwargs):
            return {
                date(2025, 12, 1): {},
                date(2026, 1, 1): {"me/repo": 1},
                date(2026, 2, 1): {},
            }

    class FakeGitHubClient:
        @classmethod
        def from_environment(cls):
            return FakeClient()

    monkeypatch.setattr(cli, "GitHubClient", FakeGitHubClient)
    monkeypatch.setattr(
        cli,
        "render_stacked_bar_chart",
        fake_renderer_that_writes(render_calls),
    )

    exit_code = cli.main(
        [
            "me",
            "--to",
            "2026-02",
            "--output",
            str(tmp_path / "chart.pdf"),
        ]
    )

    assert exit_code == 0
    assert list(render_calls[0]["monthly_counts"]) == [
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


def test_main_reports_no_contributions_after_account_discovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FakeClient:
        def fetch_user_created_at(self, username):
            return datetime(2026, 1, 1, tzinfo=UTC)

        def fetch_monthly_commit_counts(self, **kwargs):
            return {date(2026, 1, 1): {}}

    class FakeGitHubClient:
        @classmethod
        def from_environment(cls):
            return FakeClient()

    monkeypatch.setattr(cli, "GitHubClient", FakeGitHubClient)

    assert (
        cli.main(
            [
                "me",
                "--to",
                "2026-01",
                "--output",
                str(tmp_path / "chart.pdf"),
            ]
        )
        == 1
    )


def test_atomic_render_failure_preserves_all_existing_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first.pdf"
    second_output = tmp_path / "second.png"
    first_output.write_bytes(b"old-first")
    second_output.write_bytes(b"old-second")
    calls = 0

    def failing_renderer(**kwargs):
        nonlocal calls
        calls += 1
        kwargs["output_path"].write_bytes(b"new")
        if calls == 2:
            raise ValueError("second format failed")
        return kwargs["output_path"]

    monkeypatch.setattr(cli, "render_stacked_bar_chart", failing_renderer)

    with pytest.raises(ValueError, match="second format failed"):
        render_outputs_atomically(
            monthly_counts={date(2026, 1, 1): {"me/repo": 1}},
            username="me",
            output_paths=[first_output, second_output],
            top_repos=15,
            as_of=None,
        )

    assert first_output.read_bytes() == b"old-first"
    assert second_output.read_bytes() == b"old-second"
    assert not list(tmp_path.glob(".*.tmp.*"))
    assert not list(tmp_path.glob(".*.bak"))


def test_atomic_render_success_replaces_existing_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "chart.pdf"
    output.write_bytes(b"old")
    monkeypatch.setattr(
        cli,
        "render_stacked_bar_chart",
        fake_renderer_that_writes([]),
    )

    result = render_outputs_atomically(
        monthly_counts={date(2026, 1, 1): {"me/repo": 1}},
        username="me",
        output_paths=[output],
        top_repos=15,
        as_of=None,
    )

    assert result == [output]
    assert output.read_bytes() == b"rendered"
    assert not list(tmp_path.glob(".*.bak"))


def test_atomic_install_failure_rolls_back_already_replaced_outputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first.pdf"
    second_output = tmp_path / "second.png"
    first_output.write_bytes(b"old-first")
    second_output.write_bytes(b"old-second")
    monkeypatch.setattr(
        cli,
        "render_stacked_bar_chart",
        fake_renderer_that_writes([]),
    )
    real_replace = cli.os.replace
    replace_calls = 0

    def fail_fourth_replace(source, destination):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 4:
            raise OSError("simulated install failure")
        real_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", fail_fourth_replace)

    with pytest.raises(OSError, match="simulated install failure"):
        render_outputs_atomically(
            monthly_counts={date(2026, 1, 1): {"me/repo": 1}},
            username="me",
            output_paths=[first_output, second_output],
            top_repos=15,
            as_of=None,
        )

    assert first_output.read_bytes() == b"old-first"
    assert second_output.read_bytes() == b"old-second"


def test_main_debug_prints_traceback_for_operational_error(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    class FailingClient:
        @classmethod
        def from_environment(cls):
            raise RuntimeError("expected failure")

    monkeypatch.setattr(cli, "GitHubClient", FailingClient)

    assert (
        cli.main(
            [
                "me",
                "--from",
                "2026-01",
                "--to",
                "2026-01",
                "--debug",
                "--output",
                str(tmp_path / "chart.pdf"),
            ]
        )
        == 1
    )
    assert "Traceback" in capsys.readouterr().err


def test_validate_output_paths_rejects_duplicates_and_unknown_suffix(
    tmp_path: Path,
) -> None:
    output = tmp_path / "chart.pdf"

    with pytest.raises(ValueError, match="Duplicate"):
        cli.validate_output_paths([output, output])
    with pytest.raises(ValueError, match="Unsupported"):
        cli.validate_output_paths([tmp_path / "chart.jpeg"])
