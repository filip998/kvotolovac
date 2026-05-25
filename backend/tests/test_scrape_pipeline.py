from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.schemas import (
    MatchUnificationResolutionBenchmarkOut,
    ScrapeRuntimeSettings,
)
from app.services.market_allowlist import analysis_market_allowlist
from app.services.scrape_pipeline import (
    BookmakerIdentity,
    DefaultTeamRegistryActions,
    ScrapePipeline,
    ScrapePipelineInput,
    _CanonicalAnalysisResult,
)
from app.services import scrape_pipeline
from app.services.match_unification import (
    MatchUnificationResult,
    MatchUnificationStatus,
    MatchUnificationWarning,
)


class FakeBenchmark:
    def __init__(self) -> None:
        self.match_unification_metrics = []

    def record_phase_duration(self, *_args, **_kwargs):
        pass

    def record_outcome_normalization(self, *_args, **_kwargs):
        pass

    def record_auto_resolution_rerun(self, *_args, **_kwargs):
        pass

    def record_persistence(self, *_args, **_kwargs):
        pass

    def record_match_unification(self, metrics, *_args, **_kwargs):
        self.match_unification_metrics.append(metrics)

    def record_event_split_diagnostics(self, *_args, **_kwargs):
        pass

    def record_opportunity_analysis(self, *_args, **_kwargs):
        pass

    def record_phase_durations(self, *_args, **_kwargs):
        pass

    def publish(self, *_args, **_kwargs):
        pass


class FakeStore:
    def __init__(self, *, auto_approved_case_ids: list[int] | None = None) -> None:
        self.auto_approved_case_ids = auto_approved_case_ids or []
        self.cleanup_calls = 0
        self.rollback_calls = 0
        self.deleted_case_ids: list[int] = []
        self.marked_analysis_failures = 0

    async def upsert_bookmaker(self, **_kwargs):
        pass

    async def persist_scrape_snapshot_batch(self, **_kwargs):
        return {
            "seen_match_ids": [],
            "snapshot_id": "snapshot-1",
            "auto_approved_team_review_case_ids": self.auto_approved_case_ids,
        }

    async def publish_opportunities(self, **_kwargs):
        return "publish-1"

    async def mark_scrape_snapshot_analysis_failed(self, **_kwargs):
        self.marked_analysis_failures += 1

    async def cleanup_retained_data(self, *_args, **_kwargs):
        self.cleanup_calls += 1
        return {}

    async def rollback_pending_transaction(self):
        self.rollback_calls += 1

    async def delete_team_review_cases(self, case_ids, **_kwargs):
        self.deleted_case_ids.extend(case_ids)


class FakeNotificationService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.publish_ids: list[str | None] = []

    async def notify_opportunities(self, _opportunities, *, publish_id=None):
        self.publish_ids.append(publish_id)
        if self.fail:
            raise RuntimeError("notification failure")
        return 0


