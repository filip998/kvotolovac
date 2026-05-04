from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
import re
from collections import Counter, defaultdict
from functools import lru_cache

from rapidfuzz import fuzz

from ..models.schemas import (
    NormalizedOdds,
    RawOddsData,
    TeamReviewCandidate,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from .league_registry import resolve_league
from .team_registry import (
    DEFAULT_SPORT,
    create_canonical_team,
    resolve_team_alias,
    search_canonical_team_candidates,
)
from .text_normalizer import (
    compact_identity_text,
    normalize_identity_text,
    tokenize_identity_text,
)
from .text_normalizer import _strip_diacritics

logger = logging.getLogger(__name__)

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
TEAM_REVIEW_CANDIDATE_THRESHOLD = 76
ANCHORED_AUTO_APPLY_THRESHOLD = 85
TEAM_REVIEW_MAX_CANDIDATES = 3

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
    "player_turnovers": "player_turnovers",
    "player turnovers": "player_turnovers",
    "turnovers": "player_turnovers",
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
    "home_handicap_ot": "home_handicap_ot",
    "home handicap ot": "home_handicap_ot",
    "handicap (+ot)": "home_handicap_ot",
    "handicap +ot": "home_handicap_ot",
    "hendikep (+ot)": "home_handicap_ot",
    "hendikep (uklj. ot)": "home_handicap_ot",
    "hendikep uklj ot": "home_handicap_ot",
}


def _normalize_team_key(raw_name: str) -> str:
    return normalize_identity_text(raw_name)


@dataclass(frozen=True)
class TeamNameResolution:
    team_id: int | None
    team_name: str
    source: str
    confidence: str
    score: float | None = None


def resolve_team_name(
    raw_name: str,
    league_id: str | None = None,
    bookmaker_id: str | None = None,
    *,
    sport: str = DEFAULT_SPORT,
) -> TeamNameResolution:
    alias_resolution = resolve_team_alias(
        raw_name,
        bookmaker_id=bookmaker_id,
        sport=sport,
    )
    if alias_resolution is not None:
        return TeamNameResolution(
            team_id=alias_resolution.team_id,
            team_name=alias_resolution.team_name,
            source=alias_resolution.source,
            confidence="high",
        )

    del league_id

    return TeamNameResolution(
        team_id=None,
        team_name=raw_name.strip(),
        source="raw",
        confidence="low",
        score=None,
    )


def normalize_team_name(
    raw_name: str,
    league_id: str | None = None,
    bookmaker_id: str | None = None,
    *,
    sport: str = DEFAULT_SPORT,
) -> str:
    return resolve_team_name(
        raw_name,
        league_id=league_id,
        bookmaker_id=bookmaker_id,
        sport=sport,
    ).team_name


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


def _player_name_parts(name: str) -> tuple[list[str], str] | None:
    tokens = _normalize_person_tokens(name)
    tokens = _strip_trailing_suffixes(tokens)
    if len(tokens) < 2:
        return None
    return tokens[:-1], tokens[-1]


_SURNAME_HYPHEN_SPACE_RE = re.compile(r"[\s\-]+")


def _fold_surname(surname: str) -> str:
    return _SURNAME_HYPHEN_SPACE_RE.sub(" ", surname).strip()


def _build_compound_surname_hints(names: list[str]) -> frozenset[str]:
    """Collect bucket-wide multi-word surname hints for `_resolver_player_parts`.

    A surname is a "compound hint" when its hyphen/space-folded form contains
    a space — e.g., ``gilgeous-alexander`` folds to ``gilgeous alexander``,
    a two-word hint that lets a sibling surface like ``S. Gilgeous Alexander``
    re-parse its trailing tokens as a single surname.
    """

    hints: set[str] = set()
    for name in names:
        parts = _player_name_parts(name)
        if not parts:
            continue
        folded = _fold_surname(parts[1])
        if " " in folded:
            hints.add(folded)
    return frozenset(hints)


def _resolver_player_parts(
    name: str,
    *,
    compound_surname_hints: frozenset[str] | None = None,
) -> tuple[list[str], str] | None:
    """Resolver-layer variant of `_player_name_parts` with compound-surname
    awareness.

    `_player_name_parts` is the simple parser: it tokenises by whitespace
    and treats the LAST token as the surname. That misses two production-
    relevant compound-surname shapes:

    * ``A.St.Brown`` and ``Amon-Ra St. Brown`` parse as
      ``first=['a','st'], last='brown'`` and
      ``first=['amon-ra','st'], last='brown'`` — ``St`` stays in the
      first-name list because it sits before the final whitespace-separated
      token, so the resolver tries to align ``('a','st')`` with
      ``('amon','ra','st')`` and fails.
    * ``S. Gilgeous Alexander`` and ``Shai Gilgeous-Alexander`` parse as
      ``first=['s','gilgeous'], last='alexander'`` and
      ``first=['shai'], last='gilgeous-alexander'`` — different surname
      tokens, so the surname compatibility check rejects the pair before
      any first-name comparison runs.

    The wrapper post-processes `_player_name_parts`'s output:

    1. Particle pull. If the LAST token in the first-name list is in
       `_SURNAME_PARTICLES` (``st``, ``van``, ``de``, ``la``, ``le``,
       ``mc``, ...), peel it off the first-name list and prepend it to
       the surname.
    2. Multi-token surname expansion (only when ``compound_surname_hints``
       is supplied). For every plausible tail-length ``n`` (2..len), the
       wrapper checks whether the last ``n`` tokens of
       ``first_tokens + [last_name]`` fold to a hint surname; if so, the
       split is shifted so those ``n`` tokens become the surname. This
       handles ``S. Gilgeous Alexander`` re-parsing as
       ``first=['s'], last='gilgeous alexander'`` when another bucket
       member's parsed surname is the hyphenated ``gilgeous-alexander``
       (which folds to the same hint string).
    3. Hyphen ↔ space fold. Replace any run of whitespace or hyphens in
       the surname with a single space so ``Gilgeous-Alexander`` and
       ``Gilgeous Alexander`` compare equal.

    `_player_name_parts` itself is left untouched, so all other callers
    (event_player_resolver, outcome_normalizer, anything outside the
    contextual resolver) keep their existing behaviour. The original
    surface forms are still used for storage and display.
    """

    parts = _player_name_parts(name)
    if parts is None:
        return None
    first_tokens, last_name = parts
    # Particle pull. Only fires when at least one given-name token would
    # remain — `Van Jefferson` (where `Van` IS the actual first name) must
    # NOT be mis-parsed as `last='van jefferson'` with empty first_tokens.
    # We also iterate while a particle stays at the trailing position so
    # chains like `A. de la Cruz` (parts: `(['a','de','la'], 'cruz')`) get
    # both `la` and `de` pulled, producing `(['a'], 'de la cruz')`.
    while len(first_tokens) > 1 and first_tokens[-1] in _SURNAME_PARTICLES:
        particle = first_tokens[-1]
        first_tokens = first_tokens[:-1]
        last_name = f"{particle} {last_name}".strip()
    # Multi-token surname expansion. Bucket-context hints let `S. Gilgeous
    # Alexander` re-parse as `first=['s'], last='gilgeous alexander'` when
    # another bucket member's hyphenated `gilgeous-alexander` folds to the
    # matching hint string. Only consider tail lengths that leave at least
    # one given-name token — otherwise an unrelated bucket member's hint
    # could consume a normal full name like `John Paul` entirely (when the
    # bucket also contains `Alice John-Paul`), breaking legitimate
    # abbreviation merges from `J. Paul`.
    if compound_surname_hints and len(first_tokens) >= 2:
        full_tokens = first_tokens + [last_name]
        for n in range(len(full_tokens) - 1, 1, -1):
            candidate_surname = " ".join(full_tokens[-n:])
            folded = _fold_surname(candidate_surname)
            if folded in compound_surname_hints:
                first_tokens = full_tokens[:-n]
                last_name = candidate_surname
                break
    last_name = _fold_surname(last_name)
    return first_tokens, last_name


def _player_name_completeness(first_tokens: list[str]) -> int:
    return sum(len(token) for token in first_tokens if len(token) > 1)


def _first_name_letter_sequence(first_tokens: list[str]) -> list[str]:
    """Expand first-name tokens into the comparable given-name parts.

    Hyphenated tokens like ``["karl-anthony"]`` split into ``["karl", "anthony"]`` so
    they line up positionally with multi-initial inputs like ``["k", "a"]``. Multi-token
    inputs (already split by spaces during tokenization) pass through as-is.
    """

    sequence: list[str] = []
    for token in first_tokens:
        for part in token.split("-"):
            stripped = part.strip()
            if stripped:
                sequence.append(stripped)
    return sequence


def _given_name_part_compatible(a: str, b: str) -> bool:
    """One given-name part must be a prefix of the other (or fuzzy-match for typos)."""
    if not a or not b:
        return False
    if a == b:
        return True
    if a.startswith(b) or b.startswith(a):
        return True
    # Typo tolerance only for tokens long enough that fuzzy matching is meaningful;
    # short tokens (initials) must match exactly via the prefix check above.
    if min(len(a), len(b)) >= 3 and a[0] == b[0]:
        return fuzz.ratio(a, b) >= 80
    return False


