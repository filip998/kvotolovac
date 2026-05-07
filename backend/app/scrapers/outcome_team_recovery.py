from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re


_MATCHUP_RE = re.compile(
    r"\s*·\s*|\s+vs\.?\s+|\s+v\.?\s+|\s+-\s+|\s+–\s+|\s+—\s+",
    re.IGNORECASE,
)
_HOME_KEYS = (
    "home",
    "homeTeam",
    "home_team",
    "homeName",
    "homeCompetitor",
    "team1",
    "team1Name",
    "host",
)
_AWAY_KEYS = (
    "away",
    "awayTeam",
    "away_team",
    "awayName",
    "awayCompetitor",
    "visitor",
    "team2",
    "team2Name",
    "guest",
)
_MATCHUP_KEYS = ("name", "matchName", "eventName", "title", "label")
_PARTICIPANT_KEYS = ("participants", "competitors", "teams", "p")
_NAME_KEYS = ("name", "n", "teamName", "displayName", "shortName")
_ROLE_KEYS = ("side", "role", "type", "qualifier", "homeAway", "position")
_HOME_ROLES = {"home", "h", "1", "team1", "competitor1"}
_AWAY_ROLES = {"away", "a", "2", "team2", "visitor", "guest", "competitor2"}


@dataclass(frozen=True)
class MatchupRecovery:
    home_team: str
    away_team: str
    source: str | None = None

    @property
    def recovered(self) -> bool:
        return self.source is not None


def split_matchup_text(value: object) -> tuple[str, str] | None:
    text = _coerce_text(value)
    if text is None:
        return None
    parts = _MATCHUP_RE.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    home_team, away_team = (part.strip() for part in parts)
    if not home_team or not away_team:
        return None
    return home_team, away_team


def recover_matchup_from_payload(
    payload: Mapping[str, object],
    *,
    home_keys: Iterable[str] = _HOME_KEYS,
    away_keys: Iterable[str] = _AWAY_KEYS,
    matchup_keys: Iterable[str] = _MATCHUP_KEYS,
    participant_keys: Iterable[str] = _PARTICIPANT_KEYS,
) -> MatchupRecovery:
    home_key_list = tuple(home_keys)
    away_key_list = tuple(away_keys)
    home_team, home_source = _first_named_text(payload, home_key_list)
    away_team, away_source = _first_named_text(payload, away_key_list)

    if home_team and away_team:
        source = _direct_recovery_source(home_source, away_source, home_key_list, away_key_list)
        return MatchupRecovery(home_team, away_team, source)

    for key in participant_keys:
        matchup = _matchup_from_participants(payload.get(key))
        recovered = _reconcile_matchup(home_team, away_team, matchup)
        if recovered is not None:
            return MatchupRecovery(*recovered, source=key)

    for key in matchup_keys:
        matchup = split_matchup_text(payload.get(key))
        recovered = _reconcile_matchup(home_team, away_team, matchup)
        if recovered is not None:
            return MatchupRecovery(*recovered, source=key)

    return MatchupRecovery(home_team, away_team)


def _coerce_text(value: object) -> str | None:
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    if isinstance(value, Mapping):
        for key in _NAME_KEYS:
            text = _coerce_text(value.get(key))
            if text is not None:
                return text
    return None


def _first_named_text(
    payload: Mapping[str, object],
    keys: Sequence[str],
) -> tuple[str, str | None]:
    for key in keys:
        text = _coerce_text(payload.get(key))
        if text is not None:
            return text, key
    return "", None


def _direct_recovery_source(
    home_source: str | None,
    away_source: str | None,
    home_keys: Sequence[str],
    away_keys: Sequence[str],
) -> str | None:
    primary_home = home_keys[0] if home_keys else None
    primary_away = away_keys[0] if away_keys else None
    if home_source == primary_home and away_source == primary_away:
        return None
    sources = [source for source in (home_source, away_source) if source is not None]
    return ",".join(sources) if sources else None


def _matchup_from_participants(value: object) -> tuple[str, str] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None

    named: list[tuple[int, str | None, str]] = []
    for index, participant in enumerate(value):
        if not isinstance(participant, Mapping):
            return None
        name = _coerce_text(participant)
        if name is None:
            return None
        role = _participant_role(participant)
        named.append((index, role, name))

    home = next((name for _, role, name in named if role == "home"), None)
    away = next((name for _, role, name in named if role == "away"), None)
    if home and away:
        return home, away
    if any(role is not None for _, role, _ in named):
        return None

    return named[0][2], named[1][2]


def _participant_role(participant: Mapping[str, object]) -> str | None:
    for key in _ROLE_KEYS:
        raw = participant.get(key)
        if raw is None:
            continue
        value = str(raw).strip().casefold()
        if value in _HOME_ROLES:
            return "home"
        if value in _AWAY_ROLES:
            return "away"
    return None


def _reconcile_matchup(
    home_team: str,
    away_team: str,
    matchup: tuple[str, str] | None,
) -> tuple[str, str] | None:
    if matchup is None:
        return None

    candidate_home, candidate_away = matchup
    if home_team and away_team:
        return home_team, away_team
    if home_team and not away_team and _same_team_text(home_team, candidate_home):
        return home_team, candidate_away
    if away_team and not home_team and _same_team_text(away_team, candidate_away):
        return candidate_home, away_team
    if not home_team and not away_team:
        return candidate_home, candidate_away
    return None


def _same_team_text(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
