from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from typing import Literal

from ...models.schemas import NormalizedOdds, ResolvedEventMemberOut
from ..normalizer import resolve_contextual_player_name_variants
from ..tennis_name_matcher import match_tennis_player_names, tennis_player_name_variants
from ..text_normalizer import (
    compact_identity_text,
    normalize_identity_text,
    tokenize_identity_text,
)


PlayerIdentitySkipReason = Literal[
    "non_player_market",
    "unsupported_sport",
    "unsupported_player_market",
    "empty_player_name",
    "missing_resolved_event_member",
]


@dataclass(frozen=True)
class ActiveEventMembership:
    members: tuple[ResolvedEventMemberOut, ...]
    _member_by_source: Mapping[tuple[str, str], ResolvedEventMemberOut] = field(
        repr=False
    )

    @classmethod
    def from_members(
        cls,
        event_members: Sequence[ResolvedEventMemberOut],
    ) -> "ActiveEventMembership":
        member_by_source: dict[tuple[str, str], ResolvedEventMemberOut] = {}
        for member in sorted(
            event_members,
            key=lambda item: (
                item.resolved_event_id,
                item.id,
                item.match_id,
                item.bookmaker_id,
            ),
        ):
            if member.status != "active":
                continue
            member_by_source.setdefault((member.match_id, member.bookmaker_id), member)
        return cls(
            members=tuple(member_by_source.values()),
            _member_by_source=member_by_source,
        )

    def member_for(
        self,
        *,
        match_id: str,
        bookmaker_id: str,
    ) -> ResolvedEventMemberOut | None:
        return self._member_by_source.get((match_id, bookmaker_id))

    def resolved_event_id_for(
        self,
        *,
        match_id: str,
        bookmaker_id: str,
    ) -> str | None:
        member = self.member_for(match_id=match_id, bookmaker_id=bookmaker_id)
        return member.resolved_event_id if member is not None else None


@dataclass(frozen=True)
class EventScopedPlayerIdentity:
    resolved_event_id: str
    event_scoped_player_key: str
    display_name: str
    source_variants: tuple[str, ...]


@dataclass(frozen=True)
class EventScopedPlayerOdds:
    odds: NormalizedOdds
    resolved_event_id: str
    event_scoped_player_key: str
    event_player_display_name: str
    source_player_name_variants: tuple[str, ...]

    @property
    def comparison_group_key(self) -> tuple[str, str, str]:
        return (
            self.resolved_event_id,
            self.odds.market_type,
            self.event_scoped_player_key,
        )


@dataclass(frozen=True)
class SkippedPlayerOdds:
    odds: NormalizedOdds
    reason: PlayerIdentitySkipReason


@dataclass(frozen=True)
class EventPlayerResolution:
    scoped_odds: tuple[EventScopedPlayerOdds, ...]
    identities: tuple[EventScopedPlayerIdentity, ...]
    skipped: tuple[SkippedPlayerOdds, ...] = ()

    @property
    def skipped_counts(self) -> dict[PlayerIdentitySkipReason, int]:
        counts: dict[PlayerIdentitySkipReason, int] = {
            "non_player_market": 0,
            "unsupported_sport": 0,
            "unsupported_player_market": 0,
            "empty_player_name": 0,
            "missing_resolved_event_member": 0,
        }
        for skipped in self.skipped:
            counts[skipped.reason] += 1
        return counts


@dataclass(frozen=True)
class _PlayerIdentityPolicy:
    market_supported: Callable[[str], bool]
    resolve_display_names: Callable[[Sequence[str]], dict[str, str]]

    def supports(self, market_type: str) -> bool:
        return self.market_supported(market_type)


_TENNIS_PLAYER_MARKETS = frozenset(
    {
        "player_games_won",
        "player_aces",
        "player_double_faults",
    }
)
_TENNIS_SURNAME_PARTICLES = frozenset(
    {"da", "de", "del", "della", "di", "du", "la", "le", "van", "von"}
)


def _supports_player_prefix(market_type: str) -> bool:
    return market_type.startswith("player_")


def _supports_tennis_player_market(market_type: str) -> bool:
    return market_type in _TENNIS_PLAYER_MARKETS


def _default_player_name_variants(names: Sequence[str]) -> dict[str, str]:
    return resolve_contextual_player_name_variants(list(names))


_PLAYER_IDENTITY_POLICIES: Mapping[str, _PlayerIdentityPolicy] = {
    "basketball": _PlayerIdentityPolicy(
        market_supported=_supports_player_prefix,
        resolve_display_names=_default_player_name_variants,
    ),
    "tennis": _PlayerIdentityPolicy(
        market_supported=_supports_tennis_player_market,
        resolve_display_names=lambda names: _resolve_tennis_player_name_variants(names),
    ),
}


