from __future__ import annotations

import abc
from ..models.schemas import RawOddsData, RawOutcomeOffer


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
