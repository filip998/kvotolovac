from __future__ import annotations

import hashlib
from typing import Optional

from thefuzz import fuzz

from ..models.schemas import NormalizedOdds, RawOddsData

# Canonical name mappings — maps known variations to a standard form.
_CANONICAL_TEAMS: dict[str, str] = {
    "olympiacos": "Olympiacos",
    "olympiakos": "Olympiacos",
    "real madrid": "Real Madrid",
    "fenerbahce": "Fenerbahce",
    "fc barcelona": "FC Barcelona",
    "barcelona": "FC Barcelona",
    "partizan": "Partizan",
    "crvena zvezda": "Crvena Zvezda",
    "red star": "Crvena Zvezda",
    "panathinaikos": "Panathinaikos",
    "anadolu efes": "Anadolu Efes",
    "efes": "Anadolu Efes",
    "bayern munich": "Bayern Munich",
    "bayern": "Bayern Munich",
    "maccabi tel aviv": "Maccabi Tel Aviv",
    "maccabi": "Maccabi Tel Aviv",
}

# Known player canonical names — last name is the key
_CANONICAL_PLAYERS: dict[str, str] = {
    "vezenkov": "Sasha Vezenkov",
    "campazzo": "Facundo Campazzo",
    "sloukas": "Kostas Sloukas",
    "tavares": "Walter Tavares",
    "hayes-davis": "Nigel Hayes-Davis",
    "calathes": "Nick Calathes",
    "mirotic": "Nikola Mirotic",
    "lundberg": "Iffe Lundberg",
    "jovic": "Nikola Jovic",
    "petrusev": "Filip Petrusev",
    "lessort": "Mathias Lessort",
    "blossomgame": "Jaron Blossomgame",
    "lucic": "Vladimir Lucic",
    "lee": "Saben Lee",
}

FUZZY_THRESHOLD = 75


def normalize_team_name(raw_name: str) -> str:
    key = raw_name.strip().lower()
    if key in _CANONICAL_TEAMS:
        return _CANONICAL_TEAMS[key]
    # Fuzzy match against known names
    best_score = 0
    best_match = raw_name.strip()
    for canon_key, canon_val in _CANONICAL_TEAMS.items():
        score = fuzz.ratio(key, canon_key)
        if score > best_score and score >= FUZZY_THRESHOLD:
            best_score = score
            best_match = canon_val
    return best_match


def normalize_player_name(raw_name: str | None) -> str | None:
    if not raw_name:
        return None
    name = raw_name.strip()
    name_lower = name.lower()

    # Check if any canonical last name appears in the raw name
    for last_name, canonical in _CANONICAL_PLAYERS.items():
        if last_name in name_lower:
            return canonical

    # Fuzzy match against all canonical full names
    best_score = 0
    best_match = name
    for canonical in _CANONICAL_PLAYERS.values():
        score = fuzz.token_sort_ratio(name_lower, canonical.lower())
        if score > best_score and score >= FUZZY_THRESHOLD:
            best_score = score
            best_match = canonical
    return best_match


def generate_match_id(home_team: str, away_team: str, league_id: str) -> str:
    raw = f"{league_id}:{home_team}:{away_team}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def normalize_market_type(raw_type: str) -> str:
    mapping = {
        "player_points": "player_points",
        "player points": "player_points",
        "points": "player_points",
        "game_total": "game_total",
        "total": "game_total",
    }
    return mapping.get(raw_type.strip().lower(), raw_type.strip().lower())


def normalize_odds(raw_list: list[RawOddsData]) -> list[NormalizedOdds]:
    results: list[NormalizedOdds] = []
    for raw in raw_list:
        home = normalize_team_name(raw.home_team)
        away = normalize_team_name(raw.away_team)
        match_id = generate_match_id(home, away, raw.league_id)
        player = normalize_player_name(raw.player_name)
        market = normalize_market_type(raw.market_type)

        results.append(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                home_team=home,
                away_team=away,
                market_type=market,
                player_name=player,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )
    return results
