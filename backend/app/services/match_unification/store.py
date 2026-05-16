from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from ...models.schemas import EventReviewCaseIn, ResolvedEventIn, ResolvedEventMemberIn
from ...store import odds_store


class MatchUnificationStore(Protocol):
    async def persist_event_resolution_batch(
        self,
        *,
        snapshot_id: str | None = None,
        events: list[ResolvedEventIn],
        members: list[ResolvedEventMemberIn],
        review_cases: list[EventReviewCaseIn],
    ) -> dict[str, int]:
        ...


class OddsStoreMatchUnificationAdapter:
    """Production Adapter for resolved-event graph persistence."""

    def __init__(self, store=odds_store) -> None:
        self._store = store

    async def persist_event_resolution_batch(
        self,
        *,
        snapshot_id: str | None = None,
        events: list[ResolvedEventIn],
        members: list[ResolvedEventMemberIn],
        review_cases: list[EventReviewCaseIn],
    ) -> dict[str, int]:
        return await self._store.persist_event_resolution_batch(
            snapshot_id=snapshot_id,
            events=events,
            members=members,
            review_cases=review_cases,
        )


class InMemoryMatchUnificationStore:
    """Local test Adapter that records graph writes and can model failures."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[dict[str, object]] = []

    async def persist_event_resolution_batch(
        self,
        *,
        snapshot_id: str | None = None,
        events: list[ResolvedEventIn],
        members: list[ResolvedEventMemberIn],
        review_cases: list[EventReviewCaseIn],
    ) -> dict[str, int]:
        if self.fail:
            raise RuntimeError("in-memory match unification persistence failure")
        self.batches.append(
            {
                "snapshot_id": snapshot_id,
                "events": deepcopy(events),
                "members": deepcopy(members),
                "review_cases": deepcopy(review_cases),
            }
        )
        return {
            "resolved_events": len(events),
            "resolved_event_members": len(members),
            "review_cases": len(review_cases),
        }