def _letter_seq_compatible(a: list[str], b: list[str]) -> bool:
    """True iff two letter-sequences plausibly describe the same given-name set.

    Same length: every position must be prefix-compatible. Different length: the
    shorter side must be all single-letter initials so we can treat it as an
    abbreviation of the longer name. This is the post–letter-sequence half of
    `_check_first_name_match`, factored out so it can also be used to decide
    whether two candidate fingerprints describe the same player.
    """

    if not a or not b:
        return False
    if len(a) == len(b):
        return all(_given_name_part_compatible(x, y) for x, y in zip(a, b))
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    if not all(len(part) == 1 for part in shorter):
        return False
    return all(_given_name_part_compatible(s, l) for s, l in zip(shorter, longer))


def _letter_seq_collapse_compatible(
    a: tuple[str, ...],
    a_is_abbrev: bool,
    b: tuple[str, ...],
    b_is_abbrev: bool,
) -> bool:
    """Equivalence test for the candidate-candidate diversity collapse.

    Two candidate fingerprints describe the same player when their given-name
    parts are *prefix-compatible at every shared position*. The same physical
    person can appear at many abbreviation depths within a single event —
    ``Karl-Anthony Towns``, ``K.A. Towns``, ``K. Towns`` — but two players
    with the same surname playing in the same event whose first-name parts
    diverge at any position (``C.J.`` vs ``C.K.``) really are different
    people.

    Rules, applied symmetrically (call order does not matter; the helper
    reorders by length and treats abbreviation flags as advisory):

    * Same length. Every position must be prefix-compatible. Single-letter
      initials count as prefixes of any longer token starting with the same
      letter (``("v",)`` ↔ ``("vj",)``, ``("k","a")`` ↔ ``("karl","anthony")``).
      For multi-character prefix relations (``("jar",)`` vs ``("jared",)``) at
      least one side must carry an explicit abbreviation flag — surface dot or
      single-letter parts — so coincidental prefixes between two full first
      names (``("jo",)`` vs ``("john",)``) stay distinct.

    * Different length. The SHORTER side (by part count) must be all single-
      letter (a structural abbreviation), and every shorter-side position
      must be prefix-compatible with the corresponding position of the longer
      side. This collapses ``("k",)`` ↔ ``("karl","anthony")`` (single
      initial into a hyphenated full name), ``("c","j")`` ↔ ``("c","j","k")``
      (multi-initial extension/contraction), and ``("k",)`` ↔ ``("k","a","j")``
      (single initial into longer abbreviation) — all "same player at a
      different abbreviation depth" within an event. We deliberately do NOT
      gate this on the longer side's abbreviation flag: per the project's
      contextual-resolution intent, an event already gives us strong
      same-player evidence (same teams, same start time, same surname), so
      prefix-compatible first-name shapes always denote the same identity.
      Mismatched-position cases (``("c","j")`` vs ``("c","k")``) still fail
      the same-length check below; fuzzy near-twins (``("jalen",)`` vs
      ``("jaden",)``) still fail because neither side is an abbreviation.

      Note that the rule keys on the SHORTER side being abbreviated. Pairs
      like ``("k","a","j")`` (longer, all-initial) vs ``("karl","anthony")``
      (shorter, full-name) currently return False because the shorter side
      is not all-single-letter — that scenario simply leaves the bookmaker's
      three-initial surface unmerged rather than producing an incorrect
      identity. A separate rival-extension guard inside
      ``_resolve_contextual_player_name_replacements`` further protects the
      contraction direction (``raw=C.J.``, ``best=C.``) when the bucket
      contains a sibling extension that makes ``best`` ambiguous.
    """

    if not a or not b:
        return False
    if len(a) != len(b):
        if len(a) < len(b):
            shorter, longer = a, b
        else:
            shorter, longer = b, a
        if not all(len(part) == 1 for part in shorter):
            return False
        for short_part, long_part in zip(shorter, longer):
            if not short_part or not long_part:
                return False
            if short_part == long_part or long_part.startswith(short_part):
                continue
            return False
        return True
    for x, y in zip(a, b):
        if not x or not y:
            return False
        if x == y:
            continue
        if len(x) == 1 and y.startswith(x):
            continue
        if len(y) == 1 and x.startswith(y):
            continue
        if a_is_abbrev and len(x) < len(y) and y.startswith(x):
            continue
        if b_is_abbrev and len(y) < len(x) and x.startswith(y):
            continue
        return False
    return True


def _collapse_first_name_sequences(
    sequences: set[tuple[tuple[str, ...], bool]],
) -> set[tuple[str, ...]]:
    """Collapse equivalent ``(letter_sequence, is_abbreviation)`` pairs.

    Pairs that pass `_letter_seq_collapse_compatible` are merged, keeping the
    more informative member. The abbreviation flag (carried per fingerprint,
    OR'd across all candidates that share a sequence) lets multi-character
    prefix relations collapse only when at least one side is explicitly an
    abbreviation (surface dot or single-letter parts).
    """

    items = sorted(
        sequences,
        key=lambda item: (sum(len(part) for part in item[0]), len(item[0]), 0 if item[1] else 1),
        reverse=True,
    )
    kept: list[tuple[tuple[str, ...], bool]] = []
    for seq, is_abbrev in items:
        if any(
            _letter_seq_collapse_compatible(seq, is_abbrev, existing_seq, existing_abbrev)
            for existing_seq, existing_abbrev in kept
        ):
            continue
        kept.append((seq, is_abbrev))
    return {seq for seq, _ in kept}


def _name_surface_richness(name: str) -> tuple[int, int, int]:
    stripped = name.strip()
    return (
        sum(1 for ch in stripped if not ch.isascii()),
        stripped.count("-"),
        len(stripped),
    )


def _surface_person_tokens(name: str) -> list[str]:
    tokens = [part.strip() for part in name.split() if part.strip()]
    while tokens and normalize_identity_text(tokens[-1]) in _NAME_SUFFIXES:
        tokens.pop()
    return tokens


def _is_abbreviated_surface_token(token: str) -> bool:
    compact = re.sub(r"[^A-Za-zÀ-ž]+", "", token)
    return bool(compact) and ("." in token or (len(compact) <= 2 and compact.isupper()))


_SURNAME_PARTICLES: frozenset[str] = frozenset(
    {
        "st",
        "mc",
        "mac",
        "de",
        "del",
        "della",
        "van",
        "von",
        "der",
        "den",
        "la",
        "le",
        "du",
        "da",
        "di",
        "do",
        "el",
        "bin",
        "ben",
        "al",
        "abu",
        "san",
        "santa",
    }
)


def _candidate_first_name_has_abbreviation_dot(name: str) -> bool:
    """True iff the first-name portion of ``name``'s surface contains ``.``.

    The first-name portion is the surface minus its trailing surname token.
    Surnames may be glued onto the first-name portion without whitespace
    (``Ja.Butler``, ``K.A.Towns``), so we locate the parsed surname inside the
    surface and check the prefix that precedes it.

    Surface and surname are both diacritic-folded before the search, so names
    like ``Stef. Miljenović`` (parsed surname ``miljenovic``) still match.

    Surname particles like ``St.``, ``Mc.``, ``Van.`` are NOT first-name
    abbreviations — those forms (``St.Brown``) get parsed as
    ``first=st last=brown`` by the simple parser, but the dot is part of the
    composite surname, not a given-name marker. We guard against that here.

    Examples:
      * ``Jar.Butler`` → first-name portion ``Jar.`` → True.
      * ``K.A.Towns`` → first-name portion ``K.A.`` → True.
      * ``P.J. Tucker`` → first-name portion ``P.J. `` → True.
      * ``Stef. Miljenović`` → first-name portion ``Stef. `` → True.
      * ``John Williams`` → first-name portion ``John `` → False.
      * ``St.Brown`` → first-name portion ``St.`` but ``st`` is a surname
        particle → False.
    """

    parts = _player_name_parts(name)
    if not parts:
        return False
    first_tokens, last_name = parts
    if not last_name:
        return False
    if first_tokens and len(first_tokens) == 1 and first_tokens[0] in _SURNAME_PARTICLES:
        return False
    surface_folded = _strip_diacritics(name).lower()
    last_folded = _strip_diacritics(last_name).lower()
    if not surface_folded or not last_folded:
        return False
    idx = surface_folded.rfind(last_folded)
    if idx <= 0:
        return False
    return "." in surface_folded[:idx]


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
    ):
        return False

    return _letter_seq_compatible(
        _first_name_letter_sequence(raw_first_tokens),
        _first_name_letter_sequence(candidate_first_tokens),
    )


