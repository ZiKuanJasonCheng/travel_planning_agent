"""Shared date resolution for trip requests and feedback revisions.

Both entry points accept the same three fields (start_date, end_date, days)
and must agree on how they resolve against each other; keeping the rule here
means `/trip/start` and `/trip/feedback` can't drift apart.
"""
from datetime import date, timedelta
from typing import Optional


def parse_iso_date(value: Optional[str], field: str) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{field} must be a valid ISO date (YYYY-MM-DD)")


def resolve_dates(
    start_date: Optional[str],
    end_date: Optional[str],
    days: Optional[int],
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """Return a consistent (start_date, end_date, days) triple.

    end_date wins when given, and days is recomputed from it. Otherwise days
    wins and end_date is recomputed. Fields that can't be resolved without a
    start_date are passed through untouched.
    """
    start = parse_iso_date(start_date, "start_date")
    end = parse_iso_date(end_date, "end_date")

    if end is not None:
        if start is None:
            return start_date, end_date, days
        if end <= start:
            raise ValueError("end_date must be at least one day after start_date")
        return start_date, end_date, (end - start).days

    if days is not None and start is not None:
        return start_date, (start + timedelta(days=days)).strftime("%Y-%m-%d"), days

    return start_date, end_date, days


def derive_start_date(end_date: str, days: int) -> str:
    """Infer the start date from an end date and a duration.

    Both must already be validated — the caller checks them against the stored
    trip before asking for this.
    """
    return (parse_iso_date(end_date, "end_date") - timedelta(days=days)).strftime("%Y-%m-%d")


__all__ = ["parse_iso_date", "resolve_dates", "derive_start_date"]
