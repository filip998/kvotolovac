from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter, defaultdict

from thefuzz import fuzz

from ..models.schemas import NormalizedOdds, RawOddsData

logger = logging.getLogger(__name__)

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

_NBA_CANONICAL_TEAMS: dict[str, str] = {
    "atlanta": "Atlanta Hawks",
    "atlanta hawks": "Atlanta Hawks",
    "boston": "Boston Celtics",
    "boston celtics": "Boston Celtics",
    "brooklyn": "Brooklyn Nets",
    "brooklyn nets": "Brooklyn Nets",
    "charlotte": "Charlotte Hornets",
    "charlotte hornets": "Charlotte Hornets",
    "chicago": "Chicago Bulls",
    "chicago bulls": "Chicago Bulls",
    "cleveland": "Cleveland Cavaliers",
    "cleveland cavaliers": "Cleveland Cavaliers",
    "dallas": "Dallas Mavericks",
    "dallas mavericks": "Dallas Mavericks",
    "denver": "Denver Nuggets",
    "denver nuggets": "Denver Nuggets",
    "detroit": "Detroit Pistons",
    "detroit pistons": "Detroit Pistons",
    "golden state": "Golden State Warriors",
    "golden state warriors": "Golden State Warriors",
    "houston": "Houston Rockets",
    "houston rockets": "Houston Rockets",
    "indiana": "Indiana Pacers",
    "indiana pacers": "Indiana Pacers",
    "la clippers": "Los Angeles Clippers",
    "los angeles clippers": "Los Angeles Clippers",
    "la lakers": "Los Angeles Lakers",
    "los angeles lakers": "Los Angeles Lakers",
    "memphis": "Memphis Grizzlies",
    "memphis grizzlies": "Memphis Grizzlies",
    "miami": "Miami Heat",
    "miami heat": "Miami Heat",
    "milwaukee": "Milwaukee Bucks",
    "milwaukee bucks": "Milwaukee Bucks",
    "minnesota": "Minnesota Timberwolves",
    "minnesota timberwolves": "Minnesota Timberwolves",
    "new orleans": "New Orleans Pelicans",
    "new orleans pelicans": "New Orleans Pelicans",
    "new york": "New York Knicks",
    "new york knicks": "New York Knicks",
    "okc": "Oklahoma City Thunder",
    "oklahoma city": "Oklahoma City Thunder",
    "oklahoma city thunder": "Oklahoma City Thunder",
    "orlando": "Orlando Magic",
    "orlando magic": "Orlando Magic",
    "philadelphia": "Philadelphia 76ers",
    "philadelphia 76ers": "Philadelphia 76ers",
    "phoenix": "Phoenix Suns",
    "phoenix suns": "Phoenix Suns",
    "portland": "Portland Trail Blazers",
    "portland trail blazers": "Portland Trail Blazers",
    "sacramento": "Sacramento Kings",
    "sacramento kings": "Sacramento Kings",
    "san antonio": "San Antonio Spurs",
    "san antonio spurs": "San Antonio Spurs",
    "toronto": "Toronto Raptors",
    "toronto raptors": "Toronto Raptors",
    "utah": "Utah Jazz",
    "utah jazz": "Utah Jazz",
    "washington": "Washington Wizards",
    "washington wizards": "Washington Wizards",
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
    "durant": "Kevin Durant",
}

FUZZY_THRESHOLD = 75
_CANONICAL_LEAGUES: dict[str, str] = {
    "usa nba": "nba",
}

