from datetime import date

from githubactivitychart.cli import resolve_end_month


def test_resolve_end_month_defaults_to_current_month() -> None:
    end_month = resolve_end_month(
        to_month=None,
        today=date(2026, 3, 23),
    )

    assert end_month == date(2026, 3, 1)