class FakeTeamActions:
    def __init__(
        self,
        *,
        applied_aliases: list[tuple[str, str, str]] | None = None,
        rollback_alias_failures: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self.applied_aliases = applied_aliases or []
        self.rollback_alias_failures = rollback_alias_failures or []

    async def _same_time_canonical_merge_candidates(self, _raw_rows):
        return [], []

    def _build_case_alias_requests(self, _case, _raw_rows):
        return []

    async def _auto_apply_anchored_aliases(self, _team_review_cases, _raw_rows=None):
        return [], self.applied_aliases, []

    async def _apply_canonical_merges(self, _pending_auto_merges):
        return SimpleNamespace(applied_pairings=[], display_names={})

    async def _rollback_auto_applied_aliases(self, _applied_aliases):
        return self.rollback_alias_failures

    async def _rollback_auto_applied_merges(self, _applied_auto_merges):
        return []


async def _load_empty_canonical_analysis(_store, **_kwargs):
    return _CanonicalAnalysisResult()


class FakeMatchUnification:
    def __init__(self, result: MatchUnificationResult | None = None) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def unify_after_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return self.result or MatchUnificationResult(
            status=MatchUnificationStatus.unified(snapshot_id="snapshot-1"),
        )


def _pipeline_input() -> ScrapePipelineInput:
    runtime_settings = ScrapeRuntimeSettings()
    return ScrapePipelineInput(
        raw_odds=(),
        raw_outcome_offers=(),
        bookmakers=(BookmakerIdentity(id="demo", name="Demo"),),
        runtime_settings=runtime_settings,
        market_allowlist=analysis_market_allowlist(
            runtime_settings.analysis_markets,
            legacy_scrape_market_scope=runtime_settings.scrape_market_scope,
        ),
        cycle_started_at=0.0,
        cycle_started_at_iso="2026-01-01T00:00:00",
        scrape_duration_ms=0,
    )


def _pipeline(
    *,
    store: FakeStore,
    notification_service: FakeNotificationService | None = None,
    team_actions: FakeTeamActions | None = None,
    phase_callback=None,
    benchmark: FakeBenchmark | None = None,
    match_unification: FakeMatchUnification | None = None,
) -> ScrapePipeline:
    return ScrapePipeline(
        store=store,
        benchmark=benchmark or FakeBenchmark(),
        notification_service=notification_service or FakeNotificationService(),
        team_actions=team_actions or FakeTeamActions(),
        phase_callback=phase_callback,
        load_canonical_analysis=_load_empty_canonical_analysis,
        match_unification=match_unification or FakeMatchUnification(),
    )


def test_candidate_merge_source_ids_uses_contextual_merge_ids(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        scrape_pipeline,
        "_contextual_merge_source_ids",
        lambda _case: {7},
    )

    assert scrape_pipeline._candidate_merge_source_ids(SimpleNamespace()) == {7}


@pytest.mark.asyncio
async def test_default_team_registry_actions_returns_applied_merge_result(
    monkeypatch: pytest.MonkeyPatch,
):
    teams = {
        1: SimpleNamespace(id=1, display_name="Source"),
        2: SimpleNamespace(id=2, display_name="Target"),
    }
    merged_pairings: list[tuple[int, int]] = []

    monkeypatch.setattr(
        scrape_pipeline,
        "get_canonical_team",
        lambda team_id, follow_merge=False: teams[team_id],
    )
    monkeypatch.setattr(
        scrape_pipeline,
        "merge_canonical_teams",
        lambda *, source_team_id, target_team_id, **_kwargs: merged_pairings.append(
            (source_team_id, target_team_id)
        ),
    )

    result = await DefaultTeamRegistryActions()._apply_canonical_merges([(1, 2)])

    assert result.applied_pairings == [(1, 2)]
    assert result.display_names == {(1, 2): ("Source", "Target")}
    assert merged_pairings == [(1, 2)]


@pytest.mark.asyncio
async def test_pipeline_phase_callback_failure_is_non_fatal():
    store = FakeStore()

    def failing_phase_callback(_phase):
        raise RuntimeError("phase sink failed")

    result = await _pipeline(
        store=store,
        phase_callback=failing_phase_callback,
    ).run(_pipeline_input())

    assert result["matches_scraped"] == 0
    assert store.cleanup_calls == 1


@pytest.mark.asyncio
async def test_pipeline_returns_typed_match_unification_status_and_compatible_benchmark():
    store = FakeStore()
    benchmark = FakeBenchmark()
    warning = MatchUnificationWarning(
        code="match_unification_failed",
        detail="RuntimeError: store failed",
    )
    match_unification_result = MatchUnificationResult(
        status=MatchUnificationStatus.match_id_only(
            snapshot_id="snapshot-1",
            warning=warning,
            fallback_reason=warning.detail,
        ),
        benchmark=MatchUnificationResolutionBenchmarkOut(candidate_count=4),
    )

    result = await _pipeline(
        store=store,
        benchmark=benchmark,
        match_unification=FakeMatchUnification(match_unification_result),
    ).run(_pipeline_input())

    match_unification_status = result["match_unification"]
    assert isinstance(match_unification_status, MatchUnificationStatus)
    assert match_unification_status.state == "match_id_only"
    assert match_unification_status.to_cycle_status_out().model_dump() == {
        "state": "match_id_only",
        "mode": "match_id_only",
        "warnings": ["RuntimeError: store failed"],
        "fallback_reason": "RuntimeError: store failed",
    }
    recorded = benchmark.match_unification_metrics[-1]
    assert recorded.candidate_count == 4
    assert recorded.state == "match_id_only"
    assert recorded.mode == "match_id_only"
    assert recorded.warnings == ["match_unification_failed"]
    assert recorded.fallback_reason == "RuntimeError: store failed"


@pytest.mark.asyncio
async def test_pipeline_retention_runs_only_after_successful_pipeline_work():
    store = FakeStore()

    with pytest.raises(RuntimeError, match="notification failure"):
        await _pipeline(
            store=store,
            notification_service=FakeNotificationService(fail=True),
        ).run(_pipeline_input())

    assert store.rollback_calls == 1
    assert store.cleanup_calls == 0


@pytest.mark.asyncio
async def test_pipeline_deletes_auto_approved_audit_rows_after_successful_rollback():
    store = FakeStore(auto_approved_case_ids=[42])

    with pytest.raises(RuntimeError, match="notification failure"):
        await _pipeline(
            store=store,
            notification_service=FakeNotificationService(fail=True),
            team_actions=FakeTeamActions(applied_aliases=[("demo", "Raw", "football")]),
        ).run(_pipeline_input())

    assert store.deleted_case_ids == [42]


@pytest.mark.asyncio
async def test_pipeline_keeps_auto_approved_audit_rows_after_failed_rollback():
    store = FakeStore(auto_approved_case_ids=[42])

    with pytest.raises(RuntimeError, match="notification failure"):
        await _pipeline(
            store=store,
            notification_service=FakeNotificationService(fail=True),
            team_actions=FakeTeamActions(
                applied_aliases=[("demo", "Raw", "football")],
                rollback_alias_failures=[("demo", "Raw", "football")],
            ),
        ).run(_pipeline_input())

    assert store.deleted_case_ids == []


@pytest.mark.asyncio
async def test_pipeline_discards_cached_event_resolutions_after_skipped_registry_change():
    store = FakeStore()
    match_unification = FakeMatchUnification()

    await _pipeline(
        store=store,
        team_actions=FakeTeamActions(applied_aliases=[("demo", "Raw", "football")]),
        match_unification=match_unification,
    ).run(_pipeline_input())

    rows = match_unification.calls[-1]["rows"]
    assert rows.football_event_resolutions is None
