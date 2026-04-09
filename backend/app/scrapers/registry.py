from __future__ import annotations

from .base import BaseScraper


class ScraperRegistry:
    """Discovers and manages scraper instances."""

    def __init__(self) -> None:
        self._scrapers: dict[str, BaseScraper] = {}

    def register(self, scraper: BaseScraper) -> None:
        self._scrapers[scraper.get_bookmaker_id()] = scraper

    def get(self, bookmaker_id: str) -> BaseScraper | None:
        return self._scrapers.get(bookmaker_id)

    def get_all(self) -> list[BaseScraper]:
        return list(self._scrapers.values())

    def get_ids(self) -> list[str]:
        return list(self._scrapers.keys())


registry = ScraperRegistry()
