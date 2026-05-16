from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import logging
import re
import time

from rapidfuzz import fuzz

from ..models.schemas import (
    NormalizedOutcomeOffer,
    OutcomeNormalizationBenchmarkOut,
    OutcomeNormalizationBookmakerBenchmarkOut,
    OutcomeNormalizationRunBenchmarkOut,
    OutcomeFootballEventBucketBenchmarkOut,
    RawOddsData,
    RawOutcomeOffer,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from .league_registry import resolve_league
from .normalizer import (
    TEAM_REVIEW_CANDIDATE_THRESHOLD,
    TeamReviewDiagnosticsMetrics,
    build_team_review_cases_for_diagnostics,
    generate_match_id,
    resolve_team_name,
)
from .team_registry import create_canonical_team, create_canonical_teams_batch
from .tennis_name_matcher import (
    TENNIS_BROAD_DRIFT_MINUTES,
    match_tennis_player_names,
    tennis_competitor_pair_matches,
)
from .text_normalizer import normalize_identity_text

logger = logging.getLogger(__name__)

_FOOTBALL_AUTO_MATCH_AVG_THRESHOLD = 78
_FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD = 70
_FOOTBALL_AUTO_MATCH_STRONG_SIDE_THRESHOLD = 95
_FOOTBALL_AUTO_MATCH_WEAK_SIDE_THRESHOLD = 60
_FOOTBALL_AUTO_MATCH_MARGIN = 8
_LOW_SIGNAL_TEAM_TOKENS = {"bc", "bk", "kk", "fc", "fk", "club", "team", "sc", "cf", "cd", "ce"}
# Foreign-language women markers seen in real-world team names. Tokens are
# matched after `normalize_identity_text` (NFKD strip + lower + alnum-only),
# so include the post-normalization form (e.g. "feminin" covers French
# "Féminin" because diacritics are stripped). Conservative additions only —
# tokens that could plausibly appear as part of a regular team name (e.g.
# bare "fem") are intentionally omitted.
_FOREIGN_WOMEN_TOKENS = frozenset({
    "frauen",      # German
    "damen",       # German (formal)
    "feminino",    # Portuguese (m.)
    "feminina",    # Portuguese (f.)
    "femminile",   # Italian
    "femenino",    # Spanish (m.)
    "femenina",    # Spanish (f.)
    "feminin",     # French/Romanian (post-diacritic strip)
    "feminines",   # French plural
    "kvinnor",     # Swedish
    "naiset",      # Finnish
    "vrouwen",     # Dutch
    "kvinder",     # Danish
    "dff",         # Swedish "Damfotboll Förening" club designation
})
_TEAM_QUALIFIER_TOKENS = {
    "2",
    "ii",
    "b",
    "res",
    "reserve",
    "reserves",
    "u17",
    "u18",
    "u19",
    "u20",
    "u21",
    "u23",
    "w",
    "women",
    "youth",
} | _FOREIGN_WOMEN_TOKENS
# Cross-sport aliases for explicit women markers. Plain ASCII "z" is not in
# this set because it is a common location abbreviation in football; only
# explicit marker syntax such as "(Ž)" or "Ž/" is treated as women.
_WOMEN_QUALIFIER_ALIASES = frozenset({"w", "wom", "women"}) | _FOREIGN_WOMEN_TOKENS
_WOMEN_MARKER_TOKENS = frozenset({"w", "wom", "women"}) | _FOREIGN_WOMEN_TOKENS
_AGGRESSIVE_MERGE_SPORTS = frozenset({"basketball"})
_EXPLICIT_Z_WOMEN_MARKER_RE = re.compile(
    r"(^|\s)ž(?=$|\s)|\(\s*[žz]\s*\)|^\s*[žz]\s*/",
    re.IGNORECASE,
)
_SAME_ORIENTATION = "same"
_REVERSED_ORIENTATION = "reversed"
_OUTCOME_EVENT_RESOLUTION_SPORTS = frozenset({"football", "tennis"})
_TENNIS_MATCH_WINNER_MARKETS = frozenset({"tennis_match_winner", "match_winner"})


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _parse_event_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _event_time_delta_minutes(left: str, right: str) -> float | None:
    if left == right:
        return 0.0
    left_dt = _parse_event_time(left)
    right_dt = _parse_event_time(right)
    if left_dt is None or right_dt is None:
        return None
    try:
        return abs((left_dt - right_dt).total_seconds()) / 60
    except TypeError:
        return None


@dataclass(frozen=True)
class _OutcomeEvent:
    bookmaker_id: str
    sport: str
    start_time: str
    home_team: str
    away_team: str


@dataclass(frozen=True)
class _OutcomeEventSlot:
    sport: str
    start_time: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.sport, self.start_time, self.home_team_id, self.away_team_id)

    @property
    def reversed_key(self) -> tuple[str, str, int, int]:
        return (self.sport, self.start_time, self.away_team_id, self.home_team_id)


@dataclass(frozen=True)
class _OutcomeEventResolution:
    slot: _OutcomeEventSlot
    orientation: str = _SAME_ORIENTATION


FootballEventResolutionKey = tuple[str, str, str, str, str]
FootballEventResolutionMap = dict[FootballEventResolutionKey, _OutcomeEventResolution]


@dataclass(frozen=True)
class OutcomeNormalizationResult:
    normalized: list[NormalizedOutcomeOffer]
    unresolved: list[UnresolvedOddsDiagnostic]
    team_review_cases: list[TeamReviewDiagnostic]
    benchmark: OutcomeNormalizationBenchmarkOut
    football_event_resolutions: FootballEventResolutionMap


@dataclass(frozen=True)
class _OutcomeEventPair:
    left: _OutcomeEvent
    right: _OutcomeEvent
    home_score: float
    away_score: float
    orientation: str
    time_delta_minutes: float = 0.0
    broad_time_safe: bool = False

    @property
    def score(self) -> float:
        return (self.home_score + self.away_score) / 2

    @property
    def strong_side_score(self) -> float:
        return max(self.home_score, self.away_score)

    @property
    def weak_side_score(self) -> float:
        return min(self.home_score, self.away_score)


@dataclass
class _FootballEventResolutionStats:
    football_unique_event_count: int = 0
    football_event_pair_candidate_count: int = 0
    football_event_fuzzy_score_count: int = 0
    football_event_canonical_conflict_skip_count: int = 0
    football_event_canonical_conflict_fuzzy_score_avoided_count: int = 0
    football_event_pair_ranking_ms: int = 0
    football_event_slot_lookup_ms: int = 0
    football_event_slot_mutation_ms: int = 0
    football_event_time_slot_count: int = 0
    football_event_max_events_per_slot: int = 0
    top_football_event_buckets: list[OutcomeFootballEventBucketBenchmarkOut] = field(
        default_factory=list
    )


