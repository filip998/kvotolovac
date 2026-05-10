from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


TENNIS_NORMAL_DRIFT_MINUTES = 60.0
TENNIS_BROAD_DRIFT_MINUTES = 360.0

_SURFACE_TOKEN_RE = re.compile(r"[^\W\d_]+\.?", re.UNICODE)
_TENNIS_SURNAME_PARTICLES = frozenset(
    {"da", "de", "del", "della", "di", "du", "la", "le", "van", "von"}
)
_DOUBLES_SURFACE_RE = re.compile(r"[/\\／&]")


@dataclass(frozen=True)
class TennisPlayerName:
    family_tokens: tuple[str, ...]
    given_tokens: tuple[str, ...] = ()
    abbreviated_given_tokens: tuple[str, ...] = ()
    order: str = "western"

    @property
    def has_given_evidence(self) -> bool:
        return bool(self.given_tokens or self.abbreviated_given_tokens)

    @property
    def has_full_given(self) -> bool:
        return bool(self.given_tokens)


@dataclass(frozen=True)
class TennisPlayerMatch:
    score: float
    strength: str
    broad_time_safe: bool


@dataclass(frozen=True)
class TennisCompetitorPairMatch:
    orientation: str
    home_score: float
    away_score: float
    broad_time_safe: bool

    @property
    def avg_score(self) -> float:
        return (self.home_score + self.away_score) / 2

    @property
    def weak_side_score(self) -> float:
        return min(self.home_score, self.away_score)

    @property
    def max_time_delta_minutes(self) -> float:
        return (
            TENNIS_BROAD_DRIFT_MINUTES
            if self.broad_time_safe
            else TENNIS_NORMAL_DRIFT_MINUTES
        )


