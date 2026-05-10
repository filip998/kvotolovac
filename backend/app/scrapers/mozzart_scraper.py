from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .base import BaseScraper
from .http_client import HttpClient
from ..models.schemas import RawOddsData, RawOutcomeOffer
from ..services.text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_SPECIALS_API_URL = "https://www.mozzartbet.com/betting/specialMatches"
_MATCHES_API_URL = "https://www.mozzartbet.com/betting/matches"

_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    ),
    "Cookie": "i18next=sr",
    "medium": "PREMATCH_MOBILE",
}

# The /betting/matches endpoint omits the Hendikep / Pobednik / Dupla šansa
# groups when called with PREMATCH_MOBILE; the website itself uses PREMATCH_WEB
# and gets a strictly larger set of oddsGroup entries (still including
# "Ukupno poena na meču"), so we override the medium for the matches request.
_MATCHES_HEADERS = {**_DEFAULT_HEADERS, "medium": "PREMATCH_WEB"}

# "Broj poena B.Saraf" → ("B.Saraf", "player_points")
# "Broj skokova B.Saraf" → ("B.Saraf", "player_rebounds")
# "Broj asistencija B.Saraf" → ("B.Saraf", "player_assists")
_MARKET_PATTERNS = [
    (re.compile(r"^Broj poena\s+(.+)$", re.IGNORECASE), "player_points"),
    (re.compile(r"^Broj skokova\s+(.+)$", re.IGNORECASE), "player_rebounds"),
    (re.compile(r"^Broj asistencija\s+(.+)$", re.IGNORECASE), "player_assists"),
]

# Group names that contain player markets
_PLAYER_GROUP_KEYWORDS = [
    "poena igrača", "poena igra",
    "skokova igrača", "skokova igra",
    "asistencija igrača", "asistencija igra",
]

_BASKETBALL_SPORT_ID = 2
_FOOTBALL_SPORT_ID = 1
_TENNIS_SPORT_ID = 5
_MATCHES_MATCH_TYPE = 0
_SPECIALS_MATCH_TYPE = 2
_MATCHES_DATE = "all"
_PAGE_SIZE = 50
_TENNIS_PAGE_URL = "https://www.mozzartbet.com/sr/kladjenje/tenis"
_FOOTBALL_TOTAL_GOALS_LINE = 2.5
_GAME_TOTAL_GROUP_NAMES = {
    "ukupno poena na meču",
    "ukupno poena na mecu",
}
_HANDICAP_GROUP_NAMES = {"hendikep"}
_FOOTBALL_RESULT_GROUP_NAMES = {"konacan ishod"}
_FOOTBALL_DOUBLE_CHANCE_GROUP_NAMES = {"dupla sansa"}
_FOOTBALL_TOTAL_GOALS_GROUP_NAMES = {"ukupno golova na mecu"}
_FOOTBALL_RESULT_OUTCOMES = {
    "1": "home",
    "x": "draw",
    "2": "away",
}
_FOOTBALL_DOUBLE_CHANCE_OUTCOMES = {
    "1x": "home_or_draw",
    "12": "home_or_away",
    "x2": "draw_or_away",
}
_FOOTBALL_TOTAL_GOALS_OUTCOMES = {
    "0-2": "under",
    "3": "over",
}
_FOOTBALL_TOTAL_GOALS_LABELS = {
    "under": "0-2",
    "over": "3+",
}
_TENNIS_MATCH_WINNER_GROUP_NAMES = {"konacan ishod"}
_TENNIS_MATCH_WINNER_OUTCOMES = {
    "1": ("home", "1"),
    "2": ("away", "2"),
}
_CANONICAL_LEAGUES = {
    "nba": "nba",
    "usa nba": "nba",
    "euroleague": "euroleague",
    "aba liga": "aba_liga",
    "aba league": "aba_liga",
    "admiralbet aba liga": "aba_liga",
    "admiralbet aba liga plej of": "aba_liga",
}


def _build_specials_request_body(
    competition_ids: list[int] | None = None,
    page_size: int = _PAGE_SIZE,
) -> dict:
    body_inner: dict = {
        "sportIds": [_BASKETBALL_SPORT_ID],
        "pageSize": page_size,
        "currentPage": 0,
        "matchTypeId": _SPECIALS_MATCH_TYPE,
        "orderType": "BY_COMPETITION",
        "loadPriorityTemplateGamesOnly": True,
        "loadAllTemplateGames": False,
        "packGamesGroupBySport": False,
        "medium": "ANDROID",
        "loadExtendedOffer": False,
        "packGroupsInMatch": True,
        "sportsLoad": True,
        "uberOffer": True,
    }
    if competition_ids:
        body_inner["competitionIds"] = competition_ids

    return {
        "currentPage": 0,
        "pageSize": page_size,
        "body": body_inner,
        "uri": "/matches",
    }


