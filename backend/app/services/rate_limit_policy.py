from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from ..config import settings

ScrapeLane = Literal["threshold_odds", "outcome_offer"]
DetailMode = Literal["partial", "full"]

_RATE_LIMIT_PER_SECOND_MAX = 20.0
_ENTRY_SPLIT_RE = re.compile(r"[,;]")
_VALID_LANES = {"threshold_odds", "outcome_offer"}
_VALID_DETAIL_MODES = {"partial", "full", "*"}


class RateLimitPolicyError(ValueError):
    """Raised when configured rate-limit policy strings cannot be parsed."""


@dataclass(frozen=True)
class ScrapeTypeRateLimit:
    bookmaker_id: str
    lane: ScrapeLane
    detail_mode: DetailMode | None
    rate_limit_per_second: float

    @property
    def key(self) -> str:
        detail = self.detail_mode if self.detail_mode is not None else "*"
        return f"{self.bookmaker_id}:{self.lane}:{detail}"


@dataclass(frozen=True)
class RateLimitPolicy:
    bookmaker_rate_limits: dict[str, float]
    scrape_type_rate_limits: tuple[ScrapeTypeRateLimit, ...]

    @classmethod
    def from_settings(cls) -> "RateLimitPolicy":
        return cls(
            bookmaker_rate_limits=parse_bookmaker_rate_limits(settings.bookmaker_rate_limits),
            scrape_type_rate_limits=parse_scrape_type_rate_limits(settings.scrape_type_rate_limits),
        )

    def effective_rate_limit(
        self,
        *,
        bookmaker_id: str,
        lane: ScrapeLane,
        detail_mode: DetailMode | None,
        global_rate_limit_per_second: float,
        meridian_rate_limit_per_second: float,
    ) -> float:
        normalized_bookmaker_id = _normalize_bookmaker_id(bookmaker_id)
        baseline = (
            meridian_rate_limit_per_second
            if normalized_bookmaker_id == "meridian"
            else global_rate_limit_per_second
        )

        scrape_type_cap = self._scrape_type_cap(
            bookmaker_id=normalized_bookmaker_id,
            lane=lane,
            detail_mode=detail_mode,
        )
        if scrape_type_cap is not None:
            return min(scrape_type_cap, baseline)

        bookmaker_cap = self.bookmaker_rate_limits.get(normalized_bookmaker_id)
        if bookmaker_cap is not None:
            return min(bookmaker_cap, baseline)

        return baseline

    def metadata_bookmaker_rate_limits(self) -> dict[str, float]:
        return dict(sorted(self.bookmaker_rate_limits.items()))

    def metadata_scrape_type_rate_limits(self) -> dict[str, float]:
        return {
            policy.key: policy.rate_limit_per_second
            for policy in sorted(
                self.scrape_type_rate_limits,
                key=lambda item: item.key,
            )
        }

    def _scrape_type_cap(
        self,
        *,
        bookmaker_id: str,
        lane: ScrapeLane,
        detail_mode: DetailMode | None,
    ) -> float | None:
        wildcard_match: float | None = None
        for policy in self.scrape_type_rate_limits:
            if policy.bookmaker_id != bookmaker_id or policy.lane != lane:
                continue
            if policy.detail_mode is None:
                wildcard_match = policy.rate_limit_per_second
                continue
            if policy.detail_mode == detail_mode:
                return policy.rate_limit_per_second
        return wildcard_match


def parse_bookmaker_rate_limits(raw: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for entry in _split_entries(raw):
        parts = _split_policy_parts(entry)
        if len(parts) != 2:
            raise RateLimitPolicyError(
                "Bookmaker rate limits must use '<bookmaker>:<rate>' entries"
            )
        bookmaker_id, raw_rate = parts
        normalized_bookmaker_id = _normalize_bookmaker_id(bookmaker_id)
        if normalized_bookmaker_id in result:
            raise RateLimitPolicyError(f"Duplicate bookmaker rate policy: {entry}")
        result[normalized_bookmaker_id] = _parse_rate(raw_rate, entry)
    return result


def parse_scrape_type_rate_limits(raw: str) -> tuple[ScrapeTypeRateLimit, ...]:
    result: list[ScrapeTypeRateLimit] = []
    seen: set[tuple[str, ScrapeLane, DetailMode | None]] = set()
    for entry in _split_entries(raw):
        parts = _split_policy_parts(entry)
        if len(parts) == 3:
            bookmaker_id, raw_lane, raw_rate = parts
            raw_detail_mode = "*"
        elif len(parts) == 4:
            bookmaker_id, raw_lane, raw_detail_mode, raw_rate = parts
        else:
            raise RateLimitPolicyError(
                (
                    "Scrape-type rate limits must use '<bookmaker>:<lane>:<rate>' "
                    "or '<bookmaker>:<lane>:<detail_mode>:<rate>' entries"
                )
            )

        lane = _parse_lane(raw_lane, entry)
        detail_mode = _parse_detail_mode(raw_detail_mode, entry)
        key = (_normalize_bookmaker_id(bookmaker_id), lane, detail_mode)
        if key in seen:
            raise RateLimitPolicyError(f"Duplicate scrape-type rate policy: {entry}")
        seen.add(key)
        result.append(
            ScrapeTypeRateLimit(
                bookmaker_id=key[0],
                lane=lane,
                detail_mode=detail_mode,
                rate_limit_per_second=_parse_rate(raw_rate, entry),
            )
        )
    return tuple(result)


def _split_entries(raw: str) -> list[str]:
    return [entry.strip() for entry in _ENTRY_SPLIT_RE.split(raw or "") if entry.strip()]


def _split_policy_parts(entry: str) -> list[str]:
    return [part.strip() for part in entry.split(":")]


def _normalize_bookmaker_id(bookmaker_id: str) -> str:
    normalized = bookmaker_id.strip().lower()
    if not normalized:
        raise RateLimitPolicyError("Rate-limit policy bookmaker id cannot be empty")
    return normalized


def _parse_lane(raw_lane: str, entry: str) -> ScrapeLane:
    lane = raw_lane.strip().lower()
    if lane not in _VALID_LANES:
        raise RateLimitPolicyError(f"Invalid scrape lane in rate policy '{entry}'")
    return lane  # type: ignore[return-value]


def _parse_detail_mode(raw_detail_mode: str, entry: str) -> DetailMode | None:
    detail_mode = raw_detail_mode.strip().lower()
    if detail_mode not in _VALID_DETAIL_MODES:
        raise RateLimitPolicyError(f"Invalid detail mode in rate policy '{entry}'")
    if detail_mode == "*":
        return None
    return detail_mode  # type: ignore[return-value]


def _parse_rate(raw_rate: str, entry: str) -> float:
    try:
        rate = float(raw_rate)
    except ValueError as exc:
        raise RateLimitPolicyError(f"Invalid rate value in rate policy '{entry}'") from exc
    if rate < 0 or rate > _RATE_LIMIT_PER_SECOND_MAX:
        raise RateLimitPolicyError(
            (
                "Rate-limit policy values must be between "
                f"0 and {_RATE_LIMIT_PER_SECOND_MAX}: '{entry}'"
            )
        )
    return rate