class _OutcomeTextCache:
    def __init__(self, stats: _FootballEventResolutionStats | None = None) -> None:
        self._stats = stats
        self._qualifiers: dict[tuple[str, str | None], set[str]] = {}
        self._comparison_text: dict[tuple[str, str | None], str] = {}
        self._significant_tokens: dict[tuple[str, str | None], set[str]] = {}
        self._women_marker_forms: dict[str, frozenset[str]] = {}

    def qualifiers(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._qualifiers.get(key)
        if cached is None:
            cached = _team_qualifiers(name, sport=sport)
            self._qualifiers[key] = cached
        return cached

    def comparison_text(self, name: str, *, sport: str | None = None) -> str:
        key = (name, sport)
        cached = self._comparison_text.get(key)
        if cached is None:
            qualifiers = self.qualifiers(name, sport=sport)
            comparison_name = (
                _strip_explicit_z_women_markers(name)
                if "women" in qualifiers
                else name
            )
            tokens = normalize_identity_text(comparison_name).split()
            if "women" in qualifiers:
                tokens = [
                    token for token in tokens if token not in _WOMEN_MARKER_TOKENS
                ]
            cached = " ".join(tokens)
            self._comparison_text[key] = cached
        return cached

    def significant_tokens(self, name: str, *, sport: str | None = None) -> set[str]:
        key = (name, sport)
        cached = self._significant_tokens.get(key)
        if cached is None:
            cached = {
                token
                for token in self.comparison_text(name, sport=sport).split()
                if token not in _LOW_SIGNAL_TEAM_TOKENS
            }
            self._significant_tokens[key] = cached
        return cached

    def same_context(
        self,
        left: str,
        right: str,
        *,
        sport: str | None = None,
    ) -> bool:
        return self.qualifiers(left, sport=sport) == self.qualifiers(
            right, sport=sport
        )

    def team_similarity(
        self,
        left: str,
        right: str,
        *,
        sport: str | None = None,
    ) -> float:
        left_key = self.comparison_text(left, sport=sport)
        right_key = self.comparison_text(right, sport=sport)
        if not left_key or not right_key:
            return 0.0
        if left_key == right_key:
            return 100.0
        left_tokens = self.significant_tokens(left, sport=sport)
        right_tokens = self.significant_tokens(right, sport=sport)
        if left_tokens and left_tokens == right_tokens:
            return 100.0
        if self._stats is not None:
            self._stats.football_event_fuzzy_score_count += 1
        return float(fuzz.token_sort_ratio(left_key, right_key))

    def comparison_texts_are_compatible(
        self,
        left_name: str,
        right_name: str,
        *,
        sport: str | None = None,
        team_ids: tuple[int, int] | None = None,
    ) -> bool:
        if team_ids is not None and team_ids[0] == team_ids[1]:
            return True
        left_text = self.comparison_text(left_name, sport=sport)
        right_text = self.comparison_text(right_name, sport=sport)
        if left_text == right_text:
            return True
        left_tokens = self.significant_tokens(left_name, sport=sport)
        right_tokens = self.significant_tokens(right_name, sport=sport)
        if not left_tokens or not right_tokens:
            return False
        return left_tokens <= right_tokens or right_tokens <= left_tokens

    def women_marker_forms(self, name: str) -> frozenset[str]:
        cached = self._women_marker_forms.get(name)
        if cached is None:
            cached = _women_marker_forms(name, text_cache=self)
            self._women_marker_forms[name] = cached
        return cached


def _significant_tokens(name: str, *, sport: str | None = None) -> set[str]:
    return {
        token
        for token in _comparison_team_text(name, sport=sport).split()
        if token not in _LOW_SIGNAL_TEAM_TOKENS
    }


def _team_similarity(left: str, right: str, *, sport: str | None = None) -> float:
    left_key = _comparison_team_text(left, sport=sport)
    right_key = _comparison_team_text(right, sport=sport)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 100.0
    left_tokens = _significant_tokens(left, sport=sport)
    right_tokens = _significant_tokens(right, sport=sport)
    if left_tokens and left_tokens == right_tokens:
        return 100.0
    return float(fuzz.token_sort_ratio(left_key, right_key))


_TENNIS_SURNAME_PARTICLES = frozenset(
    {"da", "de", "del", "della", "di", "du", "la", "le", "van", "von"}
)


def _is_initial_token(token: str) -> bool:
    return len(token) == 1 and token.isalpha()


def _tokens_suffix_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if left == right:
        return True
    if len(left) > len(right):
        return left[-len(right) :] == right
    if len(right) > len(left):
        return right[-len(left) :] == left
    return False


def _tennis_player_name_parts(
    name: str,
    *,
    text_cache: _OutcomeTextCache,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
    tokens = tuple(text_cache.comparison_text(name, sport="tennis").split())
    if not tokens:
        return None

    suffix_initial_count = 0
    for token in reversed(tokens):
        if not _is_initial_token(token):
            break
        suffix_initial_count += 1
    if suffix_initial_count and suffix_initial_count < len(tokens):
        return (
            tokens[: len(tokens) - suffix_initial_count],
            (),
            tokens[len(tokens) - suffix_initial_count :],
        )

    prefix_initial_count = 0
    for token in tokens:
        if not _is_initial_token(token):
            break
        prefix_initial_count += 1
    if prefix_initial_count and prefix_initial_count < len(tokens):
        return (
            tokens[prefix_initial_count:],
            (),
            tokens[:prefix_initial_count],
        )

    given_tokens = list(tokens[:-1])
    family_tokens = [tokens[-1]]
    while given_tokens and given_tokens[-1] in _TENNIS_SURNAME_PARTICLES:
        family_tokens.insert(0, given_tokens.pop())
    return (tuple(family_tokens), tuple(given_tokens), ())


def _tennis_initials_for(given_tokens: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(token[0] for token in given_tokens if token)


def _tennis_name_match_score(
    left_name: str,
    right_name: str,
    *,
    text_cache: _OutcomeTextCache,
) -> float | None:
    del text_cache
    match = match_tennis_player_names(left_name, right_name)
    return match.score if match is not None else None


def _team_qualifiers(name: str, *, sport: str | None = None) -> set[str]:
    tokens = normalize_identity_text(name).split()
    qualifiers: set[str] = set()
    youth_ages = {"17", "18", "19", "20", "21", "23"}
    active_qualifier_tokens = _TEAM_QUALIFIER_TOKENS | {"wom"}

    if _EXPLICIT_Z_WOMEN_MARKER_RE.search(name):
        qualifiers.add("women")

    def suffix_has_qualifier(start_index: int) -> bool:
        index = start_index
        while index < len(tokens):
            token = tokens[index]
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if token == "team":
                index += 1
                continue
            if token == "u" and next_token in youth_ages:
                return True
            if token in active_qualifier_tokens:
                return True
            index += 1
        return False

    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == "u" and next_token in youth_ages:
            qualifiers.add(f"u{next_token}")
            continue
        if token in {"b", "2", "ii"}:
            if index > 0 and (index == len(tokens) - 1 or next_token == "team" or suffix_has_qualifier(index + 1)):
                qualifiers.add(token)
            continue
        if token in _WOMEN_QUALIFIER_ALIASES:
            is_explicit_prefix = (
                token in ({"women", "wom"} | _FOREIGN_WOMEN_TOKENS)
                and index == 0
                and len(tokens) > 1
            )
            is_suffix = index > 0 and (
                index == len(tokens) - 1
                or next_token in {"team", "women"}
                or suffix_has_qualifier(index + 1)
            )
            if is_explicit_prefix or is_suffix:
                qualifiers.add("women")
            continue
        if token == "z":
            # Plain ASCII Z is intentionally not a universal women alias.
            # The explicit-marker regex above handles "(Ž)", "(Z)", "Ž/",
            # and "Z/" without breaking football abbreviations such as
            # "FK Borac Z" for Zvornik.
            continue
        if token not in active_qualifier_tokens:
            continue
        qualifiers.add(token)
    return qualifiers


def _strip_explicit_z_women_markers(name: str) -> str:
    without_parenthesized = re.sub(
        r"\(\s*[žz]\s*\)",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    without_leading_slash = re.sub(
        r"^\s*[žz]\s*/",
        "",
        without_parenthesized,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"(^|\s)ž(?=$|\s)",
        r"\1",
        without_leading_slash,
        flags=re.IGNORECASE,
    )


def _comparison_team_text(team_name: str, *, sport: str | None = None) -> str:
    qualifiers = _team_qualifiers(team_name, sport=sport)
    comparison_name = (
        _strip_explicit_z_women_markers(team_name)
        if "women" in qualifiers
        else team_name
    )
    tokens = normalize_identity_text(comparison_name).split()
    if "women" in qualifiers:
        tokens = [token for token in tokens if token not in _WOMEN_MARKER_TOKENS]
    return " ".join(tokens)


def _same_team_context(left: str, right: str, *, sport: str | None = None) -> bool:
    return _team_qualifiers(left, sport=sport) == _team_qualifiers(right, sport=sport)


def _display_name_for_event(*names: str) -> str:
    return max(
        (name.strip() for name in names if name.strip()),
        key=lambda value: (len(_significant_tokens(value)), len(value), value),
    )


def _autocreate_cross_book_football_teams(raw_list: list[RawOutcomeOffer]) -> int:
    matchup_counts: dict[tuple[str, str, tuple[str, str]], set[str]] = defaultdict(set)
    display_names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if raw.start_time is None:
            continue
        home_key = normalize_identity_text(raw.home_team)
        away_key = normalize_identity_text(raw.away_team)
        if not home_key or not away_key or home_key == away_key:
            continue
        pair_key = (raw.sport, raw.start_time, tuple(sorted((home_key, away_key))))
        matchup_counts[pair_key].add(raw.bookmaker_id)
        display_names[(raw.sport, home_key)][raw.home_team.strip()] += 1
        display_names[(raw.sport, away_key)][raw.away_team.strip()] += 1

    missing_display_names: dict[tuple[str, str], str] = {}
    for (sport, _start_time, team_keys), bookmaker_ids in matchup_counts.items():
        if len(bookmaker_ids) < 2:
            continue
        for team_key in team_keys:
            counter = display_names.get((sport, team_key))
            if not counter:
                continue
            display_name = max(counter.items(), key=lambda item: (item[1], len(item[0]), item[0]))[0]
            if resolve_team_name(display_name, sport=sport).team_id is None:
                missing_display_names[(sport, team_key)] = display_name

    created_count = 0
    by_sport: dict[str, list[str]] = defaultdict(list)
    for (sport, _team_key), display_name in missing_display_names.items():
        by_sport[sport].append(display_name)
    for sport, sport_display_names in by_sport.items():
        resolutions = create_canonical_teams_batch(
            display_names=sport_display_names,
            sport=sport,
        )
        created_count += sum(1 for resolution in resolutions if resolution.source == "batch_create")
    return created_count


def _unique_events(raw_list: list[RawOutcomeOffer]) -> list[_OutcomeEvent]:
    seen: set[tuple[str, str, str, str, str]] = set()
    events: list[_OutcomeEvent] = []
    for raw in raw_list:
        if raw.sport not in _OUTCOME_EVENT_RESOLUTION_SPORTS or raw.start_time is None:
            continue
        if raw.sport == "tennis" and raw.market_type not in _TENNIS_MATCH_WINNER_MARKETS:
            continue
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
        )
        if key in seen:
            continue
        seen.add(key)
        events.append(
            _OutcomeEvent(
                bookmaker_id=raw.bookmaker_id,
                sport=raw.sport,
                start_time=raw.start_time,
                home_team=raw.home_team,
                away_team=raw.away_team,
            )
        )
    return events


def _pair_candidates(
    left: _OutcomeEvent,
    right: _OutcomeEvent,
    *,
    text_cache: _OutcomeTextCache | None = None,
    stats: _FootballEventResolutionStats | None = None,
) -> _OutcomeEventPair | None:
    if left.bookmaker_id == right.bookmaker_id or left.sport != right.sport:
        return None
    time_delta = _event_time_delta_minutes(left.start_time, right.start_time)
    if time_delta is None:
        return None
    if left.sport == "tennis":
        if time_delta > TENNIS_BROAD_DRIFT_MINUTES:
            return None
    elif left.start_time != right.start_time:
        return None
    if stats is not None:
        stats.football_event_pair_candidate_count += 1
    text_cache = text_cache or _OutcomeTextCache(stats)
    candidates: list[_OutcomeEventPair] = []
    if left.sport == "tennis":
        for tennis_match in tennis_competitor_pair_matches(
            left.home_team,
            left.away_team,
            right.home_team,
            right.away_team,
        ):
            if time_delta > tennis_match.max_time_delta_minutes:
                continue
            candidates.append(
                _OutcomeEventPair(
                    left=left,
                    right=right,
                    home_score=tennis_match.home_score,
                    away_score=tennis_match.away_score,
                    orientation=(
                        _SAME_ORIENTATION
                        if tennis_match.orientation == "as_listed"
                        else _REVERSED_ORIENTATION
                    ),
                    time_delta_minutes=time_delta,
                    broad_time_safe=tennis_match.broad_time_safe,
                )
            )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.orientation == _SAME_ORIENTATION,
            ),
        )

    if text_cache.same_context(left.home_team, right.home_team, sport=left.sport) and text_cache.same_context(left.away_team, right.away_team, sport=left.sport):
        candidates.append(
            _OutcomeEventPair(
                left=left,
                right=right,
                home_score=text_cache.team_similarity(
                    left.home_team, right.home_team, sport=left.sport
                ),
                away_score=text_cache.team_similarity(
                    left.away_team, right.away_team, sport=left.sport
                ),
                orientation=_SAME_ORIENTATION,
            )
        )
    if text_cache.same_context(left.home_team, right.away_team, sport=left.sport) and text_cache.same_context(left.away_team, right.home_team, sport=left.sport):
        candidates.append(
            _OutcomeEventPair(
                left=left,
                right=right,
                home_score=text_cache.team_similarity(
                    left.home_team, right.away_team, sport=left.sport
                ),
                away_score=text_cache.team_similarity(
                    left.away_team, right.home_team, sport=left.sport
                ),
                orientation=_REVERSED_ORIENTATION,
            )
        )
    if not candidates:
        return None
    pair = max(candidates, key=lambda candidate: (candidate.score, candidate.orientation == _SAME_ORIENTATION))
    if not _is_auto_event_match_candidate(pair):
        return None
    return pair


def _slot_team_ids(
    resolution: _OutcomeEventResolution,
) -> frozenset[int]:
    return frozenset((resolution.slot.home_team_id, resolution.slot.away_team_id))


def _has_significant_token_overlap(
    left_name: str,
    right_name: str,
    *,
    sport: str,
    text_cache: _OutcomeTextCache,
) -> bool:
    left_tokens = text_cache.significant_tokens(left_name, sport=sport)
    right_tokens = text_cache.significant_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return True
    return bool(left_tokens & right_tokens)


def _event_pair_has_plausible_text_overlap(
    left: _OutcomeEvent,
    right: _OutcomeEvent,
    *,
    text_cache: _OutcomeTextCache,
) -> bool:
    return any(
        _has_significant_token_overlap(
            left_name,
            right_name,
            sport=left.sport,
            text_cache=text_cache,
        )
        for left_name, right_name in (
            (left.home_team, right.home_team),
            (left.away_team, right.away_team),
            (left.home_team, right.away_team),
            (left.away_team, right.home_team),
        )
    )


def _should_skip_disjoint_canonical_slots(
    left: _OutcomeEvent,
    right: _OutcomeEvent,
    *,
    left_resolution: _OutcomeEventResolution | None,
    right_resolution: _OutcomeEventResolution | None,
    slot_bookmaker_support: dict[frozenset[int], set[str]],
    text_cache: _OutcomeTextCache,
) -> bool:
    if left_resolution is None or right_resolution is None:
        return False
    left_ids = _slot_team_ids(left_resolution)
    right_ids = _slot_team_ids(right_resolution)
    if not left_ids.isdisjoint(right_ids):
        return False
    if (
        len(slot_bookmaker_support.get(left_ids, set())) < 2
        or len(slot_bookmaker_support.get(right_ids, set())) < 2
    ):
        return False
    return not _event_pair_has_plausible_text_overlap(
        left,
        right,
        text_cache=text_cache,
    )


def _is_auto_event_match_candidate(pair: _OutcomeEventPair) -> bool:
    if pair.score < _FOOTBALL_AUTO_MATCH_AVG_THRESHOLD:
        return False
    balanced_match = pair.weak_side_score >= _FOOTBALL_AUTO_MATCH_SIDE_THRESHOLD
    anchored_match = (
        pair.strong_side_score >= _FOOTBALL_AUTO_MATCH_STRONG_SIDE_THRESHOLD
        and pair.weak_side_score >= _FOOTBALL_AUTO_MATCH_WEAK_SIDE_THRESHOLD
    )
    return balanced_match or anchored_match


def _event_key(event: _OutcomeEvent) -> tuple[str, str, str, str, str]:
    return (
        event.bookmaker_id,
        event.sport,
        event.start_time,
        normalize_identity_text(event.home_team),
        normalize_identity_text(event.away_team),
    )


def _event_key_from_raw(raw: RawOutcomeOffer) -> tuple[str, str, str, str, str] | None:
    if raw.start_time is None:
        return None
    return (
        raw.bookmaker_id,
        raw.sport,
        raw.start_time,
        normalize_identity_text(raw.home_team),
        normalize_identity_text(raw.away_team),
    )


def _invert_orientation(orientation: str) -> str:
    return _REVERSED_ORIENTATION if orientation == _SAME_ORIENTATION else _SAME_ORIENTATION


def _orientation_from_pair(base_orientation: str, pair_orientation: str) -> str:
    if pair_orientation == _SAME_ORIENTATION:
        return base_orientation
    return _invert_orientation(base_orientation)


def _resolve_event_slot(event: _OutcomeEvent) -> _OutcomeEventSlot | None:
    home_resolution = resolve_team_name(
        event.home_team,
        bookmaker_id=event.bookmaker_id,
        sport=event.sport,
    )
    away_resolution = resolve_team_name(
        event.away_team,
        bookmaker_id=event.bookmaker_id,
        sport=event.sport,
    )
    if (
        home_resolution.team_id is None
        or away_resolution.team_id is None
        or home_resolution.team_id == away_resolution.team_id
    ):
        return None
    return _OutcomeEventSlot(
        sport=event.sport,
        start_time=event.start_time,
        home_team_id=home_resolution.team_id,
        away_team_id=away_resolution.team_id,
        home_team=home_resolution.team_name,
        away_team=away_resolution.team_name,
    )


def _create_event_slot(pair: _OutcomeEventPair) -> _OutcomeEventSlot | None:
    if pair.orientation == _SAME_ORIENTATION:
        home_name = _display_name_for_event(pair.left.home_team, pair.right.home_team)
        away_name = _display_name_for_event(pair.left.away_team, pair.right.away_team)
    else:
        home_name = _display_name_for_event(pair.left.home_team, pair.right.away_team)
        away_name = _display_name_for_event(pair.left.away_team, pair.right.home_team)

    home = create_canonical_team(display_name=home_name, sport=pair.left.sport)
    away = create_canonical_team(display_name=away_name, sport=pair.left.sport)
    if home.team_id == away.team_id:
        return None
    return _OutcomeEventSlot(
        sport=pair.left.sport,
        start_time=pair.left.start_time,
        home_team_id=home.team_id,
        away_team_id=away.team_id,
        home_team=home.team_name,
        away_team=away.team_name,
    )


def _same_slot(left: _OutcomeEventResolution, right: _OutcomeEventResolution) -> bool:
    return left.slot.key == right.slot.key


def _reversed_slots(left: _OutcomeEventResolution, right: _OutcomeEventResolution) -> bool:
    return left.slot.key == right.slot.reversed_key


def _move_resolution_component(
    resolutions: dict[tuple[str, str, str, str, str], _OutcomeEventResolution],
    *,
    source_resolution: _OutcomeEventResolution,
    target_slot: _OutcomeEventSlot,
    source_target_orientation: str,
) -> None:
    for event_key, resolution in list(resolutions.items()):
        if resolution.slot.key != source_resolution.slot.key:
            continue
        relative_orientation = (
            _SAME_ORIENTATION
            if resolution.orientation == source_resolution.orientation
            else _REVERSED_ORIENTATION
        )
        resolutions[event_key] = _OutcomeEventResolution(
            slot=target_slot,
            orientation=_orientation_from_pair(
                source_target_orientation,
                relative_orientation,
            ),
        )


def _oriented_pair_team_names(
    pair: _OutcomeEventPair,
) -> tuple[tuple[str, str], tuple[str, str]]:
    if pair.orientation == _SAME_ORIENTATION:
        return (
            (pair.left.home_team, pair.right.home_team),
            (pair.left.away_team, pair.right.away_team),
        )
    return (
        (pair.left.home_team, pair.right.away_team),
        (pair.left.away_team, pair.right.home_team),
    )


def _oriented_pair_team_ids(
    pair: _OutcomeEventPair,
    left_resolution: _OutcomeEventResolution,
    right_resolution: _OutcomeEventResolution,
) -> tuple[tuple[int, int], tuple[int, int]]:
    if pair.orientation == _SAME_ORIENTATION:
        return (
            (left_resolution.slot.home_team_id, right_resolution.slot.home_team_id),
            (left_resolution.slot.away_team_id, right_resolution.slot.away_team_id),
        )
    return (
        (left_resolution.slot.home_team_id, right_resolution.slot.away_team_id),
        (left_resolution.slot.away_team_id, right_resolution.slot.home_team_id),
    )


def _oriented_pair_team_ids_from_resolutions(
    pair: _OutcomeEventPair,
    resolutions: dict[tuple[str, str, str, str, str], _OutcomeEventResolution],
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    left_resolution = resolutions.get(_event_key(pair.left))
    right_resolution = resolutions.get(_event_key(pair.right))
    if left_resolution is None or right_resolution is None:
        return None
    return _oriented_pair_team_ids(pair, left_resolution, right_resolution)


def _comparison_team_texts_are_compatible(
    left_name: str,
    right_name: str,
    *,
    sport: str | None = None,
    team_ids: tuple[int, int] | None = None,
    text_cache: _OutcomeTextCache | None = None,
) -> bool:
    if text_cache is not None:
        return text_cache.comparison_texts_are_compatible(
            left_name,
            right_name,
            sport=sport,
            team_ids=team_ids,
        )
    if team_ids is not None and team_ids[0] == team_ids[1]:
        return True
    left_text = _comparison_team_text(left_name, sport=sport)
    right_text = _comparison_team_text(right_name, sport=sport)
    if left_text == right_text:
        return True
    left_tokens = _significant_tokens(left_name, sport=sport)
    right_tokens = _significant_tokens(right_name, sport=sport)
    if not left_tokens or not right_tokens:
        return False
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _pair_has_compatible_women_context(
    pair: _OutcomeEventPair,
    *,
    oriented_team_ids: tuple[tuple[int, int], tuple[int, int]] | None = None,
    text_cache: _OutcomeTextCache | None = None,
) -> bool:
    text_cache = text_cache or _OutcomeTextCache()
    has_women_pair = False
    for index, (left_name, right_name) in enumerate(_oriented_pair_team_names(pair)):
        left_qualifiers = text_cache.qualifiers(left_name, sport=pair.left.sport)
        right_qualifiers = text_cache.qualifiers(right_name, sport=pair.left.sport)
        if left_qualifiers != right_qualifiers:
            return False
        if "women" in left_qualifiers:
            has_women_pair = True
        if not _comparison_team_texts_are_compatible(
            left_name,
            right_name,
            sport=pair.left.sport,
            team_ids=oriented_team_ids[index] if oriented_team_ids is not None else None,
            text_cache=text_cache,
        ):
            return False
    return has_women_pair


def _women_marker_forms(
    name: str,
    *,
    text_cache: _OutcomeTextCache | None = None,
) -> frozenset[str]:
    forms: set[str] = set()
    if re.search(r"\(\s*[žz]\s*\)", name, flags=re.IGNORECASE):
        forms.add("z:parenthesized")
    if re.search(r"^\s*[žz]\s*/", name, flags=re.IGNORECASE):
        forms.add("z:slash-prefix")
    if re.search(r"(^|\s)ž(?=$|\s)", name, flags=re.IGNORECASE):
        forms.add("z:standalone")

    tokens = normalize_identity_text(name).split()
    youth_ages = {"17", "18", "19", "20", "21", "23"}
    active_qualifier_tokens = _TEAM_QUALIFIER_TOKENS | {"wom"}

    def suffix_has_qualifier(start_index: int) -> bool:
        index = start_index
        while index < len(tokens):
            token = tokens[index]
            next_token = tokens[index + 1] if index + 1 < len(tokens) else None
            if token == "team":
                index += 1
                continue
            if token == "u" and next_token in youth_ages:
                return True
            if token in active_qualifier_tokens:
                return True
            index += 1
        return False

    for index, token in enumerate(tokens):
        if token not in _WOMEN_QUALIFIER_ALIASES:
            continue
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        is_explicit_prefix = (
            token in ({"women", "wom"} | _FOREIGN_WOMEN_TOKENS)
            and index == 0
            and len(tokens) > 1
        )
        is_suffix = index > 0 and (
            index == len(tokens) - 1
            or next_token in {"team", "women"}
            or suffix_has_qualifier(index + 1)
        )
        if is_explicit_prefix:
            forms.add(f"{token}:prefix")
        if is_suffix:
            forms.add(f"{token}:suffix")

    return frozenset(forms)


def _pair_has_women_marker_variation(pair: _OutcomeEventPair) -> bool:
    return _pair_has_women_marker_variation_cached(pair, text_cache=_OutcomeTextCache())


def _pair_has_women_marker_variation_cached(
    pair: _OutcomeEventPair,
    *,
    text_cache: _OutcomeTextCache,
) -> bool:
    for left_name, right_name in _oriented_pair_team_names(pair):
        left_qualifiers = text_cache.qualifiers(left_name, sport=pair.left.sport)
        right_qualifiers = text_cache.qualifiers(right_name, sport=pair.left.sport)
        if (
            left_qualifiers == right_qualifiers
            and "women" in left_qualifiers
            and text_cache.women_marker_forms(left_name) != text_cache.women_marker_forms(right_name)
        ):
            return True
    return False


def _pair_counterpart(pair: _OutcomeEventPair, event: _OutcomeEvent) -> _OutcomeEvent:
    return pair.right if pair.left == event else pair.left


def _tennis_candidate_pairs_are_coherent(
    event: _OutcomeEvent,
    candidates: list[_OutcomeEventPair],
    *,
    text_cache: _OutcomeTextCache,
) -> bool:
    if len(candidates) <= 1:
        return True
    counterparts = [_pair_counterpart(pair, event) for pair in candidates]
    for left_index, left in enumerate(counterparts):
        for right in counterparts[left_index + 1 :]:
            if _pair_candidates(left, right, text_cache=text_cache) is None:
                return False
    return True


def _rank_event_pairs(
    events: list[_OutcomeEvent],
    *,
    resolutions: dict[tuple[str, str, str, str, str], _OutcomeEventResolution] | None = None,
    stats: _FootballEventResolutionStats | None = None,
) -> list[_OutcomeEventPair]:
    events_by_slot: dict[tuple[str, str], list[_OutcomeEvent]] = defaultdict(list)
    events_by_pair_bucket: dict[tuple[str, str], list[_OutcomeEvent]] = defaultdict(list)
    for event in events:
        events_by_slot[(event.sport, event.start_time)].append(event)
        pair_bucket = (
            (event.sport, "__tennis_time_drift__")
            if event.sport == "tennis"
            else (event.sport, event.start_time)
        )
        events_by_pair_bucket[pair_bucket].append(event)
    if stats is not None:
        bucket_rows: list[OutcomeFootballEventBucketBenchmarkOut] = []
        for (sport, start_time), slot_events in events_by_slot.items():
            bookmaker_counts = Counter(event.bookmaker_id for event in slot_events)
            total_pairs = len(slot_events) * (len(slot_events) - 1) // 2
            same_bookmaker_pairs = sum(
                count * (count - 1) // 2 for count in bookmaker_counts.values()
            )
            bucket_rows.append(
                OutcomeFootballEventBucketBenchmarkOut(
                    sport=sport,
                    start_time=start_time,
                    event_count=len(slot_events),
                    bookmaker_count=len(bookmaker_counts),
                    candidate_pair_count=total_pairs - same_bookmaker_pairs,
                )
            )
        stats.football_event_time_slot_count = len(events_by_slot)
        stats.football_event_max_events_per_slot = max(
            (row.event_count for row in bucket_rows),
            default=0,
        )
        stats.top_football_event_buckets = sorted(
            bucket_rows,
            key=lambda row: (
                row.candidate_pair_count,
                row.event_count,
                row.start_time,
            ),
            reverse=True,
        )[:20]

    accepted: list[_OutcomeEventPair] = []
    for events in events_by_pair_bucket.values():
        text_cache = _OutcomeTextCache(stats)
        resolution_by_event = (
            {event: resolutions.get(_event_key(event)) for event in events}
            if resolutions is not None
            else {}
        )
        time_support = Counter(event.start_time for event in events)
        slot_bookmaker_support: dict[frozenset[int], set[str]] = defaultdict(set)
        for event, resolution in resolution_by_event.items():
            if resolution is not None:
                slot_bookmaker_support[_slot_team_ids(resolution)].add(
                    event.bookmaker_id
                )
        all_pairs: list[_OutcomeEventPair] = []
        candidates_by_event: dict[_OutcomeEvent, list[_OutcomeEventPair]] = defaultdict(list)
        for idx, left in enumerate(events):
            for right in events[idx + 1 :]:
                if left.bookmaker_id == right.bookmaker_id:
                    continue
                pair_left = left
                pair_right = right
                if left.sport == "tennis" and left.start_time != right.start_time:
                    pair_left, pair_right = sorted(
                        (left, right),
                        key=lambda event: (
                            -time_support[event.start_time],
                            event.start_time,
                            event.bookmaker_id,
                            normalize_identity_text(event.home_team),
                            normalize_identity_text(event.away_team),
                        ),
                    )
                if _should_skip_disjoint_canonical_slots(
                    pair_left,
                    pair_right,
                    left_resolution=resolution_by_event.get(pair_left),
                    right_resolution=resolution_by_event.get(pair_right),
                    slot_bookmaker_support=slot_bookmaker_support,
                    text_cache=text_cache,
                ):
                    if stats is not None:
                        stats.football_event_canonical_conflict_skip_count += 1
                        stats.football_event_canonical_conflict_fuzzy_score_avoided_count += (
                            4
                        )
                    continue
                pair = _pair_candidates(
                    pair_left,
                    pair_right,
                    text_cache=text_cache,
                    stats=stats,
                )
                if pair is None:
                    continue
                all_pairs.append(pair)
                candidates_by_event[left].append(pair)
                candidates_by_event[right].append(pair)

        for event, candidates in candidates_by_event.items():
            ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
            if not ranked:
                continue
            if event.sport == "tennis":
                if not _tennis_candidate_pairs_are_coherent(
                    event,
                    ranked,
                    text_cache=text_cache,
                ):
                    continue
                for candidate in ranked:
                    if candidate not in accepted:
                        accepted.append(candidate)
                continue
            best = ranked[0]
            if len(ranked) > 1 and best.score - ranked[1].score < _FOOTBALL_AUTO_MATCH_MARGIN:
                continue
            counterpart = best.right if best.left == event else best.left
            counterpart_ranked = sorted(
                candidates_by_event.get(counterpart, []),
                key=lambda item: item.score,
                reverse=True,
            )
            if not counterpart_ranked or counterpart_ranked[0] != best:
                continue
            if best not in accepted:
                accepted.append(best)

        for pair in all_pairs:
            oriented_team_ids = (
                _oriented_pair_team_ids_from_resolutions(pair, resolutions)
                if resolutions is not None
                else None
            )
            if (
                _pair_has_compatible_women_context(
                    pair,
                    oriented_team_ids=oriented_team_ids,
                    text_cache=text_cache,
                )
                and _pair_has_women_marker_variation_cached(pair, text_cache=text_cache)
                and pair not in accepted
            ):
                accepted.append(pair)

    return sorted(accepted, key=lambda item: item.score, reverse=True)


def _build_football_event_resolutions(
    raw_list: list[RawOutcomeOffer],
    *,
    stats: _FootballEventResolutionStats | None = None,
) -> FootballEventResolutionMap:
    events = _unique_events(raw_list)
    if stats is not None:
        stats.football_unique_event_count = len(events)
    resolutions: FootballEventResolutionMap = {}
    lookup_started_at = time.perf_counter()
    for event in events:
        slot = _resolve_event_slot(event)
        if slot is None:
            continue
        resolutions[_event_key(event)] = _OutcomeEventResolution(slot=slot)
    if stats is not None:
        stats.football_event_slot_lookup_ms += _elapsed_ms(lookup_started_at)

    ranking_started_at = time.perf_counter()
    ranked_pairs = _rank_event_pairs(events, resolutions=resolutions, stats=stats)
    if stats is not None:
        stats.football_event_pair_ranking_ms += _elapsed_ms(ranking_started_at)

    for pair in ranked_pairs:
        left_key = _event_key(pair.left)
        right_key = _event_key(pair.right)
        left_resolution = resolutions.get(left_key)
        right_resolution = resolutions.get(right_key)

        if left_resolution is not None and right_resolution is not None:
            if _same_slot(left_resolution, right_resolution):
                continue
            if pair.left.sport == "tennis":
                mutation_started_at = time.perf_counter()
                _move_resolution_component(
                    resolutions,
                    source_resolution=right_resolution,
                    target_slot=left_resolution.slot,
                    source_target_orientation=_orientation_from_pair(
                        left_resolution.orientation,
                        pair.orientation,
                    ),
                )
                if stats is not None:
                    stats.football_event_slot_mutation_ms += _elapsed_ms(
                        mutation_started_at
                    )
                continue
            if pair.orientation == _REVERSED_ORIENTATION and _reversed_slots(
                left_resolution, right_resolution
            ):
                mutation_started_at = time.perf_counter()
                _move_resolution_component(
                    resolutions,
                    source_resolution=right_resolution,
                    target_slot=left_resolution.slot,
                    source_target_orientation=_orientation_from_pair(
                        left_resolution.orientation,
                        pair.orientation,
                    ),
                )
                if stats is not None:
                    stats.football_event_slot_mutation_ms += _elapsed_ms(
                        mutation_started_at
                    )
                continue
            if _pair_has_compatible_women_context(
                pair,
                oriented_team_ids=_oriented_pair_team_ids(
                    pair,
                    left_resolution,
                    right_resolution,
                ),
            ):
                mutation_started_at = time.perf_counter()
                _move_resolution_component(
                    resolutions,
                    source_resolution=right_resolution,
                    target_slot=left_resolution.slot,
                    source_target_orientation=_orientation_from_pair(
                        left_resolution.orientation,
                        pair.orientation,
                    ),
                )
                if stats is not None:
                    stats.football_event_slot_mutation_ms += _elapsed_ms(
                        mutation_started_at
                    )
            continue

        if left_resolution is not None:
            resolutions[right_key] = _OutcomeEventResolution(
                slot=left_resolution.slot,
                orientation=_orientation_from_pair(left_resolution.orientation, pair.orientation),
            )
            continue

        if right_resolution is not None:
            resolutions[left_key] = _OutcomeEventResolution(
                slot=right_resolution.slot,
                orientation=_orientation_from_pair(right_resolution.orientation, pair.orientation),
            )
            continue

        mutation_started_at = time.perf_counter()
        slot = _create_event_slot(pair)
        if stats is not None:
            stats.football_event_slot_mutation_ms += _elapsed_ms(mutation_started_at)
        if slot is None:
            continue
        resolutions[left_key] = _OutcomeEventResolution(slot=slot)
        resolutions[right_key] = _OutcomeEventResolution(
            slot=slot,
            orientation=pair.orientation,
        )

    return resolutions


def _map_outcome_code_for_orientation(
    market_type: str,
    outcome_code: str,
    orientation: str,
) -> str | None:
    if orientation == _SAME_ORIENTATION:
        return outcome_code
    if market_type == "football_total_goals":
        return outcome_code
    if market_type == "football_result":
        return {
            "home": "away",
            "away": "home",
            "draw": "draw",
        }.get(outcome_code)
    if market_type == "football_double_chance":
        return {
            "home_or_draw": "draw_or_away",
            "draw_or_away": "home_or_draw",
            "home_or_away": "home_or_away",
        }.get(outcome_code)
    if market_type in _TENNIS_MATCH_WINNER_MARKETS:
        return {
            "home": "away",
            "away": "home",
        }.get(outcome_code)
    return None


def _team_review_proxy_rows(raw_list: list[RawOutcomeOffer]) -> list[RawOddsData]:
    rows: list[RawOddsData] = []
    seen: set[tuple[str, str, str | None, str, str]] = set()
    for raw in raw_list:
        if not raw.home_team.strip() or not raw.away_team.strip():
            continue
        key = (
            raw.bookmaker_id,
            raw.sport,
            raw.start_time,
            normalize_identity_text(raw.home_team),
            normalize_identity_text(raw.away_team),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=raw.home_team,
                away_team=raw.away_team,
                source_url=raw.source_url,
                market_type=raw.market_type,
                player_name=None,
                threshold=raw.line or 0.0,
                over_odds=raw.odds,
                under_odds=None,
                start_time=raw.start_time,
            )
        )
    return rows


def _format_outcome_event_slot(slot: _OutcomeEventSlot) -> str:
    return f"{slot.home_team} vs {slot.away_team}"


def _build_event_slots_by_time(
    event_resolutions: FootballEventResolutionMap,
) -> dict[tuple[str, str], list[_OutcomeEventSlot]]:
    slots_by_time: dict[
        tuple[str, str],
        dict[tuple[str, str, int, int], _OutcomeEventSlot],
    ] = defaultdict(dict)
    for resolution in event_resolutions.values():
        slot = resolution.slot
        slots_by_time[(slot.sport, slot.start_time)][slot.key] = slot
    return {
        time_key: sorted(
            slots.values(),
            key=lambda slot: (
                slot.home_team.lower(),
                slot.away_team.lower(),
                slot.home_team_id,
                slot.away_team_id,
            ),
        )
        for time_key, slots in slots_by_time.items()
    }


def _unresolved_outcome_matchup_context(
    raw: RawOutcomeOffer,
    *,
    raw_team_name: str,
    reason_code: str,
    home_resolution,
    away_resolution,
    event_slots_by_time: dict[tuple[str, str], list[_OutcomeEventSlot]],
) -> tuple[int, list[str], list[str]]:
    if raw.start_time is None:
        return 0, [], []

    slots = event_slots_by_time.get((raw.sport, raw.start_time), [])
    available_matchups = [_format_outcome_event_slot(slot) for slot in slots[:12]]

    known_team_id: int | None = None
    if reason_code == "unresolved_home_team":
        known_team_id = away_resolution.team_id
    elif reason_code == "unresolved_away_team":
        known_team_id = home_resolution.team_id

    if known_team_id is None:
        return 0, [], available_matchups

    scored_candidates: dict[str, float] = {}
    for slot in slots:
        if known_team_id == slot.home_team_id:
            candidate_team = slot.away_team
        elif known_team_id == slot.away_team_id:
            candidate_team = slot.home_team
        else:
            continue
        if not _same_team_context(raw_team_name, candidate_team, sport=raw.sport):
            continue
        score = _team_similarity(raw_team_name, candidate_team, sport=raw.sport)
        if score < TEAM_REVIEW_CANDIDATE_THRESHOLD:
            continue
        matchup_label = _format_outcome_event_slot(slot)
        scored_candidates[matchup_label] = max(
            scored_candidates.get(matchup_label, 0.0),
            score,
        )

    candidate_matchups = [
        matchup
        for matchup, _score in sorted(
            scored_candidates.items(),
            key=lambda item: (-item[1], item[0]),
        )[:8]
    ]
    return len(scored_candidates), candidate_matchups, available_matchups


def _unresolved_team_diagnostic(
    raw: RawOutcomeOffer,
    *,
    raw_team_name: str,
    reason_code: str,
    candidate_count: int = 0,
    candidate_matchups: list[str] | None = None,
    available_matchups_same_slot: list[str] | None = None,
) -> UnresolvedOddsDiagnostic:
    direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
    return UnresolvedOddsDiagnostic(
        bookmaker_id=raw.bookmaker_id,
        raw_league_id=raw.league_id,
        league_id=direct_league.league_id,
        sport=raw.sport,
        market_type=raw.market_type,
        player_name=None,
        raw_team_name=raw_team_name,
        normalized_team_name=resolve_team_name(
            raw_team_name,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        ).team_name,
        start_time=raw.start_time,
        threshold=raw.line or 0.0,
        over_odds=raw.odds,
        under_odds=None,
        reason_code=reason_code,
        candidate_count=candidate_count,
        candidate_matchups=candidate_matchups or [],
        available_matchups_same_slot=available_matchups_same_slot or [],
    )


def _unresolved_outcome_key(
    raw: RawOutcomeOffer,
    *,
    raw_team_name: str,
    reason_code: str,
) -> tuple[str, str, str, str | None, str]:
    market_type = (
        ""
        if raw.sport == "football"
        and reason_code in {"unresolved_home_team", "unresolved_away_team"}
        else raw.market_type
    )
    return (raw.bookmaker_id, market_type, raw_team_name, raw.start_time, reason_code)


def _normalized_offer_from_resolution(
    raw: RawOutcomeOffer,
    *,
    league_id: str,
    resolution: _OutcomeEventResolution,
) -> NormalizedOutcomeOffer | None:
    outcome_code = _map_outcome_code_for_orientation(
        raw.market_type,
        raw.outcome_code,
        resolution.orientation,
    )
    if outcome_code is None:
        logger.warning(
            "Skipping reversed outcome %s/%s for bookmaker %s",
            raw.market_type,
            raw.outcome_code,
            raw.bookmaker_id,
        )
        return None

    match_id = generate_match_id(
        resolution.slot.home_team_id,
        resolution.slot.away_team_id,
        resolution.slot.start_time,
        raw.sport,
    )
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=raw.bookmaker_id,
        league_id=league_id,
        sport=raw.sport,
        home_team_id=resolution.slot.home_team_id,
        away_team_id=resolution.slot.away_team_id,
        home_team=resolution.slot.home_team,
        away_team=resolution.slot.away_team,
        source_url=raw.source_url,
        market_type=raw.market_type,
        outcome_code=outcome_code,
        odds=raw.odds,
        line=raw.line,
        raw_label=raw.raw_label,
        start_time=resolution.slot.start_time,
    )