@dataclass(frozen=True)
class _ContextualMatch:
    """Result of `_try_contextual_player_match`.

    ``raw_swapped`` records whether the raw name was reversed for the match (e.g.
    surface ended in an abbreviated token like "Edgecombe VJ"). The
    ``candidate_effective_first`` is the candidate's first-name token *as observed
    by the raw* — for candidate-side swaps this is the candidate's parsed last
    name (the abbreviation that became the first name once swapped). The
    ``candidate_effective_first_seq`` is the full letter-sequence form of that
    same effective first name (e.g. ``("karl", "anthony")`` for
    "Karl-Anthony Towns"), used for ambiguity detection across multiple
    candidates.
    """

    raw_swapped: bool
    candidate_swapped: bool
    candidate_effective_first: str
    candidate_effective_first_seq: tuple[str, ...]


def _try_contextual_player_match(
    raw_name: str,
    candidate_name: str,
    *,
    compound_surname_hints: frozenset[str] | None = None,
) -> _ContextualMatch | None:
    # Conservative-then-hint resolution. The simple particle-pull / hyphen-
    # fold parse handles every case where both surfaces already agree on a
    # single-token surname (e.g., ``Mary John Paul`` and ``M.J. Paul`` both
    # parse to ``last='paul'`` with no hint expansion needed). We only fall
    # back to hint-expanded parses when the simple parses leave the surnames
    # mismatched — that's where SGA-class compound surnames live. Without
    # this gating, an unrelated bucket member's hint (e.g., ``Alice John-
    # Paul`` contributing ``'john paul'``) could destructively reparse a
    # bystander's middle-name token (``Mary John Paul`` → ``first=['mary'],
    # last='john paul'``) and break a legitimate abbreviation merge that
    # the simple parse would have caught.
    raw_parts = _resolver_player_parts(raw_name)
    candidate_parts = _resolver_player_parts(candidate_name)
    if not raw_parts or not candidate_parts:
        return None
    if (
        compound_surname_hints
        and raw_parts[1] != candidate_parts[1]
    ):
        raw_parts_with_hints = _resolver_player_parts(
            raw_name, compound_surname_hints=compound_surname_hints
        )
        candidate_parts_with_hints = _resolver_player_parts(
            candidate_name, compound_surname_hints=compound_surname_hints
        )
        if raw_parts_with_hints and candidate_parts_with_hints:
            raw_parts = raw_parts_with_hints
            candidate_parts = candidate_parts_with_hints

    raw_first_tokens, raw_last_name = raw_parts
    candidate_first_tokens, candidate_last_name = candidate_parts
    raw_surface_tokens = _surface_person_tokens(raw_name)
    candidate_surface_tokens = _surface_person_tokens(candidate_name)

    candidate_normal_seq = tuple(_first_name_letter_sequence(candidate_first_tokens))
    candidate_swapped_seq = tuple(_first_name_letter_sequence([candidate_last_name]))

    if _check_first_name_match(raw_first_tokens, candidate_first_tokens, raw_last_name, candidate_last_name):
        return _ContextualMatch(
            raw_swapped=False,
            candidate_swapped=False,
            candidate_effective_first=candidate_first_tokens[0],
            candidate_effective_first_seq=candidate_normal_seq,
        )

    # Reversed raw is only safe when the swapped token looks like an abbreviated first
    # name (e.g. "VJ", "J", "AJ"), not a full given name.
    if raw_surface_tokens and _is_abbreviated_surface_token(raw_surface_tokens[-1]):
        # Two reversed-name shapes are accepted:
        # 1. Single pre-abbreviation token (the existing path):
        #    ``Edgecombe VJ``, ``Towns K.A.``, hyphenated compounds like
        #    ``Gilgeous-Alexander S.``. The single token IS the surname (after
        #    fold to collapse hyphens to spaces).
        # 2. Multi-token pre-abbreviation surname matching a bucket hint:
        #    ``Gilgeous Alexander S.`` / ``Van Jefferson J.`` /
        #    ``Van Der Berg J.``. When another bucket member's parsed surname
        #    folds to the same multi-word string (e.g. ``Shai Gilgeous-
        #    Alexander`` → hint ``gilgeous alexander``), the ``raw_first_tokens``
        #    are the reversed surname.
        reversed_last_options: list[str] = []
        if len(raw_first_tokens) == 1:
            reversed_last_options.append(_fold_surname(raw_first_tokens[0]))
        if compound_surname_hints and len(raw_first_tokens) >= 2:
            joined = _fold_surname(" ".join(raw_first_tokens))
            if joined in compound_surname_hints:
                reversed_last_options.append(joined)
        for reversed_last in reversed_last_options:
            reversed_first = [raw_last_name]
            if _check_first_name_match(
                reversed_first,
                candidate_first_tokens,
                reversed_last,
                candidate_last_name,
            ):
                return _ContextualMatch(
                    raw_swapped=True,
                    candidate_swapped=False,
                    candidate_effective_first=candidate_first_tokens[0],
                    candidate_effective_first_seq=candidate_normal_seq,
                )

    if candidate_surface_tokens and _is_abbreviated_surface_token(candidate_surface_tokens[-1]):
        # Mirror of the raw-swap branch above for the candidate-swap path.
        reversed_last_options = []
        if len(candidate_first_tokens) == 1:
            reversed_last_options.append(_fold_surname(candidate_first_tokens[0]))
        if compound_surname_hints and len(candidate_first_tokens) >= 2:
            joined = _fold_surname(" ".join(candidate_first_tokens))
            if joined in compound_surname_hints:
                reversed_last_options.append(joined)
        for reversed_last in reversed_last_options:
            reversed_first = [candidate_last_name]
            if _check_first_name_match(
                raw_first_tokens,
                reversed_first,
                raw_last_name,
                reversed_last,
            ):
                return _ContextualMatch(
                    raw_swapped=False,
                    candidate_swapped=True,
                    candidate_effective_first=candidate_last_name,
                    candidate_effective_first_seq=candidate_swapped_seq,
                )

    return None


def _is_contextual_player_match(raw_name: str, candidate_name: str) -> bool:
    return _try_contextual_player_match(raw_name, candidate_name) is not None


