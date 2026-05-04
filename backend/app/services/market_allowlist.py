from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import re
from typing import Iterable

from ..models.schemas import ScrapeMarketScope


ALL_MARKETS = "all"
DEFAULT_ANALYSIS_MARKETS = (ALL_MARKETS,)
LEGACY_PLAYER_PROPS_ANALYSIS_MARKETS = ("*:player_*",)
MARKET_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "match_winner": ("tennis_match_winner",),
    "tennis_match_winner": ("match_winner",),
}

_TOKEN_RE = re.compile(r"^[a-z0-9_*.-]+:[a-z0-9_*.-]+$")

_OUTCOME_OFFER_MARKETS_BY_SPORT: dict[str, set[str]] = {
    "football": {
        "football_total_goals",
        "football_result",
        "football_double_chance",
        "football_result_double_chance",
    },
    "tennis": {
        "match_winner",
        "tennis_match_winner",
    },
}
# Keep this list in sync with outcome-offer market options so lane gating does
# not skip newly allowlisted markets before normalization can see them.


@dataclass(frozen=True)
class AnalysisMarketOption:
    token: str
    label: str
    sport: str | None = None


ANALYSIS_MARKET_OPTIONS: tuple[AnalysisMarketOption, ...] = (
    AnalysisMarketOption(
        token="basketball:player_*",
        label="Basketball player props",
        sport="basketball",
    ),
    AnalysisMarketOption(
        token="basketball:home_handicap_ot",
        label="Basketball handicap OT",
        sport="basketball",
    ),
    AnalysisMarketOption(
        token="basketball:game_total",
        label="Basketball totals",
        sport="basketball",
    ),
    AnalysisMarketOption(
        token="basketball:game_total_ot",
        label="Basketball totals OT",
        sport="basketball",
    ),
    AnalysisMarketOption(
        token="football:football_total_goals",
        label="Football goals/totals",
        sport="football",
    ),
    AnalysisMarketOption(
        token="football:football_result",
        label="Football result",
        sport="football",
    ),
    AnalysisMarketOption(
        token="football:football_double_chance",
        label="Football double chance",
        sport="football",
    ),
    AnalysisMarketOption(
        token="football:football_result_double_chance",
        label="Football result + double chance",
        sport="football",
    ),
    AnalysisMarketOption(
        token="tennis:tennis_match_winner",
        label="Tennis match winner",
        sport="tennis",
    ),
    AnalysisMarketOption(
        token="tennis:match_winner",
        label="Tennis match winner (generic)",
        sport="tennis",
    ),
)


class MarketAllowlistError(ValueError):
    """Raised when configured analysis market filters cannot be parsed."""


@dataclass(frozen=True)
class _MarketFilter:
    sport: str
    pattern: str

    def applies_to_sport(self, sport: str) -> bool:
        return self.sport == "*" or self.sport == sport


@dataclass(frozen=True)
class MarketAllowlist:
    tokens: tuple[str, ...]
    filters: tuple[_MarketFilter, ...]

    @property
    def allows_all(self) -> bool:
        return self.tokens == DEFAULT_ANALYSIS_MARKETS

    def allows(self, *, sport: str, market_type: str) -> bool:
        if self.allows_all:
            return True
        normalized_sport = sport.strip().lower()
        normalized_market_type = market_type.strip().lower()
        return any(
            market_filter.applies_to_sport(normalized_sport)
            and fnmatchcase(normalized_market_type, market_filter.pattern)
            for market_filter in self.filters
        )

    def has_filter_for_sport(self, sport: str) -> bool:
        if self.allows_all:
            return True
        normalized_sport = sport.strip().lower()
        return any(
            market_filter.applies_to_sport(normalized_sport)
            for market_filter in self.filters
        )

    def may_include_outcome_offer_markets(self, sport: str) -> bool:
        if self.allows_all:
            return True

        normalized_sport = sport.strip().lower()
        relevant_filters = [
            market_filter
            for market_filter in self.filters
            if market_filter.applies_to_sport(normalized_sport)
        ]
        if not relevant_filters:
            return False

        known_markets = _OUTCOME_OFFER_MARKETS_BY_SPORT.get(normalized_sport)
        if not known_markets:
            return True

        return any(
            fnmatchcase(market_type, market_filter.pattern)
            for market_filter in relevant_filters
            for market_type in known_markets
        )


def legacy_analysis_markets_for_scope(
    scrape_market_scope: ScrapeMarketScope | str,
) -> tuple[str, ...]:
    if scrape_market_scope == "player_props":
        return LEGACY_PLAYER_PROPS_ANALYSIS_MARKETS
    return DEFAULT_ANALYSIS_MARKETS


def split_analysis_markets(raw: str | Iterable[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [
            token.strip()
            for part in raw.split(";")
            for token in part.split(",")
            if token.strip()
        ]
    return [str(token).strip() for token in raw if str(token).strip()]


def normalize_analysis_markets(
    raw: str | Iterable[str] | None,
    *,
    legacy_scrape_market_scope: ScrapeMarketScope | str = "all",
) -> tuple[str, ...]:
    tokens = split_analysis_markets(raw)
    if not tokens:
        return legacy_analysis_markets_for_scope(legacy_scrape_market_scope)

    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized_token = token.lower()
        if normalized_token == ALL_MARKETS:
            if len(tokens) > 1:
                raise MarketAllowlistError(
                    "'all' cannot be combined with specific analysis market filters"
                )
            return DEFAULT_ANALYSIS_MARKETS
        if not _TOKEN_RE.fullmatch(normalized_token):
            raise MarketAllowlistError(
                "Analysis market filters must be 'all' or '<sport>:<market_pattern>'"
            )
        if normalized_token not in seen:
            seen.add(normalized_token)
            normalized.append(normalized_token)

    return tuple(normalized) or DEFAULT_ANALYSIS_MARKETS


def analysis_market_allowlist(
    raw: str | Iterable[str] | None,
    *,
    legacy_scrape_market_scope: ScrapeMarketScope | str = "all",
) -> MarketAllowlist:
    tokens = normalize_analysis_markets(
        raw,
        legacy_scrape_market_scope=legacy_scrape_market_scope,
    )
    if tokens == DEFAULT_ANALYSIS_MARKETS:
        return MarketAllowlist(tokens=tokens, filters=())

    filters = tuple(
        _MarketFilter(sport=sport, pattern=pattern)
        for sport, pattern in (token.split(":", 1) for token in tokens)
    )
    return MarketAllowlist(tokens=tokens, filters=filters)
