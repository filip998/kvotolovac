from __future__ import annotations

import abc
from ..models.schemas import RawOddsData


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

    @abc.abstractmethod
    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        """Scrape odds for a given league and return raw data."""
        ...