def _resolve_contextual_player_name_replacements(
    name_counts: Counter[str],
) -> dict[str, str]:
    cleaned_counts: Counter[str] = Counter()
    for name, count in name_counts.items():
        if name and name.strip() and count > 0:
            cleaned_counts[name.strip()] += count
    name_counts = cleaned_counts

    # Pre-pass: merge names that differ only by punctuation, spacing, or diacritics.
    case_replacements: dict[str, str] = {}
    by_compact: dict[str, list[str]] = defaultdict(list)
    for name in name_counts:
        by_compact[_compact_person_name(name)].append(name)
    for compact_key, variants in by_compact.items():
        if not compact_key or len(variants) <= 1:
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
        for variant in variants:
            if variant != best:
                case_replacements[variant] = best
                name_counts[best] = merged_count
                del name_counts[variant]

    replacements: dict[str, str] = dict(case_replacements)

    observed_names = list(name_counts)
    compound_surname_hints = _build_compound_surname_hints(observed_names)
    for raw_name in observed_names:
        raw_parts = _resolver_player_parts(
            raw_name, compound_surname_hints=compound_surname_hints
        )
        if not raw_parts:
            continue

        raw_first_tokens, raw_last_name = raw_parts
        candidate_matches: list[tuple[str, _ContextualMatch]] = []
        for candidate in observed_names:
            if candidate == raw_name:
                continue
            match = _try_contextual_player_match(
                raw_name,
                candidate,
                compound_surname_hints=compound_surname_hints,
            )
            if match is not None:
                candidate_matches.append((candidate, match))
        if not candidate_matches:
            continue

        # Each candidate's *effective* letter-sequence (the given-name positions it
        # contributes from raw's perspective, after any swap) is the diversity key.
        # Reverse-swapped candidates are described by their swap-applied sequence
        # (e.g. "Edgecombe VJ" cand-swap → ("vj",)), so swap-pair variants of the
        # same player collapse together while genuinely-different multi-initial
        # candidates with the same first initial (``("c","j")`` vs ``("c","k")``)
        # remain distinct and trigger the bail below.
        #
        # Each fingerprint also carries an ``is_abbreviation`` flag — True when
        # any candidate contributing that sequence is structurally an
        # abbreviation (single-letter parts, or a `.` in the first-name portion
        # of its surface, or it was matched via candidate-side swap of an
        # abbreviated trailing token). The flag lets the collapse merge
        # ``Jar.``/``Jared`` (one side abbreviated → same player) while keeping
        # ``Jo``/``John`` distinct (neither abbreviated → coincidental prefix).
        seq_abbrev: dict[tuple[str, ...], bool] = {}
        for candidate, match in candidate_matches:
            seq = match.candidate_effective_first_seq
            if not seq:
                continue
            is_abbrev = (
                match.candidate_swapped
                or all(len(part) == 1 for part in seq)
                or _candidate_first_name_has_abbreviation_dot(candidate)
            )
            seq_abbrev[seq] = seq_abbrev.get(seq, False) or is_abbrev
        candidate_first_seqs = _collapse_first_name_sequences(
            {(seq, abbrev) for seq, abbrev in seq_abbrev.items()}
        )
        if len(candidate_first_seqs) > 1:
            continue

        def _candidate_first_for_completeness(candidate: str, match: _ContextualMatch) -> list[str]:
            if match.candidate_swapped:
                return [match.candidate_effective_first]
            cand_parts = _resolver_player_parts(
                candidate, compound_surname_hints=compound_surname_hints
            )
            return cand_parts[0] if cand_parts else []

        def _rank_key(item: tuple[str, _ContextualMatch]) -> tuple[int, int, int, str]:
            candidate, match = item
            return (
                name_counts[candidate],
                _player_name_completeness(_candidate_first_for_completeness(candidate, match)),
                len(candidate.strip()),
                candidate,
            )

        ranked = sorted(candidate_matches, key=_rank_key, reverse=True)
        chosen_replacement: str | None = None
        for best_candidate, best_match in ranked:
            best_parts = _resolver_player_parts(
                best_candidate, compound_surname_hints=compound_surname_hints
            )
            if not best_parts:
                continue

            best_completeness = _player_name_completeness(
                _candidate_first_for_completeness(best_candidate, best_match)
            )
            if best_match.raw_swapped:
                # Raw was reversed to align with candidate; its effective given-name
                # token is its parsed last name (the abbreviated initials that became
                # "first" after the swap).
                raw_first_for_completeness = [raw_last_name]
            else:
                raw_first_for_completeness = raw_first_tokens
            raw_completeness = _player_name_completeness(raw_first_for_completeness)

            # Directional gate: only allow raw → best replacement when the call
            # site has a clear signal that raw is *meant* to be expanded into
            # best, not just coincidentally prefix-compatible. Three accepted
            # signals:
            #
            # 1. ``best_match.raw_swapped`` — the raw label is in reversed
            #    order (e.g. "Edgecombe VJ"); replacement normalises
            #    orientation, the name content is unchanged.
            # 2. raw's effective first sequence is all single-letter initials
            #    ("C.", "K.A.", "VJ-as-initials") — a true abbreviation that
            #    wants to be expanded.
            # 3. raw's surface contains a "." in the first-name portion
            #    ("Aar.", "Jar.") — an explicit abbreviation marker even when
            #    the abbreviated token is more than one letter long.
            #
            # Without one of those signals we still allow the replacement when
            # raw and best are NOT in a prefix relation but raw is decisively
            # out-counted by best — that's the typo-correction case (e.g.
            # "Arron" vs majority-spelled "Aaron"). A prefix relation between
            # two multi-character names ("Jo" / "John", "Steve" / "Steven") is
            # treated as ambiguous and left alone.
            raw_seq = tuple(_first_name_letter_sequence(raw_first_for_completeness))
            best_seq = best_match.candidate_effective_first_seq

            if not best_match.raw_swapped:
                raw_is_abbreviated = (
                    all(len(part) == 1 for part in raw_first_for_completeness)
                    or _candidate_first_name_has_abbreviation_dot(raw_name)
                )
                if not raw_is_abbreviated:
                    if name_counts[best_candidate] <= name_counts[raw_name]:
                        continue
                    first_raw = raw_seq[0] if raw_seq else ""
                    first_best = best_seq[0] if best_seq else ""
                    # Identical first-name tokens are NOT an ambiguous prefix
                    # relation — they're the strongest possible signal that
                    # the two surface forms refer to the same person (the
                    # rest of the difference lives in the surname or in a
                    # Jr/Sr/II/III suffix that ``_player_name_parts`` already
                    # strips). Only a TRUE prefix relation between DIFFERENT
                    # tokens (e.g. "Jo"/"John", "Steve"/"Steven") should bail
                    # out here.
                    if (
                        first_raw
                        and first_best
                        and first_raw != first_best
                        and (
                            first_raw.startswith(first_best)
                            or first_best.startswith(first_raw)
                        )
                    ):
                        continue

            # Rival-extension guard. The relaxation lets ``raw`` contract into
            # a strictly-shorter all-single-letter ``best`` (e.g.
            # ``C.J. → C.``) when ``best`` is the only candidate ``raw``
            # sees. The diversity guard upstream only inspects ``raw``'s own
            # candidate set, so a sibling extension that diverges from ``raw``
            # at a later position (``C.K.``) never appears in ``raw``'s
            # candidate list — its first-name letter-sequence fails the same-
            # length per-position prefix check vs ``raw_seq``. Without this
            # guard, a bucket of ``{C.(5), C.J.(1), C.K.(1)}`` would silently
            # merge BOTH ``C.J.`` and ``C.K.`` into ``C.`` even though the
            # bucket itself testifies that ``C.`` is ambiguous between two
            # different players.
            #
            # When ``best`` is a strict-shorter contraction of ``raw`` and
            # ``best_seq`` is all single-letter, scan ``observed_names`` for
            # any other surface that
            # (a) shares the surname with ``best`` or ``raw``,
            # (b) is itself a strict structural extension of ``best_seq``
            #     (longer by part count, prefix-compatible at every
            #     ``best_seq`` position), and
            # (c) is NOT prefix-compatible with ``raw_seq`` via
            #     ``_letter_seq_collapse_compatible`` (i.e., the rival could
            #     be the source of ``best``'s contraction but is a different
            #     identity from ``raw``).
            # If such a rival exists, ``best`` is ambiguous and we fall
            # through to the next ranked candidate. An earlier revision of
            # this guard ``continue``d the whole raw on ambiguity, but that
            # lost legitimate merges in mixed buckets like
            # ``{C.(5), C.J.(1), Cameron John(1), C.K.(1)}`` where ``C.`` is
            # ambiguous yet ``Cameron John`` is unambiguous and would safely
            # absorb ``C.J.``. The fall-through preserves those merges.
            if (
                best_seq
                and raw_seq
                and len(best_seq) < len(raw_seq)
                and all(len(part) == 1 for part in best_seq)
            ):
                best_parts_for_rival = _resolver_player_parts(
                    best_candidate, compound_surname_hints=compound_surname_hints
                )
                best_last_for_rival = (
                    best_parts_for_rival[1] if best_parts_for_rival else ""
                )
                raw_is_abbrev_for_rival = (
                    all(len(part) == 1 for part in raw_seq)
                    or _candidate_first_name_has_abbreviation_dot(raw_name)
                )
                rival_found = False
                for other_name in observed_names:
                    if other_name == raw_name or other_name == best_candidate:
                        continue
                    other_parts = _resolver_player_parts(
                        other_name, compound_surname_hints=compound_surname_hints
                    )
                    if not other_parts:
                        continue
                    other_first_tokens, other_last = other_parts
                    if (
                        other_last != raw_last_name
                        and other_last != best_last_for_rival
                    ):
                        continue
                    other_seq = tuple(
                        _first_name_letter_sequence(other_first_tokens)
                    )
                    # Skip empty sequences, exact duplicates of ``best_seq``
                    # (those are surface variants of ``best`` itself, not
                    # rivals), and any sequence STRICTLY SHORTER than
                    # ``best_seq``. Same-length rivals are admitted: a
                    # single-token full name like ``("carl",)`` is exactly
                    # the same kind of bucket-internal evidence as a
                    # multi-initial extension like ``("c","j")`` — both make
                    # ``best=C.`` ambiguous between two distinct identities.
                    # Earlier revisions used ``len(other_seq) <= len(best_seq)``
                    # which silently admitted the
                    # ``{C.(5), C.J.(1), Carl(1)}`` over-merge.
                    if (
                        not other_seq
                        or other_seq == best_seq
                        or len(other_seq) < len(best_seq)
                    ):
                        continue
                    if not all(
                        other_seq[i] and other_seq[i].startswith(best_seq[i])
                        for i in range(len(best_seq))
                    ):
                        continue
                    other_is_abbrev = (
                        all(len(part) == 1 for part in other_seq)
                        or _candidate_first_name_has_abbreviation_dot(other_name)
                    )
                    if _letter_seq_collapse_compatible(
                        other_seq,
                        other_is_abbrev,
                        raw_seq,
                        raw_is_abbrev_for_rival,
                    ):
                        continue
                    rival_found = True
                    break
                if rival_found:
                    continue

            if best_completeness < raw_completeness:
                continue
            if (
                best_completeness == raw_completeness
                and name_counts[best_candidate] <= name_counts[raw_name]
            ):
                # Tie-break: when raw was swapped to match best, prefer the
                # un-swapped candidate even at equal counts so reverse-name
                # pairs converge to the natural orientation. Otherwise stay
                # conservative and don't replace.
                if not best_match.raw_swapped:
                    continue

            chosen_replacement = best_candidate
            break

        if chosen_replacement is not None:
            replacements[raw_name] = chosen_replacement

    return replacements


def _final_contextual_player_name(
    player_name: str,
    replacements: dict[str, str],
) -> str:
    replacement = replacements.get(player_name)
    seen_replacements: set[str] = set()
    while replacement and replacement not in seen_replacements:
        seen_replacements.add(replacement)
        next_replacement = replacements.get(replacement)
        if not next_replacement:
            break
        replacement = next_replacement
    return replacement or player_name


