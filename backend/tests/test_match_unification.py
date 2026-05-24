from __future__ import annotations

import pytest

from app.models.schemas import MatchUnificationBenchmarkOut
from app.services.match_unification import (
    InMemoryMatchUnificationStore,
    MatchUnification,
    MatchUnificationInputError,
    MatchUnificationPersistenceMetrics,
    MatchUnificationPersistenceMetricsError,
    MatchUnificationResult,
    MatchUnificationStatus,
    MatchUnificationRows,
    OddsStoreMatchUnificationAdapter,
    PersistedScrapeSnapshot,
)
from app.services.match_unification.resolution import persist_event_resolution_groups
from app.store import odds_store


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


class _LegacyPersistenceStore:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    async def persist_event_resolution_batch(self, **_kwargs):
        return self.result


@pytest.mark.asyncio
async def test_match_unification_persists_empty_snapshot_through_adapter():
    store = InMemoryMatchUnificationStore()
    unification = MatchUnification(store=store)

    result = await unification.unify_after_snapshot(
        snapshot=_snapshot(),
        rows=_empty_rows(),
    )

    assert result.mode == "resolved_event_graph"
    assert result.status.state == "unified"
    assert result.status.mode == "resolved_event_graph"
    assert result.snapshot_id == result.status.snapshot_id == "snapshot-1"
    assert result.mode == result.status.mode
    assert result.warnings == result.status.warnings
    assert result.fallback_reason == result.status.fallback_reason
    assert result.to_benchmark_out().state == "unified"
    assert store.batches == [
        {
            "snapshot_id": "snapshot-1",
            "events": [],
            "members": [],
            "review_cases": [],
        }
    ]


@pytest.mark.asyncio
async def test_in_memory_store_returns_typed_persistence_metrics():
    store = InMemoryMatchUnificationStore()

    result = await store.persist_event_resolution_batch(
        snapshot_id="snapshot-1",
        events=[],
        members=[],
        review_cases=[],
    )

    assert result == MatchUnificationPersistenceMetrics(
        resolved_events=0,
        resolved_event_members=0,
        review_cases=0,
    )


@pytest.mark.asyncio
async def test_store_adapter_wraps_legacy_persistence_metrics():
    adapter = OddsStoreMatchUnificationAdapter(
        _LegacyPersistenceStore(
            {
                "resolved_events": 2,
                "resolved_event_members": 5,
                "review_cases": 1,
            }
        )
    )

    result = await adapter.persist_event_resolution_batch(
        snapshot_id="snapshot-1",
        events=[],
        members=[],
        review_cases=[],
    )

    assert result == MatchUnificationPersistenceMetrics(
        resolved_events=2,
        resolved_event_members=5,
        review_cases=1,
    )


@pytest.mark.asyncio
async def test_store_adapter_wraps_real_odds_store_metrics():
    adapter = OddsStoreMatchUnificationAdapter(odds_store)

    result = await adapter.persist_event_resolution_batch(
        snapshot_id="snapshot-1",
        events=[],
        members=[],
        review_cases=[],
    )

    assert result == MatchUnificationPersistenceMetrics(
        resolved_events=0,
        resolved_event_members=0,
        review_cases=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("legacy_result", "message"),
    [
        (
            {"resolved_events": 1, "resolved_event_members": 2},
            "missing key",
        ),
        (
            {
                "resolved_events": "1",
                "resolved_event_members": 2,
                "review_cases": 3,
            },
            "must be a non-negative int",
        ),
        (
            {
                "resolved_events": 1,
                "resolved_event_members": -2,
                "review_cases": 3,
            },
            "must be non-negative",
        ),
    ],
)
async def test_store_adapter_rejects_malformed_legacy_metrics(
    legacy_result,
    message,
):
    adapter = OddsStoreMatchUnificationAdapter(_LegacyPersistenceStore(legacy_result))

    with pytest.raises(MatchUnificationPersistenceMetricsError, match=message):
        await adapter.persist_event_resolution_batch(
            snapshot_id="snapshot-1",
            events=[],
            members=[],
            review_cases=[],
        )


@pytest.mark.asyncio
async def test_resolution_persistence_consumes_typed_metrics():
    class MetricsStore:
        async def persist_event_resolution_batch(self, **_kwargs):
            return MatchUnificationPersistenceMetrics(
                resolved_events=3,
                resolved_event_members=7,
                review_cases=2,
            )

    result = await persist_event_resolution_groups([], [], store=MetricsStore())

    assert result.resolved_events == 3
    assert result.resolved_event_members == 7
    assert result.review_cases == 2


@pytest.mark.asyncio
async def test_match_unification_fallback_is_visible_when_adapter_fails():
    unification = MatchUnification(store=InMemoryMatchUnificationStore(fail=True))

    result = await unification.unify_after_snapshot(
        snapshot=_snapshot(),
        rows=_empty_rows(),
    )

    assert result.mode == "match_id_only"
    assert result.status.state == "match_id_only"
    assert result.status.mode == "match_id_only"
    assert result.status.fallback_reason is not None
    assert result.status.warnings[0].detail == result.status.fallback_reason
    benchmark = result.to_benchmark_out()
    assert benchmark.state == "match_id_only"
    assert benchmark.mode == "match_id_only"
    assert benchmark.warnings == ["match_unification_failed"]
    assert benchmark.fallback_reason == result.status.fallback_reason
    assert result.warnings[0].code == "match_unification_failed"


@pytest.mark.asyncio
async def test_malformed_adapter_metrics_fall_back_visibly():
    unification = MatchUnification(
        store=OddsStoreMatchUnificationAdapter(
            _LegacyPersistenceStore(
                {
                    "resolved_events": 0,
                    "resolved_event_members": 0,
                }
            )
        )
    )

    result = await unification.unify_after_snapshot(
        snapshot=_snapshot(),
        rows=_empty_rows(),
    )

    assert result.status == MatchUnificationStatus.match_id_only(
        snapshot_id="snapshot-1",
        warning=result.warnings[0],
        fallback_reason=result.fallback_reason or "",
    )
    assert "MatchUnificationPersistenceMetricsError" in (result.fallback_reason or "")
    assert result.to_cycle_status_out().warnings == [result.warnings[0].detail]
    assert result.to_benchmark_out().warnings == ["match_unification_failed"]


def test_match_unification_result_requires_status_snapshot_id():
    with pytest.raises(ValueError, match="status.snapshot_id is required"):
        MatchUnificationResult(status=MatchUnificationStatus.pending())


def test_match_unification_result_adapts_legacy_benchmark_object():
    result = MatchUnificationResult(
        status=MatchUnificationStatus.unified(snapshot_id="snapshot-1"),
        benchmark=MatchUnificationBenchmarkOut(
            state="pending_unification",
            mode="match_id_only",
            warnings=["legacy_warning"],
            fallback_reason="legacy fallback",
            candidate_count=4,
        ),
    )

    benchmark = result.to_benchmark_out()

    assert benchmark.candidate_count == 4
    assert benchmark.state == "unified"
    assert benchmark.mode == "resolved_event_graph"
    assert benchmark.warnings == []
    assert benchmark.fallback_reason is None


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
