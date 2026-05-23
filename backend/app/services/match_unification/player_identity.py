from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from typing import Literal

from ...models.schemas import NormalizedOdds, ResolvedEventMemberOut
from ..normalizer import resolve_contextual_player_name_variants
from ..text_normalizer import compact_identity_text


PlayerIdentitySkipReason = Literal[
    "non_player_market",
    "unsupported_sport",
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
            "empty_player_name": 0,
            "missing_resolved_event_member": 0,
        }
        for skipped in self.skipped:
            counts[skipped.reason] += 1
        return counts


def is_basketball_player_prop(odds: NormalizedOdds) -> bool:
    return (
        odds.sport == "basketball"
        and odds.player_name is not None
        and odds.player_name.strip() != ""
        and odds.market_type.startswith("player_")
    )


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
    if not odds.market_type.startswith("player_"):
        return "non_player_market"
    if odds.sport != "basketball":
        return "unsupported_sport"
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
        display_by_variant = resolve_contextual_player_name_variants(source_names)
        variants_by_display: dict[str, set[str]] = defaultdict(set)
        for variant, display_name in display_by_variant.items():
            variants_by_display[display_name].add(variant)

        identities: dict[str, EventScopedPlayerIdentity] = {}
        for display_name, variants in variants_by_display.items():
            identities[display_name] = EventScopedPlayerIdentity(
                resolved_event_id=resolved_event_id,
                event_scoped_player_key=_event_scoped_player_key(
                    resolved_event_id,
                    display_name,
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


def _event_scoped_player_key(resolved_event_id: str, display_name: str) -> str:
    compact_name = compact_identity_text(display_name)
    key_source = compact_name or display_name.strip().lower()
    digest = hashlib.md5(f"{resolved_event_id}:{key_source}".encode()).hexdigest()[:12]
    return f"ply_{digest}"


def _variant_sort_key(display_name: str, variant: str) -> tuple[int, str, str]:
    return (
        0 if variant == display_name else 1,
        compact_identity_text(variant),
        variant,
    )