def _build_matches_request_body(
    current_page: int = 0,
    competition_ids: list[int] | None = None,
    page_size: int = _PAGE_SIZE,
    sport_id: int = _BASKETBALL_SPORT_ID,
) -> dict:
    return {
        "date": _MATCHES_DATE,
        "sort": "bycompetition",
        "currentPage": current_page,
        "pageSize": page_size,
        "sportId": sport_id,
        "competitionIds": competition_ids or [],
        "search": "",
        "matchTypeId": _MATCHES_MATCH_TYPE,
    }


def _extract_player_and_market(game_name: str) -> tuple[str | None, str]:
    """Extract player name and market type from game name."""
    for pattern, market_type in _MARKET_PATTERNS:
        m = pattern.match(game_name.strip())
        if m:
            return m.group(1).strip(), market_type
    return None, "player_points"


def _parse_start_time(epoch_ms: int | None) -> str | None:
    if not epoch_ms:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).isoformat()


def _normalize_league_key(raw: str | None) -> str:
    if not raw:
        return ""
    return " ".join(raw.strip().lower().replace("_", " ").replace("-", " ").split())


def _extract_league_id(competition_name: str | None) -> str:
    if not competition_name:
        return "basketball"

    raw = competition_name.strip().lower()
    normalized = _normalize_league_key(raw)
    if normalized in _CANONICAL_LEAGUES:
        return _CANONICAL_LEAGUES[normalized]
    return raw.replace(" ", "_") or "basketball"


def _extract_football_league_id(competition_name: str | None) -> str:
    normalized = normalize_identity_text(competition_name)
    return normalized.replace(" ", "_") or "football"


def _extract_tennis_league_id(competition_name: str | None) -> str:
    normalized = normalize_identity_text(competition_name)
    return normalized.replace(" ", "_") or "tennis"


def _parse_threshold(raw_value: object) -> float | None:
    try:
        threshold = float(raw_value)
    except (ValueError, TypeError):
        return None
    return threshold if threshold > 0 else None


def _parse_signed_threshold(raw_value: object) -> float | None:
    """Parse a signed threshold (e.g., handicap line "-4.5" or "+3.5").

    Unlike ``_parse_threshold``, accepts negative values and rejects only
    unparseable input.
    """
    try:
        return float(raw_value)
    except (ValueError, TypeError):
        return None


def _coerce_positive_odds(value: object) -> float | None:
    try:
        odds = float(value)
    except (TypeError, ValueError):
        return None
    return odds if odds > 0 else None


def _assign_over_under(odds: dict[str, float | None], subgame_name: str, value: float) -> None:
    if "više" in subgame_name or "vise" in subgame_name:
        odds["over"] = value
    elif "manje" in subgame_name:
        odds["under"] = value


def _assign_handicap_outcome(
    odds: dict[str, float | None],
    subgame_name: str,
    value: float,
) -> None:
    """Assign Mozzart handicap outcome odds.

    Subgame name "1" pays when the home team (``match.home``) covers, "2" when
    the away team covers. Mapped to home-perspective over/under:
    ``over`` = home covers, ``under`` = away covers.
    """
    sub = subgame_name.strip()
    if sub == "1":
        odds["over"] = value
    elif sub == "2":
        odds["under"] = value