_MARKET_TYPE_MAPPING: dict[str, str] = {
    "player_points": "player_points",
    "player points": "player_points",
    "points": "player_points",
    "player_rebounds": "player_rebounds",
    "player rebounds": "player_rebounds",
    "rebounds": "player_rebounds",
    "player_assists": "player_assists",
    "player assists": "player_assists",
    "assists": "player_assists",
    "player_3points": "player_3points",
    "player 3points": "player_3points",
    "player 3-points": "player_3points",
    "player 3 points": "player_3points",
    "3points": "player_3points",
    "3-points": "player_3points",
    "3 points": "player_3points",
    "player_steals": "player_steals",
    "player steals": "player_steals",
    "steals": "player_steals",
    "player_blocks": "player_blocks",
    "player blocks": "player_blocks",
    "blocks": "player_blocks",
    "player_points_rebounds": "player_points_rebounds",
    "player points rebounds": "player_points_rebounds",
    "player points + rebounds": "player_points_rebounds",
    "points rebounds": "player_points_rebounds",
    "points + rebounds": "player_points_rebounds",
    "player_points_assists": "player_points_assists",
    "player points assists": "player_points_assists",
    "player points + assists": "player_points_assists",
    "points assists": "player_points_assists",
    "points + assists": "player_points_assists",
    "player_rebounds_assists": "player_rebounds_assists",
    "player rebounds assists": "player_rebounds_assists",
    "player rebounds + assists": "player_rebounds_assists",
    "rebounds assists": "player_rebounds_assists",
    "rebounds + assists": "player_rebounds_assists",
    "player_points_rebounds_assists": "player_points_rebounds_assists",
    "player points rebounds assists": "player_points_rebounds_assists",
    "player points + rebounds + assists": "player_points_rebounds_assists",
    "points rebounds assists": "player_points_rebounds_assists",
    "points + rebounds + assists": "player_points_rebounds_assists",
    "pra": "player_points_rebounds_assists",
    "player pra": "player_points_rebounds_assists",
    "player_points_milestones": "player_points_milestones",
    "player points milestones": "player_points_milestones",
    "player points milestone": "player_points_milestones",
    "player points ladder": "player_points_milestones",
    "player_points_ladder": "player_points_milestones",
    "game_total": "game_total",
    "game total": "game_total",
    "total": "game_total",
}


def normalize_team_name(raw_name: str, league_id: str | None = None) -> str:
    key = " ".join(raw_name.strip().lower().split())
    team_mapping = dict(_CANONICAL_TEAMS)
    if normalize_league_id(league_id or "") == "nba":
        team_mapping.update(_NBA_CANONICAL_TEAMS)

    if key in team_mapping:
        return team_mapping[key]
    # Fuzzy match against known names
    best_score = 0
    best_match = raw_name.strip()
    for canon_key, canon_val in team_mapping.items():
        score = fuzz.token_set_ratio(key, canon_key)
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


def _normalize_person_tokens(name: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s-]+", " ", ascii_name.lower().replace("_", " "))
    return [token.strip("-") for token in cleaned.split() if token.strip("-")]


def _player_name_parts(name: str) -> tuple[list[str], str] | None:
    tokens = _normalize_person_tokens(name)
    if len(tokens) < 2:
        return None
    return tokens[:-1], tokens[-1]


def _player_name_completeness(first_tokens: list[str]) -> int:
    return sum(len(token) for token in first_tokens if len(token) > 1)


def _has_multiple_initials(first_tokens: list[str]) -> bool:
    return len(first_tokens) > 1 and all(len(token) == 1 for token in first_tokens)


def _is_contextual_player_match(raw_name: str, candidate_name: str) -> bool:
    raw_parts = _player_name_parts(raw_name)
    candidate_parts = _player_name_parts(candidate_name)
    if not raw_parts or not candidate_parts:
        return False

    raw_first_tokens, raw_last_name = raw_parts
    candidate_first_tokens, candidate_last_name = candidate_parts
    if (
        not raw_first_tokens
        or not candidate_first_tokens
        or raw_last_name != candidate_last_name
        or _has_multiple_initials(raw_first_tokens)
    ):
        return False

    raw_first = raw_first_tokens[0]
    candidate_first = candidate_first_tokens[0]
    if raw_first == candidate_first:
        return True
    if len(raw_first) == 1:
        return False
    if candidate_first.startswith(raw_first) or raw_first.startswith(candidate_first):
        return True
    return raw_first[0] == candidate_first[0] and fuzz.ratio(raw_first, candidate_first) >= 80


