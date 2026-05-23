from __future__ import annotations

import pytest

from app.services.match_unification import (
    InMemoryMatchUnificationStore,
    MatchUnification,
    MatchUnificationInputError,
    MatchUnificationRows,
    PersistedScrapeSnapshot,
)


def _snapshot() -> PersistedScrapeSnapshot:
    return PersistedScrapeSnapshot(
        id="snapshot-1",
        scraped_at="2026-01-01T00:00:00+01:00",
        seen_match_ids=frozenset(),
    )


def _empty_rows() -> MatchUnificationRows:
    return MatchUnificationRows(
        raw_odds=(),
        raw_outcome_offers=(),
        normalized_odds=(),
        normalized_outcome_offers=(),
    )


@pytest.mark.asyncio
async def test_match_unification_persists_empty_snapshot_through_adapter():
    store = InMemoryMatchUnificationStore()
    unification = MatchUnification(store=store)

    result = await unification.unify_after_snapshot(
        snapshot=_snapshot(),
        rows=_empty_rows(),
    )

    assert result.mode == "resolved_event_graph"
    assert result.status is not None
    assert result.status.state == "unified"
    assert result.benchmark.state == "unified"
    assert store.batches == [
        {
            "snapshot_id": "snapshot-1",
            "events": [],
            "members": [],
            "review_cases": [],
        }
    ]


@pytest.mark.asyncio
async def test_match_unification_fallback_is_visible_when_adapter_fails():
    unification = MatchUnification(store=InMemoryMatchUnificationStore(fail=True))

    result = await unification.unify_after_snapshot(
        snapshot=_snapshot(),
        rows=_empty_rows(),
    )

    assert result.mode == "match_id_only"
    assert result.status is not None
    assert result.status.state == "match_id_only"
    assert result.benchmark.state == "match_id_only"
    assert result.benchmark.warnings == ["match_unification_failed"]
    assert result.warnings[0].code == "match_unification_failed"


@pytest.mark.asyncio
async def test_match_unification_requires_snapshot_id():
    unification = MatchUnification(store=InMemoryMatchUnificationStore())

    with pytest.raises(MatchUnificationInputError):
        await unification.unify_after_snapshot(
            snapshot=PersistedScrapeSnapshot(
                id="",
                scraped_at="2026-01-01T00:00:00+01:00",
            ),
            rows=_empty_rows(),
        )