def normalize_outcome_offers_with_context(
    raw_list: list[RawOutcomeOffer],
) -> OutcomeNormalizationResult:
    normalization_started_at = time.perf_counter()
    autocreate_started_at = time.perf_counter()
    auto_created_team_count = _autocreate_cross_book_football_teams(raw_list)
    auto_create_football_teams_ms = _elapsed_ms(autocreate_started_at)

    football_resolution_stats = _FootballEventResolutionStats()
    event_resolution_started_at = time.perf_counter()
    event_resolutions = _build_football_event_resolutions(
        raw_list,
        stats=football_resolution_stats,
    )
    event_slots_by_time = _build_event_slots_by_time(event_resolutions)
    football_event_resolution_ms = _elapsed_ms(event_resolution_started_at)

    row_normalization_started_at = time.perf_counter()
    unresolved_event_rows = [
        raw
        for raw in raw_list
        if (event_key := _event_key_from_raw(raw)) is None
        or event_key not in event_resolutions
    ]
    team_review_proxy_rows = _team_review_proxy_rows(unresolved_event_rows)
    team_review_proxy_metrics = TeamReviewDiagnosticsMetrics()
    team_review_proxy_started_at = time.perf_counter()
    team_review_cases = build_team_review_cases_for_diagnostics(
        team_review_proxy_rows,
        metrics=team_review_proxy_metrics,
    )
    team_review_proxy_ms = _elapsed_ms(team_review_proxy_started_at)

    normalized: list[NormalizedOutcomeOffer] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []
    seen_unresolved: set[tuple[str, str, str, str | None, str]] = set()

    raw_rows_by_bookmaker = Counter(raw.bookmaker_id for raw in raw_list)
    normalized_rows_by_bookmaker: Counter[str] = Counter()
    event_resolution_rows_by_bookmaker: Counter[str] = Counter()
    direct_resolution_rows_by_bookmaker: Counter[str] = Counter()
    skipped_unresolved_rows_by_bookmaker: Counter[str] = Counter()
    unresolved_diagnostics_by_bookmaker: Counter[str] = Counter()
    missing_start_rows_by_bookmaker: Counter[str] = Counter()
    unsupported_reversed_rows_by_bookmaker: Counter[str] = Counter()

    missing_start_time_count = 0
    event_resolution_offer_count = 0
    direct_resolution_attempt_count = 0
    direct_resolution_success_count = 0
    skipped_unresolved_row_count = 0
    unsupported_reversed_offer_count = 0
    league_resolution_seconds = 0.0
    event_resolution_offer_build_seconds = 0.0
    direct_team_resolution_seconds = 0.0
    unresolved_context_seconds = 0.0
    direct_offer_build_seconds = 0.0
    row_iteration_started_at = time.perf_counter()
    for raw in raw_list:
        league_started_at = time.perf_counter()
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        league_resolution_seconds += time.perf_counter() - league_started_at
        if raw.start_time is None:
            missing_start_time_count += 1
            missing_start_rows_by_bookmaker[raw.bookmaker_id] += 1
            key = (
                raw.bookmaker_id,
                raw.raw_label or raw.market_type,
                raw.home_team,
                raw.start_time,
                "missing_start_time",
            )
            if key not in seen_unresolved:
                seen_unresolved.add(key)
                unresolved_diagnostics_by_bookmaker[raw.bookmaker_id] += 1
                unresolved.append(
                    _unresolved_team_diagnostic(
                        raw,
                        raw_team_name=raw.home_team,
                        reason_code="missing_start_time",
                    )
                )
            continue

        event_key = _event_key_from_raw(raw)
        event_resolution = event_resolutions.get(event_key) if event_key is not None else None
        if event_resolution is not None:
            offer_started_at = time.perf_counter()
            normalized_offer = _normalized_offer_from_resolution(
                raw,
                league_id=direct_league.league_id,
                resolution=event_resolution,
            )
            event_resolution_offer_build_seconds += (
                time.perf_counter() - offer_started_at
            )
            if normalized_offer is not None:
                normalized.append(normalized_offer)
                event_resolution_offer_count += 1
                normalized_rows_by_bookmaker[raw.bookmaker_id] += 1
                event_resolution_rows_by_bookmaker[raw.bookmaker_id] += 1
            else:
                unsupported_reversed_offer_count += 1
                unsupported_reversed_rows_by_bookmaker[raw.bookmaker_id] += 1
            continue

        direct_resolution_attempt_count += 1
        team_resolution_started_at = time.perf_counter()
        home_resolution = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        away_resolution = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        direct_team_resolution_seconds += (
            time.perf_counter() - team_resolution_started_at
        )

        if home_resolution.team_id is None or away_resolution.team_id is None:
            if home_resolution.team_id is None:
                context_started_at = time.perf_counter()
                (
                    candidate_count,
                    candidate_matchups,
                    available_matchups,
                ) = _unresolved_outcome_matchup_context(
                    raw,
                    raw_team_name=raw.home_team,
                    reason_code="unresolved_home_team",
                    home_resolution=home_resolution,
                    away_resolution=away_resolution,
                    event_slots_by_time=event_slots_by_time,
                )
                unresolved_context_seconds += time.perf_counter() - context_started_at
                key = _unresolved_outcome_key(
                    raw,
                    raw_team_name=raw.home_team,
                    reason_code="unresolved_home_team",
                )
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved_diagnostics_by_bookmaker[raw.bookmaker_id] += 1
                    unresolved.append(
                        _unresolved_team_diagnostic(
                            raw,
                            raw_team_name=raw.home_team,
                            reason_code="unresolved_home_team",
                            candidate_count=candidate_count,
                            candidate_matchups=candidate_matchups,
                            available_matchups_same_slot=available_matchups,
                        )
                    )
            if away_resolution.team_id is None:
                context_started_at = time.perf_counter()
                (
                    candidate_count,
                    candidate_matchups,
                    available_matchups,
                ) = _unresolved_outcome_matchup_context(
                    raw,
                    raw_team_name=raw.away_team,
                    reason_code="unresolved_away_team",
                    home_resolution=home_resolution,
                    away_resolution=away_resolution,
                    event_slots_by_time=event_slots_by_time,
                )
                unresolved_context_seconds += time.perf_counter() - context_started_at
                key = _unresolved_outcome_key(
                    raw,
                    raw_team_name=raw.away_team,
                    reason_code="unresolved_away_team",
                )
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved_diagnostics_by_bookmaker[raw.bookmaker_id] += 1
                    unresolved.append(
                        _unresolved_team_diagnostic(
                            raw,
                            raw_team_name=raw.away_team,
                            reason_code="unresolved_away_team",
                            candidate_count=candidate_count,
                            candidate_matchups=candidate_matchups,
                            available_matchups_same_slot=available_matchups,
                        )
                    )
            skipped_unresolved_row_count += 1
            skipped_unresolved_rows_by_bookmaker[raw.bookmaker_id] += 1
            continue

        if raw.sport == "tennis" and raw.market_type in _TENNIS_MATCH_WINNER_MARKETS:
            key = _unresolved_outcome_key(
                raw,
                raw_team_name=raw.home_team,
                reason_code="unresolved_event_matchup",
            )
            if key not in seen_unresolved:
                seen_unresolved.add(key)
                unresolved_diagnostics_by_bookmaker[raw.bookmaker_id] += 1
                unresolved.append(
                    _unresolved_team_diagnostic(
                        raw,
                        raw_team_name=raw.home_team,
                        reason_code="unresolved_event_matchup",
                    )
                )
            skipped_unresolved_row_count += 1
            skipped_unresolved_rows_by_bookmaker[raw.bookmaker_id] += 1
            continue

        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        direct_offer_started_at = time.perf_counter()
        normalized.append(
            NormalizedOutcomeOffer(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=direct_league.league_id,
                sport=raw.sport,
                home_team_id=home_resolution.team_id,
                away_team_id=away_resolution.team_id,
                home_team=home_resolution.team_name,
                away_team=away_resolution.team_name,
                source_url=raw.source_url,
                market_type=raw.market_type,
                outcome_code=raw.outcome_code,
                odds=raw.odds,
                line=raw.line,
                raw_label=raw.raw_label,
                start_time=raw.start_time,
            )
        )
        direct_offer_build_seconds += time.perf_counter() - direct_offer_started_at
        direct_resolution_success_count += 1
        normalized_rows_by_bookmaker[raw.bookmaker_id] += 1
        direct_resolution_rows_by_bookmaker[raw.bookmaker_id] += 1

    row_iteration_ms = _elapsed_ms(row_iteration_started_at)

    football_team_review_cases = [
        case for case in team_review_cases if case.sport == "football"
    ]
    football_team_review_alias_misses = [
        case
        for case in football_team_review_cases
        if case.suggested_team_id is not None and case.confidence != "low"
    ]
    row_normalization_ms = _elapsed_ms(row_normalization_started_at)
    wall_ms = _elapsed_ms(normalization_started_at)
    bookmaker_rows = [
        OutcomeNormalizationBookmakerBenchmarkOut(
            bookmaker_id=bookmaker_id,
            raw_rows=raw_rows_by_bookmaker[bookmaker_id],
            normalized_rows=normalized_rows_by_bookmaker[bookmaker_id],
            event_resolution_rows=event_resolution_rows_by_bookmaker[bookmaker_id],
            direct_resolution_rows=direct_resolution_rows_by_bookmaker[bookmaker_id],
            skipped_unresolved_rows=skipped_unresolved_rows_by_bookmaker[bookmaker_id],
            unresolved_diagnostic_count=unresolved_diagnostics_by_bookmaker[
                bookmaker_id
            ],
            missing_start_time_rows=missing_start_rows_by_bookmaker[bookmaker_id],
            unsupported_reversed_rows=unsupported_reversed_rows_by_bookmaker[
                bookmaker_id
            ],
        )
        for bookmaker_id in sorted(raw_rows_by_bookmaker)
    ]
    run_detail = OutcomeNormalizationRunBenchmarkOut(
        run_index=1,
        wall_ms=wall_ms,
        raw_outcome_offer_count=len(raw_list),
        normalized_outcome_offer_count=len(normalized),
        unresolved_outcome_offer_count=len(unresolved),
        football_unique_event_count=football_resolution_stats.football_unique_event_count,
        football_event_pair_candidate_count=(
            football_resolution_stats.football_event_pair_candidate_count
        ),
        football_event_fuzzy_score_count=(
            football_resolution_stats.football_event_fuzzy_score_count
        ),
        football_event_canonical_conflict_skip_count=(
            football_resolution_stats.football_event_canonical_conflict_skip_count
        ),
        football_event_canonical_conflict_fuzzy_score_avoided_count=(
            football_resolution_stats
            .football_event_canonical_conflict_fuzzy_score_avoided_count
        ),
        football_team_review_case_count=len(football_team_review_cases),
        auto_create_football_teams_ms=auto_create_football_teams_ms,
        football_event_resolution_ms=football_event_resolution_ms,
        football_event_pair_ranking_ms=(
            football_resolution_stats.football_event_pair_ranking_ms
        ),
        football_event_slot_lookup_ms=(
            football_resolution_stats.football_event_slot_lookup_ms
        ),
        row_normalization_ms=row_normalization_ms,
        team_review_proxy_rows=len(team_review_proxy_rows),
        team_review_proxy_ms=team_review_proxy_ms,
        team_review_proxy_slot_resolution_ms=(
            team_review_proxy_metrics.event_slot_resolution_ms
        ),
        team_review_proxy_case_build_ms=team_review_proxy_metrics.case_build_ms,
        team_review_proxy_resolve_league_ms=(
            team_review_proxy_metrics.resolve_league_ms
        ),
        team_review_proxy_resolve_team_ms=team_review_proxy_metrics.resolve_team_ms,
        team_review_proxy_slot_candidate_ms=(
            team_review_proxy_metrics.slot_candidate_ms
        ),
        team_review_proxy_global_candidate_ms=(
            team_review_proxy_metrics.global_candidate_ms
        ),
        team_review_proxy_duplicate_suppression_ms=(
            team_review_proxy_metrics.duplicate_suppression_ms
        ),
        team_review_proxy_resolve_team_cache_hits=(
            team_review_proxy_metrics.resolve_team_cache_hit_count
        ),
        team_review_proxy_slot_candidate_search_count=(
            team_review_proxy_metrics.slot_candidate_search_count
        ),
        team_review_proxy_slot_candidate_cache_hits=(
            team_review_proxy_metrics.slot_candidate_cache_hit_count
        ),
        team_review_proxy_global_candidate_search_count=(
            team_review_proxy_metrics.global_candidate_search_count
        ),
        team_review_proxy_global_candidate_cache_hits=(
            team_review_proxy_metrics.global_candidate_cache_hit_count
        ),
        team_review_proxy_duplicate_suppression_count=(
            team_review_proxy_metrics.duplicate_suppression_hit_count
        ),
        row_iteration_ms=row_iteration_ms,
        missing_start_time_count=missing_start_time_count,
        event_resolution_offer_count=event_resolution_offer_count,
        direct_resolution_attempt_count=direct_resolution_attempt_count,
        direct_resolution_success_count=direct_resolution_success_count,
        skipped_unresolved_row_count=skipped_unresolved_row_count,
        unsupported_reversed_offer_count=unsupported_reversed_offer_count,
        league_resolution_ms=int(league_resolution_seconds * 1000),
        event_resolution_offer_build_ms=int(
            event_resolution_offer_build_seconds * 1000
        ),
        direct_team_resolution_ms=int(direct_team_resolution_seconds * 1000),
        unresolved_context_ms=int(unresolved_context_seconds * 1000),
        direct_offer_build_ms=int(direct_offer_build_seconds * 1000),
    )
    benchmark = OutcomeNormalizationBenchmarkOut(
        runs=1,
        raw_outcome_offer_count=len(raw_list),
        normalized_outcome_offer_count=len(normalized),
        unresolved_outcome_offer_count=len(unresolved),
        football_unique_event_count=(
            football_resolution_stats.football_unique_event_count
        ),
        football_event_pair_candidate_count=(
            football_resolution_stats.football_event_pair_candidate_count
        ),
        football_event_fuzzy_score_count=(
            football_resolution_stats.football_event_fuzzy_score_count
        ),
        football_event_canonical_conflict_skip_count=(
            football_resolution_stats.football_event_canonical_conflict_skip_count
        ),
        football_event_canonical_conflict_fuzzy_score_avoided_count=(
            football_resolution_stats
            .football_event_canonical_conflict_fuzzy_score_avoided_count
        ),
        auto_created_football_team_count=auto_created_team_count,
        football_team_review_case_count=len(football_team_review_cases),
        football_team_review_alias_miss_count=len(football_team_review_alias_misses),
        football_team_review_unknown_count=(
            len(football_team_review_cases)
            - len(football_team_review_alias_misses)
        ),
        football_team_review_same_slot_alias_miss_count=sum(
            1
            for case in football_team_review_alias_misses
            if case.reason_code == "candidate_team_match_same_start_time"
        ),
        football_team_review_global_alias_miss_count=sum(
            1
            for case in football_team_review_alias_misses
            if case.reason_code == "candidate_team_search"
        ),
        auto_create_football_teams_ms=auto_create_football_teams_ms,
        football_event_resolution_ms=football_event_resolution_ms,
        football_event_pair_ranking_ms=(
            football_resolution_stats.football_event_pair_ranking_ms
        ),
        football_event_slot_lookup_ms=(
            football_resolution_stats.football_event_slot_lookup_ms
        ),
        football_event_slot_mutation_ms=(
            football_resolution_stats.football_event_slot_mutation_ms
        ),
        row_normalization_ms=row_normalization_ms,
        team_review_proxy_rows=len(team_review_proxy_rows),
        team_review_proxy_ms=team_review_proxy_ms,
        team_review_proxy_slot_resolution_ms=(
            team_review_proxy_metrics.event_slot_resolution_ms
        ),
        team_review_proxy_case_build_ms=team_review_proxy_metrics.case_build_ms,
        team_review_proxy_resolve_league_ms=(
            team_review_proxy_metrics.resolve_league_ms
        ),
        team_review_proxy_resolve_team_ms=team_review_proxy_metrics.resolve_team_ms,
        team_review_proxy_slot_candidate_ms=(
            team_review_proxy_metrics.slot_candidate_ms
        ),
        team_review_proxy_global_candidate_ms=(
            team_review_proxy_metrics.global_candidate_ms
        ),
        team_review_proxy_duplicate_suppression_ms=(
            team_review_proxy_metrics.duplicate_suppression_ms
        ),
        team_review_proxy_resolve_team_cache_hits=(
            team_review_proxy_metrics.resolve_team_cache_hit_count
        ),
        team_review_proxy_slot_candidate_search_count=(
            team_review_proxy_metrics.slot_candidate_search_count
        ),
        team_review_proxy_slot_candidate_cache_hits=(
            team_review_proxy_metrics.slot_candidate_cache_hit_count
        ),
        team_review_proxy_global_candidate_search_count=(
            team_review_proxy_metrics.global_candidate_search_count
        ),
        team_review_proxy_global_candidate_cache_hits=(
            team_review_proxy_metrics.global_candidate_cache_hit_count
        ),
        team_review_proxy_duplicate_suppression_count=(
            team_review_proxy_metrics.duplicate_suppression_hit_count
        ),
        row_iteration_ms=row_iteration_ms,
        missing_start_time_count=missing_start_time_count,
        event_resolution_offer_count=event_resolution_offer_count,
        direct_resolution_attempt_count=direct_resolution_attempt_count,
        direct_resolution_success_count=direct_resolution_success_count,
        skipped_unresolved_row_count=skipped_unresolved_row_count,
        unsupported_reversed_offer_count=unsupported_reversed_offer_count,
        league_resolution_ms=int(league_resolution_seconds * 1000),
        event_resolution_offer_build_ms=int(
            event_resolution_offer_build_seconds * 1000
        ),
        direct_team_resolution_ms=int(direct_team_resolution_seconds * 1000),
        unresolved_context_ms=int(unresolved_context_seconds * 1000),
        direct_offer_build_ms=int(direct_offer_build_seconds * 1000),
        football_event_time_slot_count=(
            football_resolution_stats.football_event_time_slot_count
        ),
        football_event_max_events_per_slot=(
            football_resolution_stats.football_event_max_events_per_slot
        ),
        run_details=[run_detail],
        bookmakers=bookmaker_rows,
        top_football_event_buckets=(
            football_resolution_stats.top_football_event_buckets
        ),
    )

    return OutcomeNormalizationResult(
        normalized=normalized,
        unresolved=unresolved,
        team_review_cases=team_review_cases,
        benchmark=benchmark,
        football_event_resolutions=event_resolutions,
    )


def normalize_outcome_offers_with_benchmark(
    raw_list: list[RawOutcomeOffer],
) -> tuple[
    list[NormalizedOutcomeOffer],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
    OutcomeNormalizationBenchmarkOut,
]:
    result = normalize_outcome_offers_with_context(raw_list)
    return (
        result.normalized,
        result.unresolved,
        result.team_review_cases,
        result.benchmark,
    )


def normalize_outcome_offers_with_diagnostics(
    raw_list: list[RawOutcomeOffer],
) -> tuple[
    list[NormalizedOutcomeOffer],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
]:
    normalized, unresolved, team_review_cases, _benchmark = (
        normalize_outcome_offers_with_benchmark(raw_list)
    )
    return normalized, unresolved, team_review_cases