def _parse_items(items: list[dict]) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for match in items:
        home = match.get("home", {}).get("name", "")
        visitor = match.get("visitor", {}).get("name", "")
        competition = match.get("competition", {}).get("name", "")
        start_time = _parse_start_time(match.get("startTime"))
        league_id = _extract_league_id(competition)

        # Aggregate by (player_name, threshold, market_type) to handle any odds ordering
        aggregated: dict[tuple[str, float, str], dict] = {}

        for group in match.get("oddsGroup", []):
            group_name = group.get("groupName", "").strip().lower()
            if not any(kw in group_name for kw in _PLAYER_GROUP_KEYWORDS):
                continue

            for odd in group.get("odds", []):
                if odd.get("oddStatus") != "ACTIVE":
                    continue

                game_name = odd.get("game", {}).get("name", "")
                extracted_name, market_type = _extract_player_and_market(game_name)
                subgame_name = odd.get("subgame", {}).get("name", "").lower()

                sov = _parse_threshold(odd.get("specialOddValue"))
                value = odd.get("value")
                if not extracted_name or sov is None or value is None:
                    continue

                key = (extracted_name, sov, market_type)
                if key not in aggregated:
                    aggregated[key] = {"over": None, "under": None}

                _assign_over_under(aggregated[key], subgame_name, value)

        for (player_name, threshold, market_type), odds in aggregated.items():
            results.append(
                RawOddsData(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    sport="basketball",
                    home_team=home,
                    away_team=visitor,
                    market_type=market_type,
                    player_name=player_name,
                    threshold=threshold,
                    over_odds=odds["over"],
                    under_odds=odds["under"],
                    start_time=start_time,
                )
            )

    return results


def _parse_game_total_items(items: list[dict]) -> list[RawOddsData]:
    results: list[RawOddsData] = []

    for match in items:
        home = match.get("home", {}).get("name", "")
        visitor = match.get("visitor", {}).get("name", "")
        competition = match.get("competition", {}).get("name", "")
        start_time = _parse_start_time(match.get("startTime"))
        league_id = _extract_league_id(competition)

        aggregated: dict[float, dict[str, float | None]] = {}

        for group in match.get("oddsGroup", []):
            group_name = group.get("groupName", "").strip().lower()
            if group_name not in _GAME_TOTAL_GROUP_NAMES:
                continue

            for odd in group.get("odds", []):
                if odd.get("oddStatus") != "ACTIVE":
                    continue

                threshold = _parse_threshold(
                    odd.get("specialOddValue") or group.get("specialOddValue"),
                )
                value = odd.get("value")
                if threshold is None or value is None:
                    continue

                subgame_name = odd.get("subgame", {}).get("name", "").lower()
                aggregated.setdefault(threshold, {"over": None, "under": None})
                _assign_over_under(aggregated[threshold], subgame_name, value)

        for threshold, odds in aggregated.items():
            results.append(
                RawOddsData(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    sport="basketball",
                    home_team=home,
                    away_team=visitor,
                    market_type="game_total",
                    player_name=None,
                    threshold=threshold,
                    over_odds=odds["over"],
                    under_odds=odds["under"],
                    start_time=start_time,
                )
            )

    return results


def _parse_handicap_items(items: list[dict]) -> list[RawOddsData]:
    """Extract Asian handicap rows from Mozzart match listings.

    Mozzart provides a single Hendikep group per match where
    ``odd.specialOddValue`` is a signed line representing team1's handicap
    (negative when home is favored). Subgame "1" pays when home covers, "2"
    when away covers. We canonicalise to a home-perspective expected margin so
    the analyzer can pair with handicap rows from other bookmakers via the
    same threshold/over/under math used for game totals: ``threshold = -line``.
    """
    results: list[RawOddsData] = []

    for match in items:
        home = match.get("home", {}).get("name", "")
        visitor = match.get("visitor", {}).get("name", "")
        competition = match.get("competition", {}).get("name", "")
        start_time = _parse_start_time(match.get("startTime"))
        league_id = _extract_league_id(competition)

        aggregated: dict[float, dict[str, float | None]] = {}

        for group in match.get("oddsGroup", []):
            group_name = group.get("groupName", "").strip().lower()
            if group_name not in _HANDICAP_GROUP_NAMES:
                continue

            for odd in group.get("odds", []):
                if odd.get("oddStatus") != "ACTIVE":
                    continue

                line = _parse_signed_threshold(
                    odd.get("specialOddValue")
                    if odd.get("specialOddValue") is not None
                    else group.get("specialOddValue"),
                )
                value = odd.get("value")
                if line is None or value is None:
                    continue

                threshold = -line
                subgame_name = odd.get("subgame", {}).get("name", "")
                aggregated.setdefault(threshold, {"over": None, "under": None})
                _assign_handicap_outcome(aggregated[threshold], subgame_name, value)

        for threshold, odds in aggregated.items():
            if odds["over"] is None and odds["under"] is None:
                continue
            results.append(
                RawOddsData(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    sport="basketball",
                    home_team=home,
                    away_team=visitor,
                    market_type="home_handicap_ot",
                    player_name=None,
                    threshold=threshold,
                    over_odds=odds["over"],
                    under_odds=odds["under"],
                    start_time=start_time,
                )
            )

    return results


