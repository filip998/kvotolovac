from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib

from ..models.schemas import NormalizedOdds, ResolvedEventMemberOut
from .normalizer import resolve_contextual_player_name_variants
from .text_normalizer import compact_identity_text


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


def is_basketball_player_prop(odds: NormalizedOdds) -> bool:
    return (
        odds.sport == "basketball"
        and odds.player_name is not None
        and odds.player_name.strip() != ""
        and odds.market_type.startswith("player_")
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


def _active_member_event_lookup(
    event_members: list[ResolvedEventMemberOut],
) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for member in sorted(
        event_members,
        key=lambda item: (item.resolved_event_id, item.id, item.match_id, item.bookmaker_id),
    ):
        if member.status != "active":
            continue
        lookup.setdefault((member.match_id, member.bookmaker_id), member.resolved_event_id)
    return lookup


def build_event_scoped_player_odds(
    odds_list: list[NormalizedOdds],
    event_members: list[ResolvedEventMemberOut],
) -> list[EventScopedPlayerOdds]:
    """Resolve player labels within active resolved-event membership.

    The returned key is deterministic only inside its resolved event. Callers should
    group by ``(resolved_event_id, market_type, event_scoped_player_key)`` instead of
    treating it as a global player identity.
    """

    event_by_member = _active_member_event_lookup(event_members)
    odds_by_event: dict[str, list[NormalizedOdds]] = defaultdict(list)
    for odds in odds_list:
        if not is_basketball_player_prop(odds):
            continue
        resolved_event_id = event_by_member.get((odds.match_id, odds.bookmaker_id))
        if resolved_event_id is None:
            continue
        odds_by_event[resolved_event_id].append(odds)

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

    return sorted(
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


def build_event_scoped_player_identities(
    event_scoped_odds: list[EventScopedPlayerOdds],
) -> list[EventScopedPlayerIdentity]:
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
    return [
        identities[key]
        for key in sorted(
            identities,
            key=lambda item: (item[0], identities[item].display_name, item[1]),
        )
    ]