def resolve_contextual_player_name_variants(names: list[str]) -> dict[str, str]:
    """Resolve equivalent player labels within one event/match context.

    Returns a variant→display-name map without introducing any global player identity.
    Ambiguous initials stay mapped to their original label.
    """

    original_names = [name.strip() for name in names if name and name.strip()]
    replacements = _resolve_contextual_player_name_replacements(Counter(original_names))
    return {
        name: _final_contextual_player_name(name, replacements)
        for name in dict.fromkeys(original_names)
    }


def _resolve_contextual_player_names(raw_list: list[RawOddsData]) -> list[RawOddsData]:
    names_by_match: dict[str, Counter[str]] = defaultdict(Counter)

    for raw in raw_list:
        if not raw.player_name:
            continue

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
        if (
            raw.start_time is None
            or home_resolution.team_id is None
            or away_resolution.team_id is None
        ):
            continue
        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        names_by_match[match_id][raw.player_name.strip()] += 1

    replacements_by_match: dict[str, dict[str, str]] = {}
    for match_id, name_counts in names_by_match.items():
        replacements_by_match[match_id] = _resolve_contextual_player_name_replacements(
            name_counts
        )

    resolved: list[RawOddsData] = []
    for raw in raw_list:
        if not raw.player_name:
            resolved.append(raw)
            continue

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
        if (
            raw.start_time is None
            or home_resolution.team_id is None
            or away_resolution.team_id is None
        ):
            resolved.append(raw)
            continue
        match_id = generate_match_id(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        replacement = _final_contextual_player_name(
            raw.player_name.strip(),
            replacements_by_match.get(match_id, {}),
        )
        if replacement == raw.player_name.strip():
            replacement = None
        if not replacement:
            resolved.append(raw)
            continue

        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=raw.home_team,
                away_team=raw.away_team,
                source_url=raw.source_url,
                market_type=raw.market_type,
                player_name=replacement,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved


def normalize_league_id(raw_league_id: str, bookmaker_id: str | None = None) -> str:
    return resolve_league(raw_league_id, bookmaker_id=bookmaker_id).league_id


def _event_identity_slot(
    start_time: str | None,
    sport: str,
) -> tuple[str, str]:
    if not start_time:
        raise ValueError("Exact kickoff time is required for event matching")
    return (sport, start_time)


def _display_event_slot_time(slot: tuple[str, str]) -> str:
    return slot[1]


def generate_match_id(
    home_team: int | str,
    away_team: int | str,
    start_time: str | None,
    sport: str = DEFAULT_SPORT,
) -> str:
    raw = f"{sport}:{_event_identity_slot(start_time, sport)[1]}:{home_team}:{away_team}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class _EventSlotResolution:
    sport: str
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    league_id: str
    support_count: int
    confidence: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _TeamReviewCandidate:
    team_id: int
    team_name: str
    score: float
    matched_alias: str | None = None
    slot_support: int | None = None
    canonical_home_team: str | None = None
    canonical_away_team: str | None = None


@dataclass(frozen=True)
class _TeamReviewSlotCandidate:
    team_id: int
    team_name: str
    counterpart_team: str
    canonical_home_team: str
    canonical_away_team: str
    slot_support: int


@dataclass(frozen=True)
class _CanonicalMatchup:
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str


def _event_slot_key(
    home_team_id: int,
    away_team_id: int,
    start_time: str | None,
    sport: str,
) -> tuple[tuple[str, str], tuple[int, int]]:
    return (
        _event_identity_slot(start_time, sport),
        tuple(sorted((home_team_id, away_team_id))),
    )


def _slot_orientation_key(
    home_team_id: int,
    away_team_id: int,
) -> tuple[int, int]:
    return (home_team_id, away_team_id)


def _choose_majority_value(counter: Counter[object]) -> object:
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


def _build_event_slot_resolutions(
    raw_list: list[RawOddsData],
) -> dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution]:
    orientation_counts: dict[
        tuple[tuple[str, str], tuple[int, int]],
        Counter[tuple[int, int]],
    ] = defaultdict(Counter)
    league_counts: dict[tuple[tuple[str, str], tuple[int, int]], Counter[str]] = defaultdict(Counter)
    bookmaker_counts: dict[tuple[tuple[str, str], tuple[int, int]], set[str]] = defaultdict(set)
    team_names: dict[int, str] = {}
    display_names: dict[str, str] = {}

    for raw in raw_list:
        if raw.start_time is None:
            continue
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
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
        if (
            home_resolution.team_id is None
            or away_resolution.team_id is None
            or home_resolution.team_id == away_resolution.team_id
        ):
            continue

        slot = _event_slot_key(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        orientation_counts[slot][
            _slot_orientation_key(home_resolution.team_id, away_resolution.team_id)
        ] += 1
        league_counts[slot][direct_league.league_id] += 1
        bookmaker_counts[slot].add(raw.bookmaker_id)
        team_names[home_resolution.team_id] = home_resolution.team_name
        team_names[away_resolution.team_id] = away_resolution.team_name
        display_names[direct_league.league_id] = direct_league.display_name

    resolutions: dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution] = {}
    for slot, orientations in orientation_counts.items():
        chosen_home_id, chosen_away_id = _choose_majority_value(orientations)
        slot_league_counts = league_counts[slot]
        chosen_league = _choose_majority_value(slot_league_counts)
        confidence = "high" if len(orientations) == 1 else "medium"
        if len(slot_league_counts) > 1 and confidence == "high":
            confidence = "medium"

        league_evidence = ", ".join(
            f"{display_names.get(league_id, league_id)} x{count}"
            for league_id, count in sorted(
                slot_league_counts.items(),
                key=lambda item: (-item[1], display_names.get(item[0], item[0])),
            )
        )
        resolutions[slot] = _EventSlotResolution(
            sport=slot[0][0],
            home_team_id=chosen_home_id,
            away_team_id=chosen_away_id,
            home_team=team_names[chosen_home_id],
            away_team=team_names[chosen_away_id],
            league_id=chosen_league,
            support_count=len(bookmaker_counts[slot]),
            confidence=confidence,
            evidence=(
                f"Sport: {slot[0][0]}",
                f"Exact start time: {_display_event_slot_time(slot[0])}",
                f"Canonical event: {team_names[chosen_home_id]} vs {team_names[chosen_away_id]}",
                f"League votes: {league_evidence}",
            ),
        )
    return resolutions


def _team_candidate_score(raw_team_name: str, candidate_team_name: str) -> float:
    raw_key = _normalize_team_key(raw_team_name)
    candidate_key = _normalize_team_key(candidate_team_name)
    if not raw_key or not candidate_key:
        return 0.0
    return float(
        max(
            fuzz.token_set_ratio(raw_key, candidate_key),
            fuzz.partial_ratio(raw_key, candidate_key),
        )
    )


def _rank_team_review_candidates(
    raw_team_name: str,
    candidate_teams: list[tuple[int, str]],
    *,
    threshold: float = TEAM_REVIEW_CANDIDATE_THRESHOLD,
) -> list[_TeamReviewCandidate]:
    raw_key = _normalize_team_key(raw_team_name)
    ranked: list[_TeamReviewCandidate] = []
    seen_team_ids: set[int] = set()

    for team_id, candidate_team in candidate_teams:
        candidate_key = _normalize_team_key(candidate_team)
        if (
            not candidate_key
            or candidate_key == raw_key
            or team_id in seen_team_ids
        ):
            continue
        seen_team_ids.add(team_id)
        score = _team_candidate_score(raw_team_name, candidate_team)
        if score < threshold:
            continue
        ranked.append(_TeamReviewCandidate(team_id, candidate_team, score))

    return sorted(ranked, key=lambda item: (-item.score, item.team_name))[
        :TEAM_REVIEW_MAX_CANDIDATES
    ]


def _rank_slot_team_review_candidates(
    raw_team_name: str,
    candidate_teams: list[_TeamReviewSlotCandidate],
    *,
    threshold: float = 0.0,
) -> list[_TeamReviewCandidate]:
    raw_key = _normalize_team_key(raw_team_name)
    ranked: list[_TeamReviewCandidate] = []
    seen_team_ids: set[int] = set()

    for candidate in candidate_teams:
        candidate_key = _normalize_team_key(candidate.team_name)
        if (
            not candidate_key
            or candidate_key == raw_key
            or candidate.team_id in seen_team_ids
        ):
            continue
        seen_team_ids.add(candidate.team_id)
        score = _team_candidate_score(raw_team_name, candidate.team_name)
        if score < threshold:
            continue
        ranked.append(
            _TeamReviewCandidate(
                team_id=candidate.team_id,
                team_name=candidate.team_name,
                score=score,
                slot_support=candidate.slot_support,
                canonical_home_team=candidate.canonical_home_team,
                canonical_away_team=candidate.canonical_away_team,
            )
        )

    return _sort_team_review_candidates(ranked)


def _sort_team_review_candidates(
    candidates: list[_TeamReviewCandidate],
) -> list[_TeamReviewCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            -item.score,
            -(item.slot_support or 0),
            item.team_id,
            item.team_name,
        ),
    )[:TEAM_REVIEW_MAX_CANDIDATES]