def _sport_key(sport: str | None) -> str:
    return (sport or "").strip().lower()


def _player_identity_policy_for(sport: str | None) -> _PlayerIdentityPolicy | None:
    return _PLAYER_IDENTITY_POLICIES.get(_sport_key(sport))


def is_player_market_candidate(odds: NormalizedOdds) -> bool:
    return odds.market_type.startswith("player_")


def resolve_event_players(
    odds_list: Sequence[NormalizedOdds],
    membership: ActiveEventMembership,
) -> EventPlayerResolution:
    """Resolve player labels within active resolved-event membership.

    Event-scoped player keys are deterministic only inside one resolved event.
    Callers should use the returned bindings instead of rebuilding key semantics.
    """

    odds_by_event: dict[str, list[NormalizedOdds]] = defaultdict(list)
    skipped: list[SkippedPlayerOdds] = []
    for odds in odds_list:
        skip_reason = _player_identity_skip_reason(odds)
        if skip_reason is not None:
            skipped.append(SkippedPlayerOdds(odds=odds, reason=skip_reason))
            continue
        resolved_event_id = membership.resolved_event_id_for(
            match_id=odds.match_id,
            bookmaker_id=odds.bookmaker_id,
        )
        if resolved_event_id is None:
            skipped.append(
                SkippedPlayerOdds(
                    odds=odds,
                    reason="missing_resolved_event_member",
                )
            )
            continue
        odds_by_event[resolved_event_id].append(odds)

    scoped_odds = _resolve_scoped_odds(odds_by_event)
    return EventPlayerResolution(
        scoped_odds=scoped_odds,
        identities=_event_scoped_player_identities(scoped_odds),
        skipped=tuple(skipped),
    )


def _player_identity_skip_reason(
    odds: NormalizedOdds,
) -> PlayerIdentitySkipReason | None:
    if not is_player_market_candidate(odds):
        return "non_player_market"
    policy = _player_identity_policy_for(odds.sport)
    if policy is None:
        return "unsupported_sport"
    if not policy.supports(odds.market_type):
        return "unsupported_player_market"
    if odds.player_name is None or odds.player_name.strip() == "":
        return "empty_player_name"
    return None


def _resolve_scoped_odds(
    odds_by_event: Mapping[str, list[NormalizedOdds]],
) -> tuple[EventScopedPlayerOdds, ...]:
    resolved: list[EventScopedPlayerOdds] = []
    for resolved_event_id in sorted(odds_by_event):
        event_odds = odds_by_event[resolved_event_id]
        source_names = [
            odds.player_name.strip()
            for odds in event_odds
            if odds.player_name and odds.player_name.strip()
        ]
        display_by_variant = _resolve_event_display_names(event_odds, source_names)
        variants_by_display: dict[str, set[str]] = defaultdict(set)
        for variant, display_name in display_by_variant.items():
            variants_by_display[display_name].add(variant)

        key_source_by_display = _event_scoped_player_key_sources(
            variants_by_display.keys()
        )
        identities: dict[str, EventScopedPlayerIdentity] = {}
        for display_name, variants in variants_by_display.items():
            identities[display_name] = EventScopedPlayerIdentity(
                resolved_event_id=resolved_event_id,
                event_scoped_player_key=_event_scoped_player_key(
                    resolved_event_id,
                    key_source_by_display[display_name],
                ),
                display_name=display_name,
                source_variants=tuple(
                    sorted(
                        variants,
                        key=lambda variant: _variant_sort_key(display_name, variant),
                    )
                ),
            )

        for odds in event_odds:
            if not odds.player_name:
                continue
            source_name = odds.player_name.strip()
            display_name = display_by_variant.get(source_name, source_name)
            identity = identities[display_name]
            resolved.append(
                EventScopedPlayerOdds(
                    odds=odds,
                    resolved_event_id=resolved_event_id,
                    event_scoped_player_key=identity.event_scoped_player_key,
                    event_player_display_name=identity.display_name,
                    source_player_name_variants=identity.source_variants,
                )
            )

    return tuple(
        sorted(
            resolved,
            key=lambda item: (
                item.resolved_event_id,
                item.odds.match_id,
                item.odds.bookmaker_id,
                item.odds.market_type,
                item.odds.player_name or "",
                item.odds.threshold,
            ),
        )
    )


