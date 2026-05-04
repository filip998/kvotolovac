from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TypeVar

from ..config import settings
from ..models.schemas import RawOddsData, RawOutcomeOffer

_RawScrapeRow = TypeVar("_RawScrapeRow", RawOddsData, RawOutcomeOffer)


def current_utc_time() -> datetime:
    return datetime.now(tz=timezone.utc)


def configured_lookahead_hours(lookahead_hours: int | None = None) -> int:
    if lookahead_hours is None:
        lookahead_hours = settings.scrape_lookahead_hours
    return max(0, lookahead_hours)


def lookahead_cutoff(
    now: datetime | None = None,
    *,
    lookahead_hours: int | None = None,
) -> datetime:
    base = now or current_utc_time()
    return base.astimezone(timezone.utc) + timedelta(
        hours=configured_lookahead_hours(lookahead_hours)
    )


def format_utc_naive_seconds(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_raw_start_time(start_time: str | None) -> datetime | None:
    if not start_time:
        return None
    try:
        parsed = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_raw_odds_by_lookahead(
    rows: list[_RawScrapeRow],
    *,
    now: datetime | None = None,
    lookahead_hours: int | None = None,
) -> list[_RawScrapeRow]:
    cutoff = lookahead_cutoff(now, lookahead_hours=lookahead_hours)
    filtered: list[_RawScrapeRow] = []
    for row in rows:
        start_dt = parse_raw_start_time(row.start_time)
        if start_dt is not None and start_dt > cutoff:
            continue
        filtered.append(row)
    return filtered
