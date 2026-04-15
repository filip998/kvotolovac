from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from functools import lru_cache

from rapidfuzz import fuzz

from ..models.schemas import NormalizedOdds, RawOddsData, UnresolvedOddsDiagnostic
from .text_normalizer import (
    compact_identity_text,
    normalize_identity_text,
    tokenize_identity_text,
)

logger = logging.getLogger(__name__)

# Canonical name mappings — maps known variations to a standard form.
_CANONICAL_TEAMS: dict[str, str] = {
    "olympiacos": "Olympiacos",
    "olympiakos": "Olympiacos",
    "real madrid": "Real Madrid",
    "fenerbahce": "Fenerbahce",
    "fenerbahce istanbul": "Fenerbahce",
    "fc barcelona": "FC Barcelona",
    "barcelona": "FC Barcelona",
    "partizan": "Partizan",
    "crvena zvezda": "Crvena Zvezda",
    "crv zvezda": "Crvena Zvezda",
    "kk crvena zvezda": "Crvena Zvezda",
    "red star": "Crvena Zvezda",
    "panathinaikos": "Panathinaikos",
    "anadolu efes": "Anadolu Efes",
    "efes": "Anadolu Efes",
    "bayern munich": "Bayern Munich",
    "bayern": "Bayern Munich",
    "maccabi tel aviv": "Maccabi Tel Aviv",
    "maccabi": "Maccabi Tel Aviv",
    "asvel": "Asvel",
    "asvel lyon-villeurbanne": "Asvel",
    "lyon-villeurb.": "Asvel",
    "lyon-villeurbanne": "Asvel",
    "universitatea cluj": "Universitatea Cluj",
    "cluj napoc": "Universitatea Cluj",
    "cluj napoca": "Universitatea Cluj",
    "buducnost": "Buducnost",
    "buducnost voli": "Buducnost",
    "kk bosna": "KK Bosna",
    "ostrow": "Ostrow Wielkopolski",
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

_COMPETITION_CANONICAL_TEAMS: dict[str, dict[str, str]] = {
    "nba": _NBA_CANONICAL_TEAMS,
    "argentina_1": {
        "obras": "Obras Sanitarias",
        "obras sanitarias": "Obras Sanitarias",
        "instituto": "Instituto de Cordoba",
        "instituto de cordoba": "Instituto de Cordoba",
        "inst de cordoba": "Instituto de Cordoba",
    },
    "korisliiga": {
        "uu korihait uusikaupunki": "Korihait Uusikaupunki",
        "uu korihait": "Korihait Uusikaupunki",
        "uu-korihait": "Korihait Uusikaupunki",
        "korihait": "Korihait Uusikaupunki",
        "korihait u.": "Korihait Uusikaupunki",
        "salon vilpas": "Salon Vilpas",
        "salon vilpas vikings": "Salon Vilpas",
    },
    "portoriko_1": {
        "capitanes de arecibo": "Capitanes de Arecibo",
        "capitanes de a": "Capitanes de Arecibo",
    },
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
    "nba play offs": "nba",
    "nba promotion play offs": "nba",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "aba liga winners stage": "aba_liga",
    "aba liga losers stage": "aba_liga",
    "aba liga plej of": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
    "italija 1": "italy",
    "italy lega a": "italy",
    "finska 1": "korisliiga",
    "finska 1 plej of": "korisliiga",
    "finnish league": "korisliiga",
    "finland play offs": "korisliiga",
    "finland korisliiga": "korisliiga",
    "korisliiga": "korisliiga",
    "balkanbet tournament 486": "korisliiga",
    "nemačka 1": "germany",
    "germany bbl": "germany",
    "poljska 1": "poland",
    "argentina": "argentina_1",
    "argentina 1": "argentina_1",
    "turska 1": "turkey",
    "turkey super league": "turkey",
    "puerto rico": "portoriko_1",
    "puerto rico 1": "portoriko_1",
    "portoriko 1": "portoriko_1",
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
    "game_total_ot": "game_total_ot",
    "game total ot": "game_total_ot",
}


def _normalize_team_key(raw_name: str) -> str:
    return normalize_identity_text(raw_name)


@lru_cache(maxsize=None)
def _team_mapping_for_league(league_id: str | None) -> tuple[tuple[str, str], ...]:
    mapping = {
        _normalize_team_key(canon_key): canon_val
        for canon_key, canon_val in _CANONICAL_TEAMS.items()
    }

    competition_id = normalize_league_id(league_id or "")
    scoped_mapping = _COMPETITION_CANONICAL_TEAMS.get(competition_id, {})
    mapping.update(
        {
            _normalize_team_key(canon_key): canon_val
            for canon_key, canon_val in scoped_mapping.items()
        }
    )
    return tuple(mapping.items())


def normalize_team_name(raw_name: str, league_id: str | None = None) -> str:
    key = _normalize_team_key(raw_name)
    team_mapping = dict(_team_mapping_for_league(league_id))

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
    return tokenize_identity_text(name, keep_hyphens=True)


def _compact_person_name(name: str) -> str:
    return compact_identity_text(name)


_NAME_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


def _strip_trailing_suffixes(tokens: list[str]) -> list[str]:
    """Remove name suffixes (Jr, Sr, II, etc.) only from the end."""
    while tokens and tokens[-1] in _NAME_SUFFIXES:
        tokens = tokens[:-1]
    return tokens


def _sorted_compact_person_name(name: str) -> str | None:
    """Compact form with tokens sorted — order-independent identity."""
    tokens = _normalize_person_tokens(name)
    tokens = _strip_trailing_suffixes(tokens)
    if len(tokens) < 2:
        return None
    return "".join(sorted(t.replace("-", "") for t in tokens))


def _player_name_parts(name: str) -> tuple[list[str], str] | None:
    tokens = _normalize_person_tokens(name)
    tokens = _strip_trailing_suffixes(tokens)
    if len(tokens) < 2:
        return None
    return tokens[:-1], tokens[-1]


def _player_name_completeness(first_tokens: list[str]) -> int:
    return sum(len(token) for token in first_tokens if len(token) > 1)


def _has_multiple_initials(first_tokens: list[str]) -> bool:
    return len(first_tokens) > 1 and all(len(token) == 1 for token in first_tokens)


def _collapse_first_name_variants(first_names: set[str]) -> set[str]:
    collapsed: list[str] = []
    for name in sorted(first_names, key=lambda value: (len(value), value), reverse=True):
        if any(existing.startswith(name) or name.startswith(existing) for existing in collapsed):
            continue
        collapsed.append(name)
    return set(collapsed)


def _name_surface_richness(name: str) -> tuple[int, int, int]:
    stripped = name.strip()
    return (
        sum(1 for ch in stripped if not ch.isascii()),
        stripped.count("-"),
        len(stripped),
    )


def _check_first_name_match(
    raw_first_tokens: list[str],
    candidate_first_tokens: list[str],
    raw_last_name: str,
    candidate_last_name: str,
) -> bool:
    if (
        not raw_first_tokens
        or not candidate_first_tokens
        or raw_last_name != candidate_last_name
        or _has_multiple_initials(raw_first_tokens)
        or (len(raw_first_tokens) == 1 and len(raw_first_tokens[0]) == 1 and _has_multiple_initials(candidate_first_tokens))
    ):
        return False

    raw_first = raw_first_tokens[0]
    candidate_first = candidate_first_tokens[0]
    if raw_first == candidate_first:
        return True
    if len(raw_first) == 1:
        return candidate_first.startswith(raw_first)
    if candidate_first.startswith(raw_first) or raw_first.startswith(candidate_first):
        return True
    return raw_first[0] == candidate_first[0] and fuzz.ratio(raw_first, candidate_first) >= 80


def _is_contextual_player_match(raw_name: str, candidate_name: str) -> bool:
    raw_parts = _player_name_parts(raw_name)
    candidate_parts = _player_name_parts(candidate_name)
    if not raw_parts or not candidate_parts:
        return False

    raw_first_tokens, raw_last_name = raw_parts
    candidate_first_tokens, candidate_last_name = candidate_parts

    # Normal order match
    if _check_first_name_match(raw_first_tokens, candidate_first_tokens, raw_last_name, candidate_last_name):
        return True

    # Reversed order: treat raw as "LastName FirstName" → swap and retry
    if len(raw_first_tokens) == 1:
        reversed_first = [raw_last_name]
        reversed_last = raw_first_tokens[0]
        if _check_first_name_match(reversed_first, candidate_first_tokens, reversed_last, candidate_last_name):
            return True

    return False


def _resolve_contextual_player_names(raw_list: list[RawOddsData]) -> list[RawOddsData]:
    names_by_match: dict[str, Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if not raw.player_name:
            continue

        league_id = normalize_league_id(raw.league_id)
        home_team = normalize_team_name(raw.home_team, league_id)
        away_team = normalize_team_name(raw.away_team, league_id)
        match_id = generate_match_id(home_team, away_team, league_id)
        names_by_match[match_id][raw.player_name.strip()] += 1

    # Pre-pass: merge names that differ only by punctuation, spacing, or diacritics.
    case_replacements: dict[tuple[str, str], str] = {}
    compact_canonical_names: set[tuple[str, str]] = set()
    for match_id, name_counts in names_by_match.items():
        by_compact: dict[str, list[str]] = defaultdict(list)
        for name in name_counts:
            by_compact[_compact_person_name(name)].append(name)
        for compact_key, variants in by_compact.items():
            if not compact_key:
                continue
            if len(variants) <= 1:
                continue
            best = max(
                variants,
                key=lambda v: (
                    name_counts[v],
                    _name_surface_richness(v),
                    v,
                ),
            )
            merged_count = sum(name_counts[v] for v in variants)
            compact_canonical_names.add((match_id, best))
            for v in variants:
                if v != best:
                    case_replacements[(match_id, v)] = best
                    name_counts[best] = merged_count
                    del name_counts[v]

        # Also merge reversed name order (e.g., "Edgecombe VJ" vs "VJ Edgecombe")
        by_sorted: dict[str, list[str]] = defaultdict(list)
        for name in name_counts:
            sorted_key = _sorted_compact_person_name(name)
            if sorted_key:
                by_sorted[sorted_key].append(name)
        for sorted_key, variants in by_sorted.items():
            if len(variants) <= 1:
                continue
            best = max(
                variants,
                key=lambda v: (
                    name_counts[v],
                    _name_surface_richness(v),
                    v,
                ),
            )
            merged_count = sum(name_counts[v] for v in variants)
            compact_canonical_names.add((match_id, best))
            for v in variants:
                if v != best:
                    case_replacements[(match_id, v)] = best
                    name_counts[best] = merged_count
                    del name_counts[v]

    replacements: dict[tuple[str, str], str] = dict(case_replacements)

    for match_id, name_counts in names_by_match.items():
        observed_names = list(name_counts)
        for raw_name in observed_names:
            raw_parts = _player_name_parts(raw_name)
            if not raw_parts:
                continue

            raw_first_tokens, _ = raw_parts
            raw_completeness = _player_name_completeness(raw_first_tokens)
            raw_is_single_initial = len(raw_first_tokens) == 1 and len(raw_first_tokens[0]) == 1
            candidates = [
                candidate
                for candidate in observed_names
                if candidate != raw_name and _is_contextual_player_match(raw_name, candidate)
            ]
            if not candidates:
                continue
            candidate_first_names = _collapse_first_name_variants({
                _player_name_parts(candidate)[0][0] for candidate in candidates if _player_name_parts(candidate)
            })
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
            raw_has_compact_alias_support = (match_id, raw_name) in compact_canonical_names
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

            replacements[(match_id, raw_name)] = best_candidate

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
        seen_replacements: set[str] = set()
        while replacement and replacement not in seen_replacements:
            seen_replacements.add(replacement)
            next_replacement = replacements.get((match_id, replacement))
            if not next_replacement:
                break
            replacement = next_replacement
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
    key = normalize_identity_text(raw_league_id)
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


def _format_matchup(matchup: tuple[str, str]) -> str:
    return f"{matchup[0]} vs {matchup[1]}"


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


def _build_inferred_shared_platform_matchups(
    raw_list: list[RawOddsData],
    matchups_by_slot: dict[tuple[str, str | None], list[tuple[str, str]]],
) -> dict[tuple[str, str | None], tuple[str, str]]:
    teams_by_slot: dict[tuple[str, str | None], set[str]] = defaultdict(set)

    for raw in raw_list:
        if not _is_unresolved_shared_platform_prop(raw):
            continue

        league_id = normalize_league_id(raw.league_id)
        slot = (league_id, raw.start_time)
        if matchups_by_slot.get(slot):
            continue

        normalized_team = normalize_team_name(raw.home_team, league_id)
        if not normalized_team:
            continue
        teams_by_slot[slot].add(normalized_team)

    inferred: dict[tuple[str, str | None], tuple[str, str]] = {}
    for slot, teams in teams_by_slot.items():
        if len(teams) != 2:
            continue
        # Shared-platform rows only tell us which team the player belongs to,
        # not the actual home/away orientation, so use a stable synthetic pair.
        inferred[slot] = tuple(sorted(teams))

    return inferred


def _resolve_shared_platform_matchups(
    raw_list: list[RawOddsData],
) -> tuple[list[RawOddsData], list[UnresolvedOddsDiagnostic]]:
    canonical_matchups = _build_canonical_matchups(raw_list)
    matchups_by_slot: dict[tuple[str, str | None], list[tuple[str, str]]] = {}
    for (league_id, start_time, _matchup_key), matchup in canonical_matchups.items():
        matchups_by_slot.setdefault((league_id, start_time), []).append(matchup)
    for slot, matchup in _build_inferred_shared_platform_matchups(
        raw_list, matchups_by_slot
    ).items():
        matchups_by_slot.setdefault(slot, []).append(matchup)

    resolved: list[RawOddsData] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []

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
            reason_code = (
                "no_canonical_matchup_for_team_at_slot"
                if len(candidates) == 0
                else "ambiguous_multiple_matchups_for_team_at_slot"
            )
            unresolved.append(
                UnresolvedOddsDiagnostic(
                    bookmaker_id=raw.bookmaker_id,
                    raw_league_id=raw.league_id,
                    league_id=league_id,
                    market_type=raw.market_type,
                    player_name=raw.player_name,
                    raw_team_name=raw.home_team,
                    normalized_team_name=known_team,
                    start_time=raw.start_time,
                    threshold=raw.threshold,
                    over_odds=raw.over_odds,
                    under_odds=raw.under_odds,
                    reason_code=reason_code,
                    candidate_count=len(candidates),
                    candidate_matchups=[_format_matchup(matchup) for matchup in candidates[:8]],
                    available_matchups_same_slot=[
                        _format_matchup(matchup)
                        for matchup in matchups_by_slot.get(slot, [])[:12]
                    ],
                )
            )
            logger.warning(
                "Dropping unresolved shared-platform prop for %s (%s, %s, %s)",
                raw.player_name,
                raw.bookmaker_id,
                known_team,
                reason_code,
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

    return resolved, unresolved


def normalize_odds_with_issues(
    raw_list: list[RawOddsData],
) -> tuple[list[NormalizedOdds], list[UnresolvedOddsDiagnostic]]:
    results: list[NormalizedOdds] = []
    resolved_shared_platform, unresolved = _resolve_shared_platform_matchups(raw_list)
    resolved_raw_list = _resolve_contextual_player_names(resolved_shared_platform)
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
    return results, unresolved


def normalize_odds(raw_list: list[RawOddsData]) -> list[NormalizedOdds]:
    return normalize_odds_with_issues(raw_list)[0]