def _merge_review_candidates_with_current_team(
    ranked_candidates: list[_TeamReviewCandidate],
    *,
    current_team_id: int,
    current_team_name: str,
    current_slot: _EventSlotResolution,
) -> list[_TeamReviewCandidate]:
    if not ranked_candidates:
        return []

    current_candidate = _TeamReviewCandidate(
        team_id=current_team_id,
        team_name=current_team_name,
        score=ranked_candidates[0].score,
        slot_support=current_slot.support_count,
        canonical_home_team=current_slot.home_team,
        canonical_away_team=current_slot.away_team,
    )
    extra_candidates = ranked_candidates[1 : TEAM_REVIEW_MAX_CANDIDATES - 1]
    return _sort_team_review_candidates(
        [ranked_candidates[0], current_candidate, *extra_candidates]
    )


def _team_review_slot_candidates(
    slot_resolutions: list[_EventSlotResolution],
    *,
    counterpart_team_id: int,
    raw_team_name: str,
) -> dict[int, _TeamReviewSlotCandidate]:
    candidates: dict[int, _TeamReviewSlotCandidate] = {}
    raw_key = _normalize_team_key(raw_team_name)

    for resolution in slot_resolutions:
        if counterpart_team_id not in {
            resolution.home_team_id,
            resolution.away_team_id,
        }:
            continue

        candidate_team_id = (
            resolution.away_team_id
            if resolution.home_team_id == counterpart_team_id
            else resolution.home_team_id
        )
        candidate_team = (
            resolution.away_team
            if resolution.home_team_id == counterpart_team_id
            else resolution.home_team
        )
        candidate_key = _normalize_team_key(candidate_team)
        if not candidate_key or candidate_key == raw_key:
            continue
        candidates.setdefault(
            candidate_team_id,
            _TeamReviewSlotCandidate(
                team_id=candidate_team_id,
                team_name=candidate_team,
                counterpart_team=(
                    resolution.home_team
                    if resolution.home_team_id == counterpart_team_id
                    else resolution.away_team
                ),
                canonical_home_team=resolution.home_team,
                canonical_away_team=resolution.away_team,
                slot_support=resolution.support_count,
            ),
        )

    return candidates


def _to_review_candidates(
    candidates: list[_TeamReviewCandidate],
) -> list[TeamReviewCandidate]:
    return [
        TeamReviewCandidate(
            team_id=candidate.team_id,
            team_name=candidate.team_name,
            score=candidate.score,
            matched_alias=candidate.matched_alias,
            slot_support=candidate.slot_support,
            canonical_home_team=candidate.canonical_home_team,
            canonical_away_team=candidate.canonical_away_team,
        )
        for candidate in candidates
    ]


def _search_global_review_candidates(
    raw_team_name: str,
    *,
    sport: str,
) -> list[_TeamReviewCandidate]:
    return [
        _TeamReviewCandidate(
            team_id=candidate.team_id,
            team_name=candidate.team_name,
            score=float(candidate.score),
            matched_alias=candidate.matched_alias,
        )
        for candidate in search_canonical_team_candidates(
            raw_team_name,
            sport=sport,
            limit=TEAM_REVIEW_MAX_CANDIDATES,
        )
    ]


def _build_team_review_cases(
    raw_list: list[RawOddsData],
    slot_resolutions: dict[tuple[tuple[str, str], tuple[int, int]], _EventSlotResolution],
) -> list[TeamReviewDiagnostic]:
    slots_by_start_time: dict[tuple[str, str], list[_EventSlotResolution]] = defaultdict(list)
    for (slot_time, _), resolution in slot_resolutions.items():
        slots_by_start_time[slot_time].append(resolution)

    review_cases: dict[tuple[str, str, str, str, str], TeamReviewDiagnostic] = {}

    for include_resolved in (False, True):
        for raw in raw_list:
            direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
            if raw.start_time is None:
                continue

            candidate_slots = slots_by_start_time.get((raw.sport, raw.start_time), [])

            team_inputs = (raw.home_team, raw.away_team)
            team_resolutions = [
                resolve_team_name(
                    team_name,
                    bookmaker_id=raw.bookmaker_id,
                    sport=raw.sport,
                )
                for team_name in team_inputs
            ]

            for team_index, raw_team_name in enumerate(team_inputs):
                team_resolution = team_resolutions[team_index]
                if include_resolved != (team_resolution.team_id is not None):
                    continue

                counterpart_resolution = team_resolutions[1 - team_index]
                matched_counterpart_team = (
                    counterpart_resolution.team_name
                    if counterpart_resolution.team_id is not None
                    else None
                )
                ranked_candidates: list[_TeamReviewCandidate] = []
                review_kind = "candidate_search"
                confidence = "low"
                evidence = [f"Exact start time: {raw.start_time}"]
                canonical_home_team: str | None = None
                canonical_away_team: str | None = None

                if team_resolution.team_id is not None:
                    if counterpart_resolution.team_id is None:
                        continue

                    slot_candidates = _team_review_slot_candidates(
                        candidate_slots,
                        counterpart_team_id=counterpart_resolution.team_id,
                        raw_team_name=raw_team_name,
                    )
                    current_slot = slot_resolutions.get(
                        _event_slot_key(
                            team_resolution.team_id,
                            counterpart_resolution.team_id,
                            raw.start_time,
                            raw.sport,
                        )
                    )
                    if not slot_candidates or current_slot is None:
                        continue

                    ranked_candidates = _rank_slot_team_review_candidates(
                        raw_team_name,
                        list(slot_candidates.values()),
                        threshold=0.0,
                    )
                    if not ranked_candidates:
                        continue

                    suggested_slot_candidate = slot_candidates.get(ranked_candidates[0].team_id)
                    if (
                        suggested_slot_candidate is None
                        or suggested_slot_candidate.slot_support <= current_slot.support_count
                    ):
                        continue

                    ranked_candidates = _merge_review_candidates_with_current_team(
                        ranked_candidates,
                        current_team_id=team_resolution.team_id,
                        current_team_name=team_resolution.team_name,
                        current_slot=current_slot,
                    )
                    suggested_slot_candidate = ranked_candidates[0]
                    review_kind = (
                        "alias_suggestion"
                        if len(slot_candidates) == 1
                        else "candidate_search"
                    )
                    confidence = "high" if len(slot_candidates) == 1 else "medium"
                    canonical_home_team = suggested_slot_candidate.canonical_home_team
                    canonical_away_team = suggested_slot_candidate.canonical_away_team
                    evidence.extend(
                        [
                            f"Matched other team: {counterpart_resolution.team_name}",
                            (
                                "Current canonical event: "
                                f"{current_slot.home_team} vs {current_slot.away_team} "
                                f"(support x{current_slot.support_count})"
                            ),
                            (
                                "Stronger competing canonical event: "
                                f"{suggested_slot_candidate.canonical_home_team} vs "
                                f"{suggested_slot_candidate.canonical_away_team} "
                                f"(support x{suggested_slot_candidate.slot_support})"
                            ),
                        ]
                    )
                else:
                    if counterpart_resolution.team_id is not None:
                        slot_candidates = _team_review_slot_candidates(
                            candidate_slots,
                            counterpart_team_id=counterpart_resolution.team_id,
                            raw_team_name=raw_team_name,
                        )
                        if len(slot_candidates) == 1:
                            slot_candidate = next(iter(slot_candidates.values()))
                            ranked_candidates = _rank_slot_team_review_candidates(
                                raw_team_name,
                                list(slot_candidates.values()),
                            )
                            suggested_slot_candidate = ranked_candidates[0]
                            review_kind = "alias_suggestion"
                            confidence = "high"
                            canonical_home_team = suggested_slot_candidate.canonical_home_team
                            canonical_away_team = suggested_slot_candidate.canonical_away_team
                            evidence.extend(
                                [
                                    f"Matched other team: {slot_candidate.counterpart_team}",
                                    f"Canonical event: {slot_candidate.canonical_home_team} vs {slot_candidate.canonical_away_team}",
                                    "Unique canonical event found at the same sport and kickoff",
                                ]
                            )
                        elif slot_candidates:
                            ranked_candidates = _rank_slot_team_review_candidates(
                                raw_team_name,
                                list(slot_candidates.values()),
                                threshold=0.0,
                            )
                            review_kind = "candidate_search"
                            confidence = "medium"
                            evidence.extend(
                                [
                                    f"Matched other team: {counterpart_resolution.team_name}",
                                    "Multiple canonical events share that team at this exact kickoff",
                                ]
                            )

                    if not ranked_candidates:
                        ranked_candidates = _search_global_review_candidates(
                            raw_team_name,
                            sport=raw.sport,
                        )
                        if ranked_candidates:
                            confidence = (
                                "medium"
                                if ranked_candidates[0].score >= TEAM_REVIEW_CANDIDATE_THRESHOLD
                                else "low"
                            )
                            evidence.append(
                                "Top fuzzy matches across canonical teams in this sport"
                            )
                        else:
                            evidence.append(
                                "No canonical team matched this label in the current database"
                            )

                if not ranked_candidates and team_resolution.team_id is not None:
                    continue
                suggested_candidate = ranked_candidates[0] if ranked_candidates else None

                if (
                    team_resolution.team_id is None
                    and canonical_home_team is None
                    and suggested_candidate is not None
                ):
                    canonical_home_team = suggested_candidate.canonical_home_team
                    canonical_away_team = suggested_candidate.canonical_away_team

                if team_resolution.team_id is not None and any(
                    existing_case.sport == raw.sport
                    and existing_case.start_time == raw.start_time
                    and existing_case.matched_counterpart_team == matched_counterpart_team
                    and existing_case.suggested_team_id == suggested_candidate.team_id
                    and any(
                        candidate.team_id == team_resolution.team_id
                        for candidate in existing_case.candidate_teams
                    )
                    for existing_case in review_cases.values()
                ):
                    continue

                review_key = (
                    raw.bookmaker_id,
                    raw.sport,
                    normalize_identity_text(raw_team_name),
                    raw.start_time,
                    matched_counterpart_team or "",
                )
                if review_key in review_cases:
                    continue

                review_cases[review_key] = TeamReviewDiagnostic(
                    bookmaker_id=raw.bookmaker_id,
                    raw_league_id=raw.league_id,
                    normalized_raw_league_id=normalize_identity_text(raw.league_id),
                    sport=raw.sport,
                    scope_league_id=direct_league.league_id,
                    raw_team_name=raw_team_name,
                    normalized_raw_team_name=team_resolution.team_name,
                    suggested_team_id=(
                        suggested_candidate.team_id
                        if suggested_candidate is not None
                        else None
                    ),
                    suggested_team_name=(
                        suggested_candidate.team_name
                        if suggested_candidate is not None
                        else None
                    ),
                    start_time=raw.start_time,
                    review_kind=review_kind,
                    reason_code=(
                        "candidate_team_match_same_start_time"
                        if matched_counterpart_team
                        else "candidate_team_search"
                    ),
                    confidence=confidence,
                    similarity_score=(
                        suggested_candidate.score
                        if suggested_candidate is not None
                        else None
                    ),
                    candidate_teams=_to_review_candidates(ranked_candidates),
                    matched_counterpart_team=matched_counterpart_team,
                    canonical_home_team=canonical_home_team,
                    canonical_away_team=canonical_away_team,
                    evidence=evidence,
                )

    return list(review_cases.values())