def _resolve_contextual_player_names(raw_list: list[RawOddsData]) -> list[RawOddsData]:
    names_by_match_and_surname: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if not raw.player_name:
            continue

        parts = _player_name_parts(raw.player_name)
        if not parts:
            continue

        league_id = normalize_league_id(raw.league_id)
        home_team = normalize_team_name(raw.home_team, league_id)
        away_team = normalize_team_name(raw.away_team, league_id)
        match_id = generate_match_id(home_team, away_team, league_id)
        _, last_name = parts
        names_by_match_and_surname[(match_id, last_name)][raw.player_name.strip()] += 1

    replacements: dict[tuple[str, str], str] = {}

    for match_and_surname, name_counts in names_by_match_and_surname.items():
        observed_names = list(name_counts)
        for raw_name in observed_names:
            raw_parts = _player_name_parts(raw_name)
            if not raw_parts:
                continue

            raw_first_tokens, _ = raw_parts
            raw_completeness = _player_name_completeness(raw_first_tokens)
            candidates = [
                candidate
                for candidate in observed_names
                if candidate != raw_name and _is_contextual_player_match(raw_name, candidate)
            ]
            if not candidates:
                continue
            candidate_first_names = {
                _player_name_parts(candidate)[0][0] for candidate in candidates if _player_name_parts(candidate)
            }
            if len(candidate_first_names) > 1:
                continue

            ranked = sorted(
                candidates,
                key=lambda candidate: (
                    name_counts[candidate],
                    _player_name_completeness(_player_name_parts(candidate)[0]),
                    len(candidate.strip()),
                    candidate,
                ),
                reverse=True,
            )
            best_candidate = ranked[0]
            best_parts = _player_name_parts(best_candidate)
            if not best_parts:
                continue
            best_completeness = _player_name_completeness(best_parts[0])
            if best_completeness < raw_completeness:
                continue
            if best_completeness == raw_completeness and name_counts[best_candidate] <= name_counts[raw_name]:
                continue

            if len(ranked) > 1:
                runner_up = ranked[1]
                runner_up_parts = _player_name_parts(runner_up)
                if runner_up_parts and (
                    name_counts[runner_up],
                    _player_name_completeness(runner_up_parts[0]),
                    len(runner_up.strip()),
                ) == (
                    name_counts[best_candidate],
                    best_completeness,
                    len(best_candidate.strip()),
                ):
                    continue

            replacements[(match_and_surname[0], raw_name)] = best_candidate

    resolved: list[RawOddsData] = []
    for raw in raw_list:
        if not raw.player_name:
            resolved.append(raw)
            continue

        league_id = normalize_league_id(raw.league_id)
        home_team = normalize_team_name(raw.home_team, league_id)
        away_team = normalize_team_name(raw.away_team, league_id)
        match_id = generate_match_id(home_team, away_team, league_id)
        replacement = replacements.get((match_id, raw.player_name.strip()))
        if not replacement:
            resolved.append(raw)
            continue

        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                home_team=raw.home_team,
                away_team=raw.away_team,
                market_type=raw.market_type,
                player_name=replacement,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved


def normalize_league_id(raw_league_id: str) -> str:
    key = " ".join(
        raw_league_id.strip().lower().replace("_", " ").replace("-", " ").split()
    )
    return _CANONICAL_LEAGUES.get(key, key)


def generate_match_id(home_team: str, away_team: str, league_id: str) -> str:
    raw = f"{league_id}:{home_team}:{away_team}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def normalize_market_type(raw_type: str) -> str:
    key = raw_type.strip().lower().replace("&", "+").replace("+", " + ")
    key = " ".join(key.split())
    return _MARKET_TYPE_MAPPING.get(key, key)