def _football_label_key(raw_label: object) -> str:
    return normalize_identity_text(str(raw_label or ""), keep_hyphens=True).replace(" ", "")


def _parse_football_outcome_match(match: dict) -> list[RawOutcomeOffer]:
    home = ((match.get("home") or {}).get("name") or "").strip()
    visitor = ((match.get("visitor") or {}).get("name") or "").strip()
    if not home or not visitor:
        return []

    competition = (match.get("competition") or {}).get("name")
    league_id = _extract_football_league_id(competition)
    start_time = _parse_start_time(match.get("startTime"))

    results: list[RawOutcomeOffer] = []
    for group in match.get("oddsGroup") or []:
        group_name = normalize_identity_text(group.get("groupName"))
        if group_name in _FOOTBALL_RESULT_GROUP_NAMES:
            market_type = "football_result"
            outcome_lookup = _FOOTBALL_RESULT_OUTCOMES
            line = None
        elif group_name in _FOOTBALL_DOUBLE_CHANCE_GROUP_NAMES:
            market_type = "football_double_chance"
            outcome_lookup = _FOOTBALL_DOUBLE_CHANCE_OUTCOMES
            line = None
        elif group_name in _FOOTBALL_TOTAL_GOALS_GROUP_NAMES:
            market_type = "football_total_goals"
            outcome_lookup = _FOOTBALL_TOTAL_GOALS_OUTCOMES
            line = _FOOTBALL_TOTAL_GOALS_LINE
        else:
            continue

        for odd in group.get("odds") or []:
            if odd.get("oddStatus") != "ACTIVE":
                continue

            raw_label = ((odd.get("subgame") or {}).get("name") or "").strip()
            outcome_code = outcome_lookup.get(_football_label_key(raw_label))
            odds = _coerce_positive_odds(odd.get("value"))
            if outcome_code is None or odds is None:
                continue

            if market_type == "football_total_goals":
                raw_label = _FOOTBALL_TOTAL_GOALS_LABELS[outcome_code]

            results.append(
                RawOutcomeOffer(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    sport="football",
                    home_team=home,
                    away_team=visitor,
                    market_type=market_type,
                    outcome_code=outcome_code,
                    odds=odds,
                    line=line,
                    raw_label=raw_label,
                    start_time=start_time,
                )
            )

    return results


def _parse_football_outcome_items(items: list[dict]) -> list[RawOutcomeOffer]:
    results: list[RawOutcomeOffer] = []
    for match in items:
        results.extend(_parse_football_outcome_match(match))
    return results


_TENNIS_DOUBLES_TOKENS = {"doubles", "double", "dublovi", "parovi"}


def _is_tennis_doubles_match(match: dict) -> bool:
    home = ((match.get("home") or {}).get("name") or "").strip()
    visitor = ((match.get("visitor") or {}).get("name") or "").strip()
    competition = ((match.get("competition") or {}).get("name") or "").strip()

    if "/" in home or "/" in visitor:
        return True
    if "(d)" in competition.lower():
        return True

    normalized_context = normalize_identity_text(f"{home} {visitor} {competition}")
    return bool(set(normalized_context.split()) & _TENNIS_DOUBLES_TOKENS)


def _parse_tennis_outcome_match(match: dict) -> list[RawOutcomeOffer]:
    status = match.get("status") or {}
    if match.get("live") is True or match.get("isLive") is True:
        return []
    if match.get("blocked") is True or status.get("live") is True:
        return []

    home = ((match.get("home") or {}).get("name") or "").strip()
    visitor = ((match.get("visitor") or {}).get("name") or "").strip()
    if not home or not visitor:
        return []
    if _is_tennis_doubles_match(match):
        return []

    competition = (match.get("competition") or {}).get("name")
    league_id = _extract_tennis_league_id(competition)
    start_time = _parse_start_time(match.get("startTime"))
    results: list[RawOutcomeOffer] = []
    seen: set[str] = set()

    for group in match.get("oddsGroup") or []:
        group_name = normalize_identity_text(group.get("groupName"))
        if group_name not in _TENNIS_MATCH_WINNER_GROUP_NAMES:
            continue

        for odd in group.get("odds") or []:
            if odd.get("oddStatus") != "ACTIVE":
                continue

            raw_label = ((odd.get("subgame") or {}).get("name") or "").strip()
            mapping = _TENNIS_MATCH_WINNER_OUTCOMES.get(raw_label)
            odds = _coerce_positive_odds(odd.get("value"))
            if mapping is None or odds is None:
                continue

            outcome_code, canonical_label = mapping
            if outcome_code in seen:
                continue
            seen.add(outcome_code)
            results.append(
                RawOutcomeOffer(
                    bookmaker_id="mozzart",
                    league_id=league_id,
                    sport="tennis",
                    home_team=home,
                    away_team=visitor,
                    source_url=_TENNIS_PAGE_URL,
                    market_type="tennis_match_winner",
                    outcome_code=outcome_code,
                    odds=odds,
                    line=None,
                    raw_label=canonical_label,
                    start_time=start_time,
                )
            )

    return results