def normalize_market_type(raw_type: str) -> str:
    key = raw_type.strip().lower().replace("&", "+").replace("+", " + ")
    key = " ".join(key.split())
    return _MARKET_TYPE_MAPPING.get(key, key)


def _reoriented_market_values(
    raw: RawOddsData,
    *,
    raw_home_team_id: int,
    raw_away_team_id: int,
    target_home_team_id: int,
    target_away_team_id: int,
) -> tuple[float, float | None, float | None]:
    threshold = raw.threshold
    over_odds = raw.over_odds
    under_odds = raw.under_odds

    if (
        raw_home_team_id == target_away_team_id
        and raw_away_team_id == target_home_team_id
        and normalize_market_type(raw.market_type) == "home_handicap_ot"
    ):
        return -threshold, under_odds, over_odds

    return threshold, over_odds, under_odds


def _raw_with_target_matchup(
    raw: RawOddsData,
    *,
    raw_home_team_id: int,
    raw_away_team_id: int,
    target_home_team_id: int,
    target_away_team_id: int,
    target_home_team: str,
    target_away_team: str,
) -> RawOddsData:
    threshold, over_odds, under_odds = _reoriented_market_values(
        raw,
        raw_home_team_id=raw_home_team_id,
        raw_away_team_id=raw_away_team_id,
        target_home_team_id=target_home_team_id,
        target_away_team_id=target_away_team_id,
    )
    return RawOddsData(
        bookmaker_id=raw.bookmaker_id,
        league_id=raw.league_id,
        sport=raw.sport,
        home_team=target_home_team,
        away_team=target_away_team,
        source_url=raw.source_url,
        market_type=raw.market_type,
        player_name=raw.player_name,
        threshold=threshold,
        over_odds=over_odds,
        under_odds=under_odds,
        start_time=raw.start_time,
    )


def _is_unresolved_shared_platform_prop(raw: RawOddsData) -> bool:
    return bool(raw.player_name and raw.away_team.strip() == raw.player_name.strip())


def _format_matchup(matchup: tuple[str, str]) -> str:
    if isinstance(matchup, _CanonicalMatchup):
        return f"{matchup.home_team} vs {matchup.away_team}"
    return f"{matchup[0]} vs {matchup[1]}"


def _separate_missing_start_times(
    raw_list: list[RawOddsData],
) -> tuple[list[RawOddsData], list[UnresolvedOddsDiagnostic]]:
    timed_rows: list[RawOddsData] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []

    for raw in raw_list:
        if raw.start_time:
            timed_rows.append(raw)
            continue
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        unresolved.append(
            UnresolvedOddsDiagnostic(
                bookmaker_id=raw.bookmaker_id,
                raw_league_id=raw.league_id,
                league_id=direct_league.league_id,
                sport=raw.sport,
                market_type=raw.market_type,
                player_name=raw.player_name,
                raw_team_name=f"{raw.home_team} vs {raw.away_team}",
                normalized_team_name=f"{raw.home_team.strip()} vs {raw.away_team.strip()}",
                start_time=None,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                reason_code="missing_start_time",
                candidate_count=0,
                candidate_matchups=[],
                available_matchups_same_slot=[],
            )
        )

    return timed_rows, unresolved


def _autocreate_exact_match_teams(raw_list: list[RawOddsData]) -> None:
    matchup_counts: Counter[tuple[str, str, tuple[str, str]]] = Counter()
    team_display_names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    anchored_unresolved_team_keys: dict[tuple[str, str, tuple[str, str]], set[str]] = defaultdict(set)
    anchored_candidates: list[tuple[tuple[str, str, tuple[str, str]], tuple[str, str], int, str]] = []
    known_team_ids_by_slot: dict[tuple[str, str], set[int]] = defaultdict(set)

    for raw in raw_list:
        if _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue
        home_key = normalize_identity_text(raw.home_team)
        away_key = normalize_identity_text(raw.away_team)
        if not home_key or not away_key or home_key == away_key:
            continue
        pair_key = (raw.sport, raw.start_time, tuple(sorted((home_key, away_key))))
        matchup_counts[pair_key] += 1
        team_display_names[(raw.sport, home_key)][raw.home_team.strip()] += 1
        team_display_names[(raw.sport, away_key)][raw.away_team.strip()] += 1
        slot_key = (raw.sport, raw.start_time)

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
        if home_resolution.team_id is not None and away_resolution.team_id is not None:
            known_team_ids_by_slot[slot_key].update(
                {home_resolution.team_id, away_resolution.team_id}
            )
        elif home_resolution.team_id is not None and away_resolution.team_id is None:
            anchored_candidates.append(
                (pair_key, slot_key, home_resolution.team_id, away_key)
            )
        elif away_resolution.team_id is not None and home_resolution.team_id is None:
            anchored_candidates.append(
                (pair_key, slot_key, away_resolution.team_id, home_key)
            )

    for pair_key, slot_key, known_team_id, unresolved_team_key in anchored_candidates:
        if known_team_id in known_team_ids_by_slot.get(slot_key, set()):
            anchored_unresolved_team_keys[pair_key].add(unresolved_team_key)

    for sport, _start_time, pair_keys in matchup_counts:
        if matchup_counts[(sport, _start_time, pair_keys)] < 2:
            continue
        for team_key in pair_keys:
            if team_key in anchored_unresolved_team_keys.get(
                (sport, _start_time, pair_keys),
                set(),
            ):
                continue
            display_counter = team_display_names.get((sport, team_key), Counter())
            if not display_counter:
                continue
            display_name = max(
                display_counter.items(),
                key=lambda item: (item[1], len(item[0]), item[0]),
            )[0]
            if resolve_team_name(display_name, sport=sport).team_id is None:
                create_canonical_team(display_name=display_name, sport=sport)