def _event_scoped_player_identities(
    event_scoped_odds: Sequence[EventScopedPlayerOdds],
) -> tuple[EventScopedPlayerIdentity, ...]:
    identities: dict[tuple[str, str], EventScopedPlayerIdentity] = {}
    for player_odds in event_scoped_odds:
        key = (player_odds.resolved_event_id, player_odds.event_scoped_player_key)
        identities.setdefault(
            key,
            EventScopedPlayerIdentity(
                resolved_event_id=player_odds.resolved_event_id,
                event_scoped_player_key=player_odds.event_scoped_player_key,
                display_name=player_odds.event_player_display_name,
                source_variants=player_odds.source_player_name_variants,
            ),
        )
    return tuple(
        identities[key]
        for key in sorted(
            identities,
            key=lambda item: (item[0], identities[item].display_name, item[1]),
        )
    )


def _resolve_event_display_names(
    event_odds: Sequence[NormalizedOdds],
    source_names: Sequence[str],
) -> dict[str, str]:
    sports = {_sport_key(odds.sport) for odds in event_odds}
    if len(sports) == 1:
        policy = _player_identity_policy_for(next(iter(sports)))
        if policy is not None:
            return policy.resolve_display_names(source_names)
    return _default_player_name_variants(source_names)


class _NameUnionFind:
    def __init__(self, names: Sequence[str]) -> None:
        self._parent = {name: name for name in names}

    def find(self, name: str) -> str:
        root = name
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[name] != name:
            parent = self._parent[name]
            self._parent[name] = root
            name = parent
        return root

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        winner, loser = sorted((left_root, right_root))
        self._parent[loser] = winner

    def __contains__(self, name: str) -> bool:
        return name in self._parent

    def groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for name in self._parent:
            groups[self.find(name)].append(name)
        return groups


def _resolve_tennis_player_name_variants(names: Sequence[str]) -> dict[str, str]:
    original_names = [name.strip() for name in names if name and name.strip()]
    unique_names = tuple(dict.fromkeys(original_names))
    if not unique_names:
        return {}

    union = _NameUnionFind(unique_names)
    contextual_display = resolve_contextual_player_name_variants(list(unique_names))
    for source_name, display_name in contextual_display.items():
        if display_name in union:
            union.union(source_name, display_name)

    full_names = [
        name for name in unique_names if _tennis_name_has_full_given_evidence(name)
    ]
    for index, left in enumerate(full_names):
        for right in full_names[index + 1 :]:
            if _tennis_full_names_match_for_identity(left, right):
                union.union(left, right)

    for name in unique_names:
        if _tennis_name_has_full_given_evidence(name):
            continue
        if _tennis_name_has_abbreviated_given_evidence(name):
            _union_unique_tennis_full_name_match(union, name, full_names)

    for name in unique_names:
        if _tennis_name_has_full_given_evidence(name):
            continue
        if _tennis_name_has_abbreviated_given_evidence(name):
            continue
        if _tennis_family_keys(name):
            _union_unique_tennis_family_match(union, name, full_names)

    name_counts = Counter(original_names)
    display_by_root = {
        root: _tennis_display_name(members, name_counts)
        for root, members in union.groups().items()
    }
    return {name: display_by_root[union.find(name)] for name in unique_names}


def _union_unique_tennis_full_name_match(
    union: _NameUnionFind,
    name: str,
    full_names: Sequence[str],
) -> None:
    matches = [
        full_name
        for full_name in full_names
        if match_tennis_player_names(name, full_name) is not None
    ]
    matched_roots = {union.find(full_name) for full_name in matches}
    if len(matched_roots) != 1:
        return
    union.union(name, min(matches))


def _union_unique_tennis_family_match(
    union: _NameUnionFind,
    name: str,
    full_names: Sequence[str],
) -> None:
    family_keys = _tennis_family_keys(name)
    matches = [
        full_name
        for full_name in full_names
        if _tennis_family_key_sets_match(family_keys, _tennis_family_keys(full_name))
    ]
    matched_roots = {union.find(full_name) for full_name in matches}
    if len(matched_roots) != 1:
        return
    union.union(name, min(matches))


def _tennis_full_names_match_for_identity(left_name: str, right_name: str) -> bool:
    for left in tennis_player_name_variants(left_name):
        if not left.has_full_given or left.order == "unpunctuated_last_first":
            continue
        for right in tennis_player_name_variants(right_name):
            if not right.has_full_given or right.order == "unpunctuated_last_first":
                continue
            if not _tennis_family_tokens_match(left.family_tokens, right.family_tokens):
                continue
            if left.given_tokens == right.given_tokens:
                return True
    return False