def _is_unresolved_shared_platform_prop(raw: RawOddsData) -> bool:
    return bool(raw.player_name and raw.away_team.strip() == raw.player_name.strip())


def _build_canonical_matchups(
    raw_list: list[RawOddsData],
) -> dict[tuple[str, str | None, tuple[str, str]], tuple[str, str]]:
    counts: dict[tuple[str, str | None, tuple[str, str]], dict[tuple[str, str], int]] = {}

    for raw in raw_list:
        if _is_unresolved_shared_platform_prop(raw):
            continue

        league_id = normalize_league_id(raw.league_id)
        home_team = normalize_team_name(raw.home_team, league_id)
        away_team = normalize_team_name(raw.away_team, league_id)
        if not home_team or not away_team or home_team == away_team:
            continue

        matchup_key = tuple(sorted((home_team, away_team)))
        slot = (league_id, raw.start_time, matchup_key)
        orientation = (home_team, away_team)
        counts.setdefault(slot, {})[orientation] = counts.setdefault(slot, {}).get(
            orientation,
            0,
        ) + 1

    canonical: dict[tuple[str, str | None, tuple[str, str]], tuple[str, str]] = {}
    for slot, orientations in counts.items():
        canonical[slot] = min(
            orientations.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
    return canonical


def _resolve_shared_platform_matchups(raw_list: list[RawOddsData]) -> list[RawOddsData]:
    canonical_matchups = _build_canonical_matchups(raw_list)
    matchups_by_slot: dict[tuple[str, str | None], list[tuple[str, str]]] = {}
    for (league_id, start_time, _matchup_key), matchup in canonical_matchups.items():
        matchups_by_slot.setdefault((league_id, start_time), []).append(matchup)

    resolved: list[RawOddsData] = []

    for raw in raw_list:
        league_id = normalize_league_id(raw.league_id)

        if not _is_unresolved_shared_platform_prop(raw):
            home_team = normalize_team_name(raw.home_team, league_id)
            away_team = normalize_team_name(raw.away_team, league_id)
            matchup_key = tuple(sorted((home_team, away_team)))
            canonical = canonical_matchups.get((league_id, raw.start_time, matchup_key))
            resolved.append(raw)
            if canonical:
                resolved[-1] = RawOddsData(
                    bookmaker_id=raw.bookmaker_id,
                    league_id=raw.league_id,
                    home_team=canonical[0],
                    away_team=canonical[1],
                    market_type=raw.market_type,
                    player_name=raw.player_name,
                    threshold=raw.threshold,
                    over_odds=raw.over_odds,
                    under_odds=raw.under_odds,
                    start_time=raw.start_time,
                )
            continue

        slot = (league_id, raw.start_time)
        known_team = normalize_team_name(raw.home_team, league_id)
        candidates = [
            matchup
            for matchup in matchups_by_slot.get(slot, [])
            if known_team in matchup
        ]

        if len(candidates) != 1:
            logger.warning(
                "Dropping unresolved shared-platform prop for %s (%s, %s)",
                raw.player_name,
                raw.bookmaker_id,
                known_team,
            )
            continue

        home_team, away_team = candidates[0]
        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                home_team=home_team,
                away_team=away_team,
                market_type=raw.market_type,
                player_name=raw.player_name,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved
def normalize_odds(raw_list: list[RawOddsData]) -> list[NormalizedOdds]:
    results: list[NormalizedOdds] = []
    resolved_raw_list = _resolve_contextual_player_names(
        _resolve_shared_platform_matchups(raw_list)
    )
    for raw in resolved_raw_list:
        league_id = normalize_league_id(raw.league_id)
        home = normalize_team_name(raw.home_team, league_id)
        away = normalize_team_name(raw.away_team, league_id)
        match_id = generate_match_id(home, away, league_id)
        player = normalize_player_name(raw.player_name)
        market = normalize_market_type(raw.market_type)

        results.append(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=league_id,
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