def _strip_diacritics(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_token(token: str) -> str:
    token = _strip_diacritics(token).lower().rstrip(".")
    return re.sub(r"[^a-z0-9]+", "", token)


def _surface_tokens(segment: str) -> tuple[tuple[str, bool], ...]:
    tokens: list[tuple[str, bool]] = []
    for match in _SURFACE_TOKEN_RE.finditer(segment.replace("-", " ")):
        raw_token = match.group(0)
        normalized = _normalize_token(raw_token)
        if not normalized:
            continue
        tokens.append((normalized, raw_token.endswith(".") or len(normalized) == 1))
    return tuple(tokens)


def _split_given_tokens(
    tokens: tuple[tuple[str, bool], ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not tokens:
        return (), ()
    if all(is_abbreviated for _token, is_abbreviated in tokens):
        return (), tuple(token for token, _is_abbreviated in tokens)
    return tuple(token for token, _is_abbreviated in tokens), ()


def _is_doubles_like_surface(name: str) -> bool:
    if _DOUBLES_SURFACE_RE.search(name):
        return True
    return name.count(",") > 1


def _western_parse(tokens: tuple[tuple[str, bool], ...], *, order: str) -> TennisPlayerName | None:
    if len(tokens) < 2:
        return None

    suffix_count = 0
    for token, is_abbreviated in reversed(tokens):
        if not is_abbreviated:
            break
        suffix_count += 1
    if suffix_count and suffix_count < len(tokens):
        family = tuple(token for token, _is_abbreviated in tokens[: len(tokens) - suffix_count])
        abbreviated = tuple(token for token, _is_abbreviated in tokens[len(tokens) - suffix_count :])
        return TennisPlayerName(
            family_tokens=family,
            abbreviated_given_tokens=abbreviated,
            order="suffix_abbrev",
        )

    prefix_count = 0
    for token, is_abbreviated in tokens:
        if not is_abbreviated:
            break
        prefix_count += 1
    if prefix_count and prefix_count < len(tokens):
        family = tuple(token for token, _is_abbreviated in tokens[prefix_count:])
        abbreviated = tuple(token for token, _is_abbreviated in tokens[:prefix_count])
        return TennisPlayerName(
            family_tokens=family,
            abbreviated_given_tokens=abbreviated,
            order="prefix_abbrev",
        )

    given = [token for token, _is_abbreviated in tokens[:-1]]
    family = [tokens[-1][0]]
    while len(given) > 1 and given[-1] in _TENNIS_SURNAME_PARTICLES:
        family.insert(0, given.pop())
    return TennisPlayerName(
        family_tokens=tuple(family),
        given_tokens=tuple(given),
        order=order,
    )


def tennis_player_name_variants(name: str) -> tuple[TennisPlayerName, ...]:
    if not name or _is_doubles_like_surface(name):
        return ()

    variants: list[TennisPlayerName] = []
    if "," in name:
        comma_parts = [part.strip() for part in name.split(",") if part.strip()]
        if len(comma_parts) == 2:
            family = tuple(token for token, _is_abbreviated in _surface_tokens(comma_parts[0]))
            given_tokens, abbreviated = _split_given_tokens(_surface_tokens(comma_parts[1]))
            if family and (given_tokens or abbreviated):
                variants.append(
                    TennisPlayerName(
                        family_tokens=family,
                        given_tokens=given_tokens,
                        abbreviated_given_tokens=abbreviated,
                        order="comma_last_first",
                    )
                )
    else:
        tokens = _surface_tokens(name)
        primary = _western_parse(tokens, order="western")
        if primary is not None:
            variants.append(primary)
        if len(tokens) == 2 and not any(is_abbreviated for _token, is_abbreviated in tokens):
            variants.append(
                TennisPlayerName(
                    family_tokens=(tokens[0][0],),
                    given_tokens=(tokens[1][0],),
                    order="unpunctuated_last_first",
                )
            )

    unique: dict[
        tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], TennisPlayerName
    ] = {}
    for variant in variants:
        if not variant.family_tokens or not variant.has_given_evidence:
            continue
        key = (
            variant.family_tokens,
            variant.given_tokens,
            variant.abbreviated_given_tokens,
        )
        unique.setdefault(key, variant)
    return tuple(unique.values())


def _tokens_suffix_compatible(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if left == right:
        return True
    if len(left) > len(right):
        return left[-len(right) :] == right
    if len(right) > len(left):
        return right[-len(left) :] == left
    return False


def _abbreviations_match_full(
    abbreviations: tuple[str, ...],
    full_tokens: tuple[str, ...],
) -> bool:
    if not abbreviations or not full_tokens or len(abbreviations) > len(full_tokens):
        return False
    for abbreviation, full_token in zip(abbreviations, full_tokens):
        if len(abbreviation) == 1:
            if not full_token.startswith(abbreviation):
                return False
            continue
        if not full_token.startswith(abbreviation):
            return False
    return True


def _player_variants_match(
    left: TennisPlayerName,
    right: TennisPlayerName,
) -> TennisPlayerMatch | None:
    if not _tokens_suffix_compatible(left.family_tokens, right.family_tokens):
        return None

    if left.given_tokens and right.given_tokens:
        if left.given_tokens == right.given_tokens:
            return TennisPlayerMatch(
                score=98.0,
                strength="full",
                broad_time_safe=True,
            )
        return None

    if left.abbreviated_given_tokens and right.abbreviated_given_tokens:
        if left.abbreviated_given_tokens == right.abbreviated_given_tokens:
            return TennisPlayerMatch(
                score=94.0,
                strength="abbrev",
                broad_time_safe=False,
            )
        return None

    if left.abbreviated_given_tokens and right.given_tokens:
        if _abbreviations_match_full(left.abbreviated_given_tokens, right.given_tokens):
            return TennisPlayerMatch(
                score=96.0,
                strength="abbrev_full",
                broad_time_safe=True,
            )
        return None

    if right.abbreviated_given_tokens and left.given_tokens:
        if _abbreviations_match_full(right.abbreviated_given_tokens, left.given_tokens):
            return TennisPlayerMatch(
                score=96.0,
                strength="abbrev_full",
                broad_time_safe=True,
            )
        return None

    return None


def match_tennis_player_names(
    left_name: str,
    right_name: str,
) -> TennisPlayerMatch | None:
    left_text = " ".join(token for token, _is_abbreviated in _surface_tokens(left_name))
    right_text = " ".join(token for token, _is_abbreviated in _surface_tokens(right_name))
    if left_text and left_text == right_text:
        variants = tennis_player_name_variants(left_name)
        has_full_given = any(variant.has_full_given for variant in variants)
        return TennisPlayerMatch(
            score=100.0,
            strength="exact",
            broad_time_safe=has_full_given,
        )

    matches: list[TennisPlayerMatch] = []
    for left in tennis_player_name_variants(left_name):
        for right in tennis_player_name_variants(right_name):
            match = _player_variants_match(left, right)
            if match is not None:
                matches.append(match)
    if not matches:
        return None
    return max(
        matches,
        key=lambda match: (
            match.score,
            match.broad_time_safe,
            match.strength,
        ),
    )


def tennis_competitor_pair_matches(
    left_home: str,
    left_away: str,
    right_home: str,
    right_away: str,
) -> tuple[TennisCompetitorPairMatch, ...]:
    matches: list[TennisCompetitorPairMatch] = []

    home_match = match_tennis_player_names(left_home, right_home)
    away_match = match_tennis_player_names(left_away, right_away)
    if home_match is not None and away_match is not None:
        matches.append(
            TennisCompetitorPairMatch(
                orientation="as_listed",
                home_score=home_match.score,
                away_score=away_match.score,
                broad_time_safe=(
                    home_match.broad_time_safe and away_match.broad_time_safe
                ),
            )
        )

    home_match = match_tennis_player_names(left_home, right_away)
    away_match = match_tennis_player_names(left_away, right_home)
    if home_match is not None and away_match is not None:
        matches.append(
            TennisCompetitorPairMatch(
                orientation="reversed",
                home_score=home_match.score,
                away_score=away_match.score,
                broad_time_safe=(
                    home_match.broad_time_safe and away_match.broad_time_safe
                ),
            )
        )

    return tuple(
        sorted(
            matches,
            key=lambda match: (
                match.avg_score,
                match.weak_side_score,
                match.broad_time_safe,
                match.orientation == "as_listed",
            ),
            reverse=True,
        )
    )