def _parse_tennis_outcome_items(items: list[dict]) -> list[RawOutcomeOffer]:
    results: list[RawOutcomeOffer] = []
    for match in items:
        results.extend(_parse_tennis_outcome_match(match))
    return results


class MozzartScraper(BaseScraper):
    """Real scraper for Mozzart player props and game total over/under odds."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http = http_client or HttpClient(default_headers=_DEFAULT_HEADERS)

    def get_bookmaker_id(self) -> str:
        return "mozzart"

    def get_bookmaker_name(self) -> str:
        return "Mozzart"

    def get_supported_leagues(self) -> list[str]:
        return ["basketball"]

    async def _fetch_special_items(self) -> list[dict]:
        body = _build_specials_request_body()

        try:
            data = await self._http.post_json(
                _SPECIALS_API_URL,
                json_body=body,
                headers=_DEFAULT_HEADERS,
            )
        except Exception:
            logger.exception("Mozzart specials scrape failed")
            return []

        return data.get("items", [])

    async def _fetch_match_items(
        self,
        *,
        sport_id: int = _BASKETBALL_SPORT_ID,
        label: str = "match",
    ) -> list[dict]:
        items: list[dict] = []
        current_page = 0

        while True:
            body = _build_matches_request_body(
                current_page=current_page,
                sport_id=sport_id,
            )
            try:
                data = await self._http.post_json(
                    _MATCHES_API_URL,
                    json_body=body,
                    headers=_MATCHES_HEADERS,
                )
            except Exception:
                logger.exception("Mozzart %s scrape failed on page %d", label, current_page)
                return items

            page_items = data.get("items", [])
            if not page_items:
                return items

            items.extend(page_items)
            if len(page_items) < _PAGE_SIZE:
                return items

            current_page += 1

    def get_supported_outcome_sports(self) -> list[str]:
        return ["football", "tennis"]

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if league_id != "basketball":
            return []

        special_items = await self._fetch_special_items()
        match_items = await self._fetch_match_items()

        if not special_items and not match_items:
            logger.warning("Mozzart returned 0 items across specials and prematch matches")
            return []

        player_results = _parse_items(special_items)
        game_total_results = _parse_game_total_items(match_items)
        handicap_results = _parse_handicap_items(match_items)
        results = player_results + game_total_results + handicap_results
        logger.info(
            (
                "Mozzart scraped %d odds (%d player props, %d game totals, %d handicaps) "
                "from %d specials and %d prematch matches"
            ),
            len(results),
            len(player_results),
            len(game_total_results),
            len(handicap_results),
            len(special_items),
            len(match_items),
        )
        return results

    async def scrape_outcome_offers(self, sport: str) -> list[RawOutcomeOffer]:
        if sport == "tennis":
            match_items = await self._fetch_match_items(
                sport_id=_TENNIS_SPORT_ID,
                label="tennis match",
            )
            if not match_items:
                logger.warning("Mozzart returned 0 tennis prematch matches")
                return []

            results = _parse_tennis_outcome_items(match_items)
            logger.info(
                "Mozzart scraped %d tennis outcome offers from %d prematch matches",
                len(results),
                len(match_items),
            )
            return results

        if sport != "football":
            return []

        match_items = await self._fetch_match_items(
            sport_id=_FOOTBALL_SPORT_ID,
            label="football match",
        )
        if not match_items:
            logger.warning("Mozzart returned 0 football prematch matches")
            return []

        results = _parse_football_outcome_items(match_items)
        logger.info(
            "Mozzart scraped %d football outcome offers from %d prematch matches",
            len(results),
            len(match_items),
        )
        return results
