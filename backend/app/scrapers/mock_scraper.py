from __future__ import annotations

from datetime import datetime, timedelta

from .base import BaseScraper
from ..models.schemas import RawOddsData

# ── Realistic Euroleague mock data ─────────────────────────
# Each bookmaker has slightly different player name spellings and thresholds
# to simulate real-world discrepancies.

_GAMES = [
    {
        "home": "Olympiacos",
        "away": "Real Madrid",
        "start": (datetime.utcnow() + timedelta(hours=3)).isoformat(),
    },
    {
        "home": "Fenerbahce",
        "away": "FC Barcelona",
        "start": (datetime.utcnow() + timedelta(hours=5)).isoformat(),
    },
    {
        "home": "Partizan",
        "away": "Crvena Zvezda",
        "start": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
    },
    {
        "home": "Panathinaikos",
        "away": "Anadolu Efes",
        "start": (datetime.utcnow() + timedelta(hours=26)).isoformat(),
    },
    {
        "home": "Bayern Munich",
        "away": "Maccabi Tel Aviv",
        "start": (datetime.utcnow() + timedelta(hours=48)).isoformat(),
    },
]

# Player markets per game — each bookmaker has INTENTIONALLY different thresholds
# to create detectable discrepancies.
_PLAYER_MARKETS: dict[str, list[dict]] = {
    # ── Mozzart ────────────────────────────────────────────
    "mozzart": [
        # Game 0: Olympiacos vs Real Madrid
        {"game": 0, "player": "Sasha Vezenkov", "threshold": 18.5, "over": 1.85, "under": 1.95},
        {"game": 0, "player": "Facundo Campazzo", "threshold": 12.5, "over": 1.90, "under": 1.90},
        {"game": 0, "player": "Kostas Sloukas", "threshold": 14.5, "over": 1.80, "under": 2.00},
        {"game": 0, "player": "Walter Tavares", "threshold": 10.5, "over": 1.75, "under": 2.05},
        # Game 1: Fenerbahce vs Barcelona
        {"game": 1, "player": "Nigel Hayes-Davis", "threshold": 15.5, "over": 1.85, "under": 1.95},
        {"game": 1, "player": "Nick Calathes", "threshold": 11.5, "over": 1.90, "under": 1.90},
        {"game": 1, "player": "Nikola Mirotic", "threshold": 19.5, "over": 1.80, "under": 2.00},
        # Game 2: Partizan vs Crvena Zvezda (Belgrade derby!)
        {"game": 2, "player": "Iffe Lundberg", "threshold": 16.5, "over": 1.85, "under": 1.95},
        {"game": 2, "player": "Nikola Jovic", "threshold": 13.5, "over": 1.90, "under": 1.90},
        {"game": 2, "player": "Filip Petrusev", "threshold": 14.5, "over": 1.75, "under": 2.05},
        # Game 3: Panathinaikos vs Efes
        {"game": 3, "player": "Mathias Lessort", "threshold": 13.5, "over": 1.85, "under": 1.95},
        {"game": 3, "player": "Jaron Blossomgame", "threshold": 12.5, "over": 1.80, "under": 2.00},
        # Game 4: Bayern vs Maccabi
        {"game": 4, "player": "Vladimir Lucic", "threshold": 14.5, "over": 1.90, "under": 1.90},
        {"game": 4, "player": "Saben Lee", "threshold": 11.5, "over": 1.85, "under": 1.95},
    ],
    # ── Meridian — DIFFERENT thresholds (creates gaps!) ────
    "meridian": [
        {"game": 0, "player": "S. Vezenkov", "threshold": 20.5, "over": 1.90, "under": 1.90},
        {"game": 0, "player": "F. Campazzo", "threshold": 14.5, "over": 1.85, "under": 1.95},
        {"game": 0, "player": "K. Sloukas", "threshold": 14.5, "over": 1.75, "under": 2.05},
        {"game": 0, "player": "W. Tavares", "threshold": 12.5, "over": 1.80, "under": 2.00},
        {"game": 1, "player": "N. Hayes-Davis", "threshold": 17.5, "over": 1.90, "under": 1.90},
        {"game": 1, "player": "Nikolas Calathes", "threshold": 13.5, "over": 1.85, "under": 1.95},
        {"game": 1, "player": "N. Mirotic", "threshold": 19.5, "over": 1.90, "under": 1.90},
        {"game": 2, "player": "I. Lundberg", "threshold": 18.5, "over": 1.80, "under": 2.00},
        {"game": 2, "player": "N. Jovic", "threshold": 15.5, "over": 1.85, "under": 1.95},
        {"game": 2, "player": "F. Petrusev", "threshold": 16.5, "over": 1.80, "under": 2.00},
        {"game": 3, "player": "M. Lessort", "threshold": 15.5, "over": 1.80, "under": 2.00},
        {"game": 3, "player": "J. Blossomgame", "threshold": 12.5, "over": 1.85, "under": 1.95},
        {"game": 4, "player": "V. Lucic", "threshold": 16.5, "over": 1.85, "under": 1.95},
        {"game": 4, "player": "S. Lee", "threshold": 13.5, "over": 1.80, "under": 2.00},
    ],
    # ── MaxBet — another set of thresholds ─────────────────
    "maxbet": [
        {"game": 0, "player": "Vezenkov S.", "threshold": 19.5, "over": 1.88, "under": 1.92},
        {"game": 0, "player": "Campazzo F.", "threshold": 13.5, "over": 1.87, "under": 1.93},
        {"game": 0, "player": "Sloukas K.", "threshold": 15.5, "over": 1.82, "under": 1.98},
        {"game": 0, "player": "Tavares W.", "threshold": 11.5, "over": 1.78, "under": 2.02},
        {"game": 1, "player": "Hayes-Davis N.", "threshold": 16.5, "over": 1.88, "under": 1.92},
        {"game": 1, "player": "Calathes N.", "threshold": 12.5, "over": 1.87, "under": 1.93},
        {"game": 1, "player": "Mirotic N.", "threshold": 20.5, "over": 1.82, "under": 1.98},
        {"game": 2, "player": "Lundberg I.", "threshold": 17.5, "over": 1.83, "under": 1.97},
        {"game": 2, "player": "Jovic N.", "threshold": 14.5, "over": 1.88, "under": 1.92},
        {"game": 2, "player": "Petrusev F.", "threshold": 15.5, "over": 1.78, "under": 2.02},
        {"game": 3, "player": "Lessort M.", "threshold": 14.5, "over": 1.83, "under": 1.97},
        {"game": 3, "player": "Blossomgame J.", "threshold": 13.5, "over": 1.82, "under": 1.98},
        {"game": 4, "player": "Lucic V.", "threshold": 15.5, "over": 1.87, "under": 1.93},
        {"game": 4, "player": "Lee S.", "threshold": 12.5, "over": 1.83, "under": 1.97},
    ],
}