def _tennis_name_has_full_given_evidence(name: str) -> bool:
    if _tennis_family_only_particle_tokens(name):
        return False
    return any(variant.has_full_given for variant in tennis_player_name_variants(name))


def _tennis_name_has_abbreviated_given_evidence(name: str) -> bool:
    if _tennis_family_only_particle_tokens(name):
        return False
    return any(
        variant.abbreviated_given_tokens for variant in tennis_player_name_variants(name)
    )


def _tennis_family_keys(name: str) -> frozenset[tuple[str, ...]]:
    keys = {variant.family_tokens for variant in tennis_player_name_variants(name)}
    stripped = name.strip()
    if "," in stripped:
        family_tokens = tuple(tokenize_identity_text(stripped.split(",", 1)[0]))
        if family_tokens:
            keys.add(family_tokens)
    else:
        tokens = tuple(tokenize_identity_text(stripped))
        if len(tokens) == 1:
            keys.add(tokens)
        particle_tokens = _tennis_family_only_particle_tokens(stripped)
        if particle_tokens:
            keys.add(particle_tokens)
    return frozenset(keys)


def _tennis_family_only_particle_tokens(name: str) -> tuple[str, ...]:
    if "," in name:
        return ()
    tokens = tuple(tokenize_identity_text(name))
    if len(tokens) > 1 and tokens[0] in _TENNIS_SURNAME_PARTICLES:
        return tokens
    return ()


def _tennis_family_key_sets_match(
    left_keys: frozenset[tuple[str, ...]],
    right_keys: frozenset[tuple[str, ...]],
) -> bool:
    return any(
        _tennis_family_tokens_match(left_key, right_key)
        for left_key in left_keys
        for right_key in right_keys
    )


def _tennis_family_tokens_match(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> bool:
    if left == right:
        return True
    if len(left) > len(right):
        return left[-len(right) :] == right
    if len(right) > len(left):
        return right[-len(left) :] == left
    return False


def _tennis_display_name(
    names: Sequence[str],
    name_counts: Counter[str],
) -> str:
    return max(names, key=lambda name: _tennis_display_sort_key(name, name_counts))


def _tennis_display_sort_key(
    name: str,
    name_counts: Counter[str],
) -> tuple[int, int, int, int, str, int, int, str]:
    variants = (
        ()
        if _tennis_family_only_particle_tokens(name)
        else tennis_player_name_variants(name)
    )
    has_western_full = any(
        variant.has_full_given and variant.order == "western" for variant in variants
    )
    has_full = any(variant.has_full_given for variant in variants)
    is_comma_order = any(variant.order == "comma_last_first" for variant in variants)
    return (
        1 if has_western_full else 0,
        1 if has_full else 0,
        0 if is_comma_order else 1,
        name_counts[name],
        normalize_identity_text(name),
        _title_case_token_count(name),
        _non_ascii_count(name),
        name.strip(),
    )


def _title_case_token_count(name: str) -> int:
    return sum(1 for token in name.split() if token[:1].isupper())


def _non_ascii_count(name: str) -> int:
    return sum(1 for char in name if ord(char) > 127)


def _event_scoped_player_key_sources(
    display_names: Iterable[str],
) -> dict[str, str]:
    """Return deterministic per-display key sources before event-id hashing.

    The compact source keeps related names readable in tests/debugging, while the
    normalized display digest makes each identity label independent of which
    peers are present in the same event. That prevents compact-name collisions
    from sharing a key without churning for accent/case-only display changes.
    """

    return {
        display_name: _event_scoped_player_key_source(display_name)
        for display_name in display_names
    }


def _event_scoped_player_key_source(display_name: str) -> str:
    """Build the event-local key source before resolved-event scoping is applied."""

    compact_name = compact_identity_text(display_name)
    base_source = (
        compact_name
        or normalize_identity_text(display_name)
        or display_name.strip().lower()
    )
    normalized_display = (
        normalize_identity_text(display_name) or display_name.strip().lower()
    )
    display_digest = hashlib.md5(normalized_display.encode()).hexdigest()[:8]
    return f"{base_source}:{display_digest}"


def _event_scoped_player_key(resolved_event_id: str, key_source: str) -> str:
    digest = hashlib.md5(f"{resolved_event_id}:{key_source}".encode()).hexdigest()[:12]
    return f"ply_{digest}"


def _variant_sort_key(display_name: str, variant: str) -> tuple[int, str, str]:
    return (
        0 if variant == display_name else 1,
        compact_identity_text(variant),
        variant,
    )
