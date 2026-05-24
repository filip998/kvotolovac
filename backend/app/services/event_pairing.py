from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, TypeVar, cast

_EventT = TypeVar("_EventT")
_CacheT = TypeVar("_CacheT")


@dataclass(frozen=True)
class EventPairingBucketSummary:
    sport: str
    start_time: str
    event_count: int
    bookmaker_count: int
    candidate_pair_count: int


class EventPairingCycle:
    """Cycle-level Event Pairing state shared by scrape pipeline phases."""

    def __init__(self) -> None:
        self._caches: dict[str, object] = {}

    def cache(self, name: str, factory: Callable[[], _CacheT]) -> _CacheT:
        cached = self._caches.get(name)
        if cached is None:
            cached = factory()
            self._caches[name] = cached
        return cast(_CacheT, cached)

    @staticmethod
    def pair_bucket_key(sport: str, start_time: str) -> tuple[str, str]:
        if sport == "tennis":
            return (sport, "__tennis_time_drift__")
        return (sport, start_time)

    def pair_buckets(
        self,
        events: list[_EventT],
        *,
        sport: Callable[[_EventT], str],
        start_time: Callable[[_EventT], str],
    ) -> list[list[_EventT]]:
        buckets: dict[tuple[str, str], list[_EventT]] = defaultdict(list)
        for event in events:
            buckets[self.pair_bucket_key(sport(event), start_time(event))].append(event)
        return list(buckets.values())

    def slot_summaries(
        self,
        events: list[_EventT],
        *,
        sport: Callable[[_EventT], str],
        start_time: Callable[[_EventT], str],
        bookmaker_id: Callable[[_EventT], str],
    ) -> list[EventPairingBucketSummary]:
        events_by_slot: dict[tuple[str, str], list[_EventT]] = defaultdict(list)
        for event in events:
            events_by_slot[(sport(event), start_time(event))].append(event)

        summaries: list[EventPairingBucketSummary] = []
        for (slot_sport, slot_start_time), slot_events in events_by_slot.items():
            bookmaker_counts = Counter(bookmaker_id(event) for event in slot_events)
            total_pairs = len(slot_events) * (len(slot_events) - 1) // 2
            same_bookmaker_pairs = sum(
                count * (count - 1) // 2 for count in bookmaker_counts.values()
            )
            summaries.append(
                EventPairingBucketSummary(
                    sport=slot_sport,
                    start_time=slot_start_time,
                    event_count=len(slot_events),
                    bookmaker_count=len(bookmaker_counts),
                    candidate_pair_count=total_pairs - same_bookmaker_pairs,
                )
            )
        return summaries