def _build_canonical_matchups(
    raw_list: list[RawOddsData],
) -> dict[tuple[tuple[str, str], tuple[int, int]], _CanonicalMatchup]:
    counts: dict[
        tuple[tuple[str, str], tuple[int, int]],
        dict[tuple[int, int], int],
    ] = {}
    team_names: dict[int, str] = {}

    for raw in raw_list:
        if _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue

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
        if (
            home_resolution.team_id is None
            or away_resolution.team_id is None
            or home_resolution.team_id == away_resolution.team_id
        ):
            continue

        slot = _event_slot_key(
            home_resolution.team_id,
            away_resolution.team_id,
            raw.start_time,
            raw.sport,
        )
        orientation = (home_resolution.team_id, away_resolution.team_id)
        counts.setdefault(slot, {})[orientation] = counts.setdefault(slot, {}).get(
            orientation,
            0,
        ) + 1
        team_names[home_resolution.team_id] = home_resolution.team_name
        team_names[away_resolution.team_id] = away_resolution.team_name

    canonical: dict[tuple[tuple[str, str], tuple[int, int]], _CanonicalMatchup] = {}
    for slot, orientations in counts.items():
        chosen_home_id, chosen_away_id = min(
            orientations.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        canonical[slot] = _CanonicalMatchup(
            home_team_id=chosen_home_id,
            away_team_id=chosen_away_id,
            home_team=team_names[chosen_home_id],
            away_team=team_names[chosen_away_id],
        )
    return canonical


def _build_inferred_shared_platform_matchups(
    raw_list: list[RawOddsData],
    matchups_by_slot: dict[tuple[str, str], list[_CanonicalMatchup]],
) -> dict[tuple[str, str], list[_CanonicalMatchup]]:
    teams_by_slot: dict[tuple[str, str], dict[int, str]] = defaultdict(dict)

    for raw in raw_list:
        if not _is_unresolved_shared_platform_prop(raw) or raw.start_time is None:
            continue

        known_team = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if known_team.team_id is None:
            continue
        slot = _event_identity_slot(raw.start_time, raw.sport)
        existing_matchups = matchups_by_slot.get(slot, [])
        if any(
            known_team.team_id in {matchup.home_team_id, matchup.away_team_id}
            for matchup in existing_matchups
        ):
            continue
        teams_by_slot[slot][known_team.team_id] = known_team.team_name

    inferred: dict[tuple[str, str], list[_CanonicalMatchup]] = defaultdict(list)
    for slot, teams in teams_by_slot.items():
        if len(teams) != 2:
            continue
        ordered = sorted(teams.items(), key=lambda item: item[1])
        inferred[slot].append(
            _CanonicalMatchup(
                home_team_id=ordered[0][0],
                away_team_id=ordered[1][0],
                home_team=ordered[0][1],
                away_team=ordered[1][1],
            )
        )

    return dict(inferred)


def _resolve_shared_platform_matchups(
    raw_list: list[RawOddsData],
) -> tuple[list[RawOddsData], list[UnresolvedOddsDiagnostic]]:
    canonical_matchups = _build_canonical_matchups(raw_list)
    matchups_by_slot: dict[tuple[str, str], list[_CanonicalMatchup]] = {}
    for (slot, _matchup_key), matchup in canonical_matchups.items():
        matchups_by_slot.setdefault(slot, []).append(matchup)
    for slot, inferred_matchups in _build_inferred_shared_platform_matchups(
        raw_list, matchups_by_slot
    ).items():
        matchups_by_slot.setdefault(slot, []).extend(inferred_matchups)

    resolved: list[RawOddsData] = []
    unresolved: list[UnresolvedOddsDiagnostic] = []

    for raw in raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)

        if not _is_unresolved_shared_platform_prop(raw):
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
            resolved.append(raw)
            canonical = None
            if (
                raw.start_time is not None
                and home_resolution.team_id is not None
                and away_resolution.team_id is not None
            ):
                canonical = canonical_matchups.get(
                    _event_slot_key(
                        home_resolution.team_id,
                        away_resolution.team_id,
                        raw.start_time,
                        raw.sport,
                    )
                )
            if canonical:
                resolved[-1] = _raw_with_target_matchup(
                    raw,
                    raw_home_team_id=home_resolution.team_id,
                    raw_away_team_id=away_resolution.team_id,
                    target_home_team_id=canonical.home_team_id,
                    target_away_team_id=canonical.away_team_id,
                    target_home_team=canonical.home_team,
                    target_away_team=canonical.away_team,
                )
            continue

        known_team = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        slot = _event_identity_slot(raw.start_time, raw.sport)
        candidates = [
            matchup
            for matchup in matchups_by_slot.get(slot, [])
            if known_team.team_id is not None
            and known_team.team_id in {matchup.home_team_id, matchup.away_team_id}
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
                    league_id=direct_league.league_id,
                    sport=raw.sport,
                    market_type=raw.market_type,
                    player_name=raw.player_name,
                    raw_team_name=raw.home_team,
                    normalized_team_name=known_team.team_name,
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
            continue

        selected = candidates[0]
        resolved.append(
            RawOddsData(
                bookmaker_id=raw.bookmaker_id,
                league_id=raw.league_id,
                sport=raw.sport,
                home_team=selected.home_team,
                away_team=selected.away_team,
                source_url=raw.source_url,
                market_type=raw.market_type,
                player_name=raw.player_name,
                threshold=raw.threshold,
                over_odds=raw.over_odds,
                under_odds=raw.under_odds,
                start_time=raw.start_time,
            )
        )

    return resolved, unresolved


def log_unresolved_shared_platform_diagnostics(
    unresolved: list[UnresolvedOddsDiagnostic],
) -> None:
    grouped: dict[tuple[str, str, str, str | None], list[UnresolvedOddsDiagnostic]] = defaultdict(list)
    for row in unresolved:
        if row.reason_code not in {
            "no_canonical_matchup_for_team_at_slot",
            "ambiguous_multiple_matchups_for_team_at_slot",
        }:
            continue
        grouped[
            (
                row.bookmaker_id,
                row.normalized_team_name,
                row.reason_code,
                row.start_time,
            )
        ].append(row)

    for (
        bookmaker_id,
        normalized_team_name,
        reason_code,
        start_time,
    ), rows in sorted(grouped.items()):
        player_examples = ", ".join(
            sorted({row.player_name for row in rows if row.player_name})[:5]
        )
        logger.warning(
            (
                "Dropping %d unresolved shared-platform props for %s "
                "(%s, %s, start=%s%s)"
            ),
            len(rows),
            normalized_team_name,
            bookmaker_id,
            reason_code,
            start_time or "unknown",
            f", players={player_examples}" if player_examples else "",
        )


def normalize_odds_with_diagnostics(
    raw_list: list[RawOddsData],
    *,
    log_unresolved_shared_platform: bool = True,
) -> tuple[
    list[NormalizedOdds],
    list[UnresolvedOddsDiagnostic],
    list[TeamReviewDiagnostic],
]:
    results: list[NormalizedOdds] = []
    timed_raw_list, missing_start_time = _separate_missing_start_times(raw_list)
    _autocreate_exact_match_teams(timed_raw_list)
    resolved_shared_platform, unresolved_shared_platform = _resolve_shared_platform_matchups(
        timed_raw_list
    )
    if log_unresolved_shared_platform:
        log_unresolved_shared_platform_diagnostics(unresolved_shared_platform)
    resolved_raw_list = _resolve_contextual_player_names(resolved_shared_platform)
    slot_resolutions = _build_event_slot_resolutions(resolved_raw_list)

    for raw in resolved_raw_list:
        direct_league = resolve_league(raw.league_id, raw.bookmaker_id)
        slot_home = resolve_team_name(
            raw.home_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        slot_away = resolve_team_name(
            raw.away_team,
            bookmaker_id=raw.bookmaker_id,
            sport=raw.sport,
        )
        if (
            raw.start_time is None
            or slot_home.team_id is None
            or slot_away.team_id is None
        ):
            continue
        slot = _event_slot_key(
            slot_home.team_id,
            slot_away.team_id,
            raw.start_time,
            raw.sport,
        )
        slot_resolution = slot_resolutions.get(slot)
        if slot_resolution is None:
            continue

        match_id = generate_match_id(
            slot_resolution.home_team_id,
            slot_resolution.away_team_id,
            raw.start_time,
            raw.sport,
        )
        player = normalize_player_name(raw.player_name)
        market = normalize_market_type(raw.market_type)
        threshold, over_odds, under_odds = _reoriented_market_values(
            raw,
            raw_home_team_id=slot_home.team_id,
            raw_away_team_id=slot_away.team_id,
            target_home_team_id=slot_resolution.home_team_id,
            target_away_team_id=slot_resolution.away_team_id,
        )

        results.append(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=raw.bookmaker_id,
                league_id=slot_resolution.league_id or direct_league.league_id,
                sport=raw.sport,
                home_team_id=slot_resolution.home_team_id,
                away_team_id=slot_resolution.away_team_id,
                home_team=slot_resolution.home_team,
                away_team=slot_resolution.away_team,
                source_url=raw.source_url,
                market_type=market,
                player_name=player,
                threshold=threshold,
                over_odds=over_odds,
                under_odds=under_odds,
                start_time=raw.start_time,
            )
        )

    team_review_cases = _build_team_review_cases(resolved_raw_list, slot_resolutions)
    return (
        results,
        [*missing_start_time, *unresolved_shared_platform],
        team_review_cases,
    )


def normalize_odds_with_issues(
    raw_list: list[RawOddsData],
) -> tuple[list[NormalizedOdds], list[UnresolvedOddsDiagnostic]]:
    normalized, unresolved, _ = normalize_odds_with_diagnostics(raw_list)
    return normalized, unresolved


def normalize_odds(raw_list: list[RawOddsData]) -> list[NormalizedOdds]:
    return normalize_odds_with_diagnostics(raw_list)[0]