_BOOKMAKER_META = {
    "mozzart": ("Mozzart", "https://www.mozzartbet.com"),
    "meridian": ("Meridian", "https://www.meridianbet.rs"),
    "maxbet": ("MaxBet", "https://www.maxbet.rs"),
}


class MockScraper(BaseScraper):
    """Mock scraper returning realistic Euroleague basketball data."""

    def __init__(self, bookmaker_id: str) -> None:
        if bookmaker_id not in _BOOKMAKER_META:
            raise ValueError(f"Unknown mock bookmaker: {bookmaker_id}")
        self._bookmaker_id = bookmaker_id

    def get_bookmaker_id(self) -> str:
        return self._bookmaker_id

    def get_bookmaker_name(self) -> str:
        return _BOOKMAKER_META[self._bookmaker_id][0]

    def get_supported_leagues(self) -> list[str]:
        return ["euroleague"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "euroleague":
            return []

        markets = _PLAYER_MARKETS.get(self._bookmaker_id, [])
        results: list[RawOddsData] = []
        for m in markets:
            game = _GAMES[m["game"]]
            results.append(
                RawOddsData(
                    bookmaker_id=self._bookmaker_id,
                    league_id=league_id,
                    sport="basketball",
                    home_team=game["home"],
                    away_team=game["away"],
                    market_type="player_points",
                    player_name=m["player"],
                    threshold=m["threshold"],
                    over_odds=m["over"],
                    under_odds=m["under"],
                    start_time=game["start"],
                )
            )
        return results
