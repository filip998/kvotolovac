from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Literal

from ..models.schemas import RawOddsData, RawOutcomeOffer


ScraperLane = Literal["threshold_odds", "outcome_offer"]


@dataclass(frozen=True)
class ScraperCapability:
    """A scrape capability exposed by a bookmaker scraper."""

    lane: ScraperLane
    sport: str
    league_id: str | None = None

    def __post_init__(self) -> None:
        if self.lane == "threshold_odds" and not self.league_id:
            raise ValueError("threshold_odds capabilities require league_id")
        if self.lane == "outcome_offer" and self.league_id is not None:
            raise ValueError("outcome_offer capabilities must not set league_id")

    @classmethod
    def threshold_odds(cls, *, sport: str, league_id: str) -> "ScraperCapability":
        return cls(lane="threshold_odds", sport=sport, league_id=league_id)

    @classmethod
    def outcome_offer(cls, *, sport: str) -> "ScraperCapability":
        return cls(lane="outcome_offer", sport=sport)


class BaseScraper(abc.ABC):
    """Abstract base class for all bookmaker scrapers."""

    @abc.abstractmethod
    def get_bookmaker_id(self) -> str:
        """Return the unique bookmaker identifier."""
        ...

    @abc.abstractmethod
    def get_bookmaker_name(self) -> str:
        """Return human-readable bookmaker name."""
        ...

    @abc.abstractmethod
    def get_supported_leagues(self) -> list[str]:
        """Return list of league IDs this scraper supports."""
        ...

    def get_supported_odds_sports(self) -> list[str]:
        """Return sports supported by the threshold-odds scraper lane."""
        return list(self.get_supported_odds_leagues())

    def get_supported_odds_leagues(self) -> dict[str, list[str]]:
        """Return threshold-odds league IDs grouped by sport."""
        leagues = self.get_supported_leagues()
        return {"basketball": leagues} if leagues else {}

    def get_scraper_capabilities(self) -> list[ScraperCapability]:
        """Return explicit scrape capabilities used by the unified pipeline."""
        capabilities: list[ScraperCapability] = []
        seen: set[ScraperCapability] = set()

        def add(capability: ScraperCapability) -> None:
            if capability not in seen:
                seen.add(capability)
                capabilities.append(capability)

        for sport, league_ids in self.get_supported_odds_leagues().items():
            for league_id in league_ids:
                add(ScraperCapability.threshold_odds(sport=sport, league_id=league_id))
        for sport in self.get_supported_outcome_sports():
            add(ScraperCapability.outcome_offer(sport=sport))

        return capabilities

    def set_runtime_rate_limit(self, rate_limit_per_second: float) -> None:
        http_client = getattr(self, "_http", None)
        if http_client is not None and hasattr(http_client, "rate_limit_per_second"):
            http_client.rate_limit_per_second = rate_limit_per_second

    def set_runtime_detail_mode(self, detail_mode: Literal["partial", "full"]) -> None:
        if hasattr(self, "_detail_mode"):
            setattr(self, "_detail_mode", detail_mode)

    def set_runtime_analysis_markets(
        self,
        analysis_markets: list[str],
        *,
        scrape_market_scope: str = "all",
    ) -> None:
        if hasattr(self, "_analysis_markets"):
            setattr(self, "_analysis_markets", list(analysis_markets))
        if hasattr(self, "_scrape_market_scope"):
            setattr(self, "_scrape_market_scope", scrape_market_scope)

    @abc.abstractmethod
    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        """Scrape odds for a given league and return raw data."""
        ...

    def get_supported_outcome_sports(self) -> list[str]:
        """Return sports supported by the generic outcome-offer scraper lane."""
        return []

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        """Scrape generic outcome offers for sports that do not fit RawOddsData."""
        del sport
        return []
