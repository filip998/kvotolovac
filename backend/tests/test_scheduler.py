from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.schemas import NormalizedOdds, RawOddsData, TeamReviewDiagnostic
from app.scrapers.base import BaseScraper
from app.services.scheduler import Scheduler, _normalize_merge_pairings
from app.services.normalizer import normalize_team_name
from app.services.notifications import InAppNotificationProvider
from app.services.team_registry import (
    create_canonical_team,
    get_canonical_team,
    remember_team_alias,
    resolve_team_alias,
)
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.store import odds_store


_UNSET = object()


def _raw_odds(
    bookmaker_id: str,
    threshold: float,
    *,
    over_odds: float = 1.9,
    under_odds: float = 1.9,
) -> RawOddsData:
    return RawOddsData(
        bookmaker_id=bookmaker_id,
        league_id="euroleague",
        home_team="Olympiacos",
        away_team="Real Madrid",
        market_type="player_points",
        player_name="Sasha Vezenkov",
        threshold=threshold,
        over_odds=over_odds,
        under_odds=under_odds,
        start_time="2030-01-01T20:00:00",
    )


def _anchored_team_raw(
    bookmaker_id: str,
    home_team: str,
    *,
    away_team: str = "Levski Sofia",
    league_id: str = "Bulgarian NBL",
) -> RawOddsData:
    return RawOddsData(
        bookmaker_id=bookmaker_id,
        league_id=league_id,
        home_team=home_team,
        away_team=away_team,
        market_type="game_total",
        threshold=161.5,
        over_odds=1.85,
        under_odds=1.95,
        start_time="2030-01-01T20:00:00+00:00",
    )


def _register_test_scrapers(*scrapers: BaseScraper) -> None:
    registry._scrapers.clear()
    for scraper in scrapers:
        registry.register(scraper)


class StubScraper(BaseScraper):
    def __init__(
        self,
        bookmaker_id: str,
        *,
        bookmaker_name: str | None = None,
        leagues: tuple[str, ...] = ("euroleague",),
        delay: float = 0.0,
        should_raise: bool = False,
        malformed_return: object = _UNSET,
        recorder: dict | None = None,
        payload_by_league: dict[str, list[RawOddsData]] | None = None,
    ) -> None:
        self._bookmaker_id = bookmaker_id
        self._bookmaker_name = bookmaker_name or bookmaker_id.title()
        self._leagues = list(leagues)
        self._delay = delay
        self._should_raise = should_raise
        self._malformed_return = malformed_return
        self._recorder = recorder
        self._payload_by_league = payload_by_league or {}

    def get_bookmaker_id(self) -> str:
        return self._bookmaker_id

    def get_bookmaker_name(self) -> str:
        return self._bookmaker_name

    def get_supported_leagues(self) -> list[str]:
        return list(self._leagues)

    async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
        if self._recorder is not None:
            self._recorder["starts"].append((self._bookmaker_id, league_id))
            self._recorder["active"] += 1
            self._recorder["max_active"] = max(
                self._recorder["max_active"], self._recorder["active"]
            )

        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            if self._should_raise:
                raise RuntimeError(f"{self._bookmaker_id} failed")
            if self._malformed_return is not _UNSET:
                return self._malformed_return
            return list(self._payload_by_league.get(league_id, []))
        finally:
            if self._recorder is not None:
                self._recorder["active"] -= 1
                self._recorder["finishes"].append((self._bookmaker_id, league_id))


def test_normalize_merge_pairings_flattens_valid_chains():
    normalized, conflicts = _normalize_merge_pairings([(3, 2), (2, 1)])

    assert normalized == {2: 1, 3: 1}
    assert conflicts == set()


def test_normalize_merge_pairings_rejects_reciprocal_cycles_regardless_of_order():
    normalized_forward, conflicts_forward = _normalize_merge_pairings([(1, 2), (2, 1)])
    normalized_reverse, conflicts_reverse = _normalize_merge_pairings([(2, 1), (1, 2)])

    assert normalized_forward == {}
    assert normalized_reverse == {}
    assert conflicts_forward == {1, 2}
    assert conflicts_reverse == {1, 2}


@pytest.fixture(autouse=True)
def register_scrapers():
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian", "maxbet"):
        registry.register(MockScraper(bm))
    yield
    registry._scrapers.clear()


@pytest.mark.asyncio
async def test_scheduler_config():
    s = Scheduler(interval_minutes=5)
    assert s.interval_minutes == 5
    assert not s.is_running


@pytest.mark.asyncio
async def test_scheduler_run_cycle():
    s = Scheduler(interval_minutes=1)
    result = await s.run_cycle()
    assert result["matches_scraped"] > 0
    assert result["odds_scraped"] > 0
    assert result["discrepancies_found"] > 0
    assert "notifications_sent" in result
    assert isinstance(result["notifications_sent"], int)


@pytest.mark.asyncio
async def test_scheduler_run_cycle_overlaps_scraper_tasks():
    recorder = {"active": 0, "max_active": 0, "starts": [], "finishes": []}
    _register_test_scrapers(
        StubScraper("alpha", delay=0.02, recorder=recorder),
        StubScraper("beta", delay=0.02, recorder=recorder),
        StubScraper("gamma", delay=0.02, recorder=recorder),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert recorder["max_active"] > 1
    assert len(recorder["starts"]) == 3
    assert len(recorder["finishes"]) == 3
    assert result["matches_scraped"] == 0
    assert result["odds_scraped"] == 0
    assert result["discrepancies_found"] == 0


@pytest.mark.asyncio
async def test_scheduler_progress_snapshot_updates_while_cycle_runs():
    _register_test_scrapers(
        StubScraper("alpha", delay=0.05, payload_by_league={"euroleague": [_raw_odds("alpha", 18.5)]}),
        StubScraper("beta", delay=0.05, payload_by_league={"euroleague": [_raw_odds("beta", 20.5)]}),
    )

    scheduler_under_test = Scheduler(interval_minutes=1)
    cycle_task = asyncio.create_task(scheduler_under_test.run_cycle())
    await asyncio.sleep(0.01)

    snapshot = scheduler_under_test.progress_snapshot()

    assert snapshot.in_progress is True
    assert snapshot.phase == "scraping"
    assert snapshot.total_tasks == 2
    assert snapshot.active_tasks > 0
    assert snapshot.started_at is not None

    await cycle_task
    assert scheduler_under_test.progress_snapshot().in_progress is False


@pytest.mark.asyncio
async def test_scheduler_run_cycle_joins_inflight_cycle():
    recorder = {"active": 0, "max_active": 0, "starts": [], "finishes": []}
    _register_test_scrapers(
        StubScraper(
            "alpha",
            delay=0.05,
            recorder=recorder,
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5)]},
        ),
        StubScraper(
            "beta",
            delay=0.05,
            recorder=recorder,
            payload_by_league={"euroleague": [_raw_odds("beta", 20.5)]},
        ),
    )

    scheduler_under_test = Scheduler(interval_minutes=1)
    first = asyncio.create_task(scheduler_under_test.run_cycle())
    await asyncio.sleep(0.01)
    second_result = await scheduler_under_test.run_cycle()
    first_result = await first

    assert first_result == second_result
    assert len(recorder["starts"]) == 2
    assert len(recorder["finishes"]) == 2


@pytest.mark.asyncio
async def test_scheduler_run_cycle_isolates_scraper_failures():
    _register_test_scrapers(
        StubScraper(
            "alpha",
            delay=0.01,
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5, over_odds=1.92)]},
        ),
        StubScraper("broken", delay=0.01, should_raise=True),
        StubScraper(
            "beta",
            delay=0.01,
            payload_by_league={
                "euroleague": [_raw_odds("beta", 20.5, under_odds=1.96)]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 2
    assert result["discrepancies_found"] == 1
    assert result["notifications_sent"] == 0


@pytest.mark.asyncio
async def test_scheduler_run_cycle_isolates_malformed_scraper_returns():
    _register_test_scrapers(
        StubScraper(
            "alpha",
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5, over_odds=1.92)]},
        ),
        StubScraper("broken", malformed_return=None),
        StubScraper(
            "beta",
            payload_by_league={
                "euroleague": [_raw_odds("beta", 20.5, under_odds=1.96)]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 2
    assert result["discrepancies_found"] == 1
    assert result["notifications_sent"] == 0


@pytest.mark.asyncio
async def test_scheduler_run_cycle_isolates_malformed_scraper_items():
    _register_test_scrapers(
        StubScraper(
            "alpha",
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5, over_odds=1.92)]},
        ),
        StubScraper("broken", malformed_return=[None]),
        StubScraper(
            "beta",
            payload_by_league={
                "euroleague": [_raw_odds("beta", 20.5, under_odds=1.96)]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 2
    assert result["discrepancies_found"] == 1
    assert result["notifications_sent"] == 0


@pytest.mark.asyncio
async def test_scheduler_run_cycle_filters_odds_beyond_configured_lookahead(
    monkeypatch,
):
    fixed_now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.config.settings.scrape_lookahead_hours", 24)
    monkeypatch.setattr("app.services.scrape_window.current_utc_time", lambda: fixed_now)
    _register_test_scrapers(
        StubScraper(
            "alpha",
            payload_by_league={
                "euroleague": [
                    RawOddsData(
                        bookmaker_id="alpha",
                        league_id="euroleague",
                        home_team="Olympiacos",
                        away_team="Real Madrid",
                        market_type="player_points",
                        player_name="Sasha Vezenkov",
                        threshold=18.5,
                        over_odds=1.9,
                        under_odds=1.9,
                        start_time=(fixed_now + timedelta(hours=23)).isoformat(),
                    ),
                    RawOddsData(
                        bookmaker_id="alpha",
                        league_id="euroleague",
                        home_team="Olympiacos",
                        away_team="Real Madrid",
                        market_type="player_points",
                        player_name="Sasha Vezenkov",
                        threshold=20.5,
                        over_odds=1.9,
                        under_odds=1.9,
                        start_time=(fixed_now + timedelta(hours=24)).isoformat(),
                    ),
                    RawOddsData(
                        bookmaker_id="alpha",
                        league_id="euroleague",
                        home_team="Olympiacos",
                        away_team="Real Madrid",
                        market_type="player_points",
                        player_name="Sasha Vezenkov",
                        threshold=22.5,
                        over_odds=1.9,
                        under_odds=1.9,
                        start_time=(fixed_now + timedelta(hours=25)).isoformat(),
                    ),
                ]
            },
        )
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["matches_scraped"] == 2
    assert result["odds_scraped"] == 2


@pytest.mark.asyncio
async def test_scheduler_run_cycle_returns_expected_output_shape():
    result = await Scheduler(interval_minutes=1).run_cycle()

    assert {
        "matches_scraped",
        "odds_scraped",
        "discrepancies_found",
        "notifications_sent",
    } <= result.keys()
    for key in (
        "matches_scraped",
        "odds_scraped",
        "discrepancies_found",
        "notifications_sent",
    ):
        assert isinstance(result[key], int)
        assert result[key] >= 0


@pytest.mark.asyncio
async def test_scheduler_run_cycle_calls_retention_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    cleanup_calls: list[str] = []

    async def fake_cleanup(snapshot_at: str) -> dict[str, int]:
        cleanup_calls.append(snapshot_at)
        return {
            "deleted_stale_odds": 0,
            "deleted_stale_unresolved_odds": 0,
            "deleted_inactive_discrepancies": 0,
            "deleted_odds_history": 0,
            "deleted_team_review_cases": 0,
            "deleted_notifications": 0,
        }

    monkeypatch.setattr(odds_store, "cleanup_retained_data", fake_cleanup)

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["matches_scraped"] > 0
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]


@pytest.mark.asyncio
async def test_scheduler_skips_in_app_notification_provider_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    send_calls = 0

    async def fake_send(self, type: str, title: str, message: str, data=None) -> None:
        nonlocal send_calls
        send_calls += 1

    monkeypatch.setattr(settings, "persist_inapp_notifications", False)
    monkeypatch.setattr(InAppNotificationProvider, "send", fake_send)

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["notifications_sent"] == 0
    assert send_calls == 0


@pytest.mark.asyncio
async def test_scheduler_uses_in_app_notification_provider_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    send_calls = 0

    async def fake_send(self, type: str, title: str, message: str, data=None) -> None:
        nonlocal send_calls
        send_calls += 1

    monkeypatch.setattr(settings, "persist_inapp_notifications", True)
    monkeypatch.setattr(InAppNotificationProvider, "send", fake_send)

    result = await Scheduler(interval_minutes=1).run_cycle()

    assert result["notifications_sent"] > 0
    assert send_calls == result["notifications_sent"]


@pytest.mark.asyncio
async def test_scheduler_cleanup_failure_does_not_roll_back_auto_saved_alias(
    monkeypatch: pytest.MonkeyPatch,
):
    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={"euroleague": [_anchored_team_raw("mozzart", "Rilski Sportist")]},
        ),
        StubScraper(
            "meridian",
            payload_by_league={"euroleague": [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")]},
        ),
    )

    await Scheduler(interval_minutes=1).run_cycle()

    async def failing_cleanup(snapshot_at: str) -> dict[str, int]:
        raise RuntimeError("simulated cleanup failure")

    monkeypatch.setattr(odds_store, "cleanup_retained_data", failing_cleanup)

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 2
    assert approved_cases == []
    assert (
        normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian")
        == "Rilski Sportist"
    )


@pytest.mark.asyncio
async def test_scheduler_run_cycle_persists_unresolved_odds():
    _register_test_scrapers(
        StubScraper(
            "admiralbet",
            payload_by_league={
                "euroleague": [
                    RawOddsData(
                        bookmaker_id="admiralbet",
                        league_id="aba_liga",
                        home_team="Borac Cacak",
                        away_team="P. Nikolic",
                        market_type="player_points",
                        player_name="P. Nikolic",
                        threshold=10.5,
                        over_odds=1.8,
                        under_odds=2.0,
                        start_time="2030-01-01T20:00:00+00:00",
                    )
                ]
            },
        )
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    unresolved = await odds_store.get_unresolved_odds()

    assert result["matches_scraped"] == 0
    assert result["odds_scraped"] == 0
    assert len(unresolved) == 1
    assert unresolved[0].reason_code == "no_canonical_matchup_for_team_at_slot"
    assert unresolved[0].raw_team_name == "Borac Cacak"
@pytest.mark.asyncio
async def test_scheduler_run_cycle_hides_stale_matches_from_latest_snapshot():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="stale",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="2026-04-10T13:39:04.516801",
    )

    _register_test_scrapers(
        StubScraper(
            "meridian",
            payload_by_league={
                "euroleague": [
                    RawOddsData(
                        bookmaker_id="meridian",
                        league_id="euroleague",
                        home_team="Maccabi Tel Aviv",
                        away_team="Hapoel Tel-Aviv",
                        market_type="player_points",
                        player_name="Tamir Blatt",
                        threshold=6.5,
                        over_odds=2.09,
                        under_odds=1.66,
                        start_time="2030-01-01T20:00:00+00:00",
                    )
                ]
            },
        )
    )

    await Scheduler(interval_minutes=1).run_cycle()

    matches = await odds_store.get_matches()

    assert len(matches) == 1
    assert matches[0].home_team == "Maccabi Tel Aviv"
    assert matches[0].away_team == "Hapoel Tel-Aviv"


@pytest.mark.asyncio
async def test_scheduler_run_cycle_advances_snapshot_when_cycle_is_empty():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="stale",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="2026-04-10T13:39:04.516801",
    )

    _register_test_scrapers(StubScraper("meridian", payload_by_league={"euroleague": []}))

    await Scheduler(interval_minutes=1).run_cycle()

    matches = await odds_store.get_matches()
    status = await odds_store.get_system_status()

    assert matches == []
    assert status.total_matches == 0
    assert status.total_odds == 0


@pytest.mark.asyncio
async def test_scheduler_run_cycle_keeps_previous_snapshot_if_store_fails_mid_batch(
    monkeypatch: pytest.MonkeyPatch,
):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("old", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="2026-04-10T13:39:04.516801",
    )
    await odds_store.set_current_snapshot("2026-04-10T13:39:04.516801")

    original_upsert_odds = odds_store.upsert_odds
    call_count = 0

    async def failing_upsert_odds(odds, *, scraped_at):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated store failure")
        return await original_upsert_odds(odds, scraped_at=scraped_at)

    monkeypatch.setattr(odds_store, "upsert_odds", failing_upsert_odds)

    _register_test_scrapers(
        StubScraper(
            "meridian",
            payload_by_league={
                "euroleague": [
                    RawOddsData(
                        bookmaker_id="meridian",
                        league_id="euroleague",
                        home_team="Maccabi Tel Aviv",
                        away_team="Hapoel Tel-Aviv",
                        market_type="player_points",
                        player_name="Tamir Blatt",
                        threshold=6.5,
                        over_odds=2.09,
                        under_odds=1.66,
                        start_time="2030-01-01T20:00:00+00:00",
                    ),
                    RawOddsData(
                        bookmaker_id="meridian",
                        league_id="euroleague",
                        home_team="Partizan",
                        away_team="Crvena Zvezda",
                        market_type="player_points",
                        player_name="Iffe Lundberg",
                        threshold=16.5,
                        over_odds=1.85,
                        under_odds=1.95,
                        start_time="2030-01-01T21:00:00+00:00",
                    ),
                ]
            },
        )
    )

    with pytest.raises(RuntimeError, match="simulated store failure"):
        await Scheduler(interval_minutes=1).run_cycle()

    matches = await odds_store.get_matches(limit=10)
    status = await odds_store.get_system_status()

    assert [match.id for match in matches] == ["old"]
    assert status.last_scrape_at == "2026-04-10T13:39:04.516801"


@pytest.mark.asyncio
async def test_scheduler_run_cycle_auto_saves_anchored_alias_same_scrape():
    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={"euroleague": [_anchored_team_raw("mozzart", "Rilski Sportist")]},
        ),
        StubScraper(
            "meridian",
            payload_by_league={"euroleague": [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")]},
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 2
    assert len(approved_cases) == 1
    assert approved_cases[0].review_kind == "auto_alias_suggestion"
    assert approved_cases[0].status == "approved"
    assert (
        normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian")
        == "Rilski Sportist"
    )


@pytest.mark.asyncio
async def test_scheduler_run_cycle_auto_saves_multiple_anchored_aliases_same_scrape():
    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={"euroleague": [_anchored_team_raw("mozzart", "Rilski Sportist")]},
        ),
        StubScraper(
            "meridian",
            payload_by_league={"euroleague": [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")]},
        ),
        StubScraper(
            "maxbet",
            payload_by_league={"euroleague": [_anchored_team_raw("maxbet", "Rilski Sport.", league_id="NBL")]},
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 3
    assert len(approved_cases) == 2
    assert {case.bookmaker_id for case in approved_cases} == {"meridian", "maxbet"}
    assert {case.review_kind for case in approved_cases} == {"auto_alias_suggestion"}


@pytest.mark.asyncio
async def test_scheduler_run_cycle_auto_merges_weaker_existing_canonical_team():
    source = create_canonical_team(display_name="NY Knicks")
    target = create_canonical_team(display_name="New York Knicks")

    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={
                "euroleague": [
                    _anchored_team_raw(
                        "mozzart",
                        "Atlanta Hawks",
                        away_team="New York Knicks",
                        league_id="nba",
                    )
                ]
            },
        ),
        StubScraper(
            "meridian",
            payload_by_league={
                "euroleague": [
                    _anchored_team_raw(
                        "meridian",
                        "Atlanta Hawks",
                        away_team="New York Knicks",
                        league_id="nba",
                    )
                ]
            },
        ),
        StubScraper(
            "pinnbet",
            payload_by_league={
                "euroleague": [
                    _anchored_team_raw(
                        "pinnbet",
                        "Atlanta Hawks",
                        away_team="NY Knicks",
                        league_id="nba",
                    )
                ]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")
    merged_source = get_canonical_team(source.team_id, follow_merge=True)

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 3
    assert len(approved_cases) == 1
    assert approved_cases[0].bookmaker_id == "pinnbet"
    assert approved_cases[0].raw_team_name == "NY Knicks"
    assert approved_cases[0].review_kind == "auto_alias_suggestion"
    assert approved_cases[0].suggested_team_id == target.team_id
    assert {candidate.team_id for candidate in approved_cases[0].candidate_teams} == {
        source.team_id,
        target.team_id,
    }
    assert normalize_team_name("NY Knicks", "nba", "pinnbet") == target.team_name
    assert get_canonical_team(source.team_id) is None
    assert merged_source is not None
    assert merged_source.id == target.team_id
    assert "NY Knicks" in merged_source.aliases


@pytest.mark.asyncio
async def test_scheduler_run_cycle_auto_merges_same_time_both_sides_when_strong_and_unambiguous():
    source_home = create_canonical_team(display_name="Novosibirsk")
    source_away = create_canonical_team(display_name="Chelyabinsk")
    target_home = create_canonical_team(display_name="BC Novosibirsk")
    target_away = create_canonical_team(display_name="BC Chelyabinsk")

    _register_test_scrapers(
        StubScraper(
            "book-a",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-a",
                        "BC Novosibirsk",
                        away_team="BC Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-b",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-b",
                        "BC Novosibirsk",
                        away_team="BC Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-c",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-c",
                        "Novosibirsk",
                        away_team="Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 1
    assert result["odds_scraped"] == 3
    assert {case.review_kind for case in approved_cases} == {
        "auto_canonical_merge_suggestion"
    }
    assert get_canonical_team(source_home.team_id) is None
    assert get_canonical_team(source_away.team_id) is None
    assert get_canonical_team(source_home.team_id, follow_merge=True).id == target_home.team_id
    assert get_canonical_team(source_away.team_id, follow_merge=True).id == target_away.team_id


@pytest.mark.asyncio
async def test_scheduler_run_cycle_skips_same_time_both_sides_auto_merge_when_compound_side_is_ambiguous():
    source_home = create_canonical_team(display_name="Novosibirsk")
    source_away = create_canonical_team(display_name="Chelyabinsk")
    target_home = create_canonical_team(display_name="BC Novosibirsk")
    target_away = create_canonical_team(display_name="BC Chelbasket Chelyabinsk")
    extra_away = create_canonical_team(display_name="Chelbasket")

    _register_test_scrapers(
        StubScraper(
            "book-a",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-a",
                        "BC Novosibirsk",
                        away_team="BC Chelbasket Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-b",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-b",
                        "BC Novosibirsk",
                        away_team="BC Chelbasket Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-c",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-c",
                        "Novosibirsk",
                        away_team="Chelyabinsk",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-d",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-d",
                        "BC Novosibirsk",
                        away_team="Chelbasket",
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 3
    assert approved_cases == []
    assert get_canonical_team(source_home.team_id) is not None
    assert get_canonical_team(source_away.team_id) is not None
    assert get_canonical_team(target_home.team_id) is not None
    assert get_canonical_team(target_away.team_id) is not None
    assert get_canonical_team(extra_away.team_id) is not None


@pytest.mark.asyncio
async def test_scheduler_run_cycle_skips_same_time_auto_merge_for_b_team_subset_names():
    senior_home = create_canonical_team(display_name="Real Madrid")
    senior_away = create_canonical_team(display_name="Barcelona")
    reserve_home = create_canonical_team(display_name="Real Madrid B")
    reserve_away = create_canonical_team(display_name="Barcelona B")

    _register_test_scrapers(
        StubScraper(
            "book-a",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-a",
                        senior_home.team_name,
                        away_team=senior_away.team_name,
                        league_id="ACB",
                    )
                ]
            },
        ),
        StubScraper(
            "book-b",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-b",
                        senior_home.team_name,
                        away_team=senior_away.team_name,
                        league_id="ACB",
                    )
                ]
            },
        ),
        StubScraper(
            "book-c",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-c",
                        reserve_home.team_name,
                        away_team=reserve_away.team_name,
                        league_id="ACB",
                    )
                ]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["matches_scraped"] == 2
    assert approved_cases == []
    assert get_canonical_team(reserve_home.team_id) is not None
    assert get_canonical_team(reserve_away.team_id) is not None


@pytest.mark.asyncio
async def test_auto_apply_anchored_aliases_skips_declined_history(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler = Scheduler(interval_minutes=1)

    async def declined_history_summary(**kwargs):
        return set(), True

    monkeypatch.setattr(
        odds_store,
        "get_team_review_case_history_summary",
        declined_history_summary,
    )

    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            sport="basketball",
            raw_team_name="Rilski Sport.",
            normalized_raw_team_name="rilski sport",
            suggested_team_id=101,
            suggested_team_name="Rilski Sportist",
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=95,
            matched_counterpart_team="Levski Sofia",
            canonical_home_team="Rilski Sportist",
            canonical_away_team="Levski Sofia",
        )
    ]

    approved_cases, applied_aliases, pending_merge_pairings = await scheduler._auto_apply_anchored_aliases(cases)

    assert approved_cases == []
    assert applied_aliases == []
    assert pending_merge_pairings == []
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sport."


@pytest.mark.asyncio
async def test_auto_apply_anchored_aliases_respects_threshold():
    scheduler = Scheduler(interval_minutes=1)
    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            sport="basketball",
            raw_team_name="Rilski Sport.",
            normalized_raw_team_name="rilski sport",
            suggested_team_id=101,
            suggested_team_name="Rilski Sportist",
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="candidate_search",
            reason_code="candidate_team_match_same_start_time",
            confidence="medium",
            similarity_score=84,
            matched_counterpart_team="Levski Sofia",
            canonical_home_team="Rilski Sportist",
            canonical_away_team="Levski Sofia",
        )
    ]

    approved_cases, applied_aliases, pending_merge_pairings = await scheduler._auto_apply_anchored_aliases(cases)

    assert approved_cases == []
    assert applied_aliases == []
    assert pending_merge_pairings == []
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sport."


@pytest.mark.asyncio
async def test_auto_apply_contextual_merge_uses_lower_threshold_for_same_event():
    winner = create_canonical_team(display_name="CSKA Moscow")
    runner_up = create_canonical_team(display_name="CSKA Moskva")
    opponent = create_canonical_team(display_name="Enisey")
    scheduler = Scheduler(interval_minutes=1)
    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="superbet",
            raw_league_id="VTB Liga",
            normalized_raw_league_id="vtb liga",
            sport="basketball",
            raw_team_name=runner_up.team_name,
            normalized_raw_team_name="cska moskva",
            suggested_team_id=winner.team_id,
            suggested_team_name=winner.team_name,
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=84.21052631578947,
            matched_counterpart_team=opponent.team_name,
            canonical_home_team=winner.team_name,
            canonical_away_team=opponent.team_name,
            candidate_teams=[
                {
                    "team_id": winner.team_id,
                    "team_name": winner.team_name,
                    "score": 84.21052631578947,
                    "slot_support": 11,
                    "canonical_home_team": winner.team_name,
                    "canonical_away_team": opponent.team_name,
                },
                {
                    "team_id": runner_up.team_id,
                    "team_name": runner_up.team_name,
                    "score": 84.21052631578947,
                    "slot_support": 2,
                    "canonical_home_team": runner_up.team_name,
                    "canonical_away_team": opponent.team_name,
                },
            ],
        )
    ]

    approved_cases, applied_aliases, pending_merge_pairings = await scheduler._auto_apply_anchored_aliases(cases)

    assert applied_aliases == []
    assert pending_merge_pairings == [(runner_up.team_id, winner.team_id)]
    assert len(approved_cases) == 1
    assert approved_cases[0].review_kind == "auto_canonical_merge_suggestion"
    assert approved_cases[0].status == "approved"

    applied_pairings = await scheduler._apply_canonical_merges(pending_merge_pairings)
    merged_runner_up = get_canonical_team(runner_up.team_id, follow_merge=True)

    assert applied_pairings == [(runner_up.team_id, winner.team_id)]
    assert get_canonical_team(runner_up.team_id) is None
    assert merged_runner_up is not None
    assert merged_runner_up.id == winner.team_id
    assert normalize_team_name("CSKA Moskva") == winner.team_name


@pytest.mark.asyncio
async def test_auto_apply_contextual_merge_keeps_low_score_pending():
    winner = create_canonical_team(display_name="QA Context Winner")
    runner_up = create_canonical_team(display_name="QA Random Runner")
    opponent = create_canonical_team(display_name="QA Context Opponent")
    scheduler = Scheduler(interval_minutes=1)
    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="superbet",
            raw_league_id="VTB Liga",
            normalized_raw_league_id="vtb liga",
            sport="basketball",
            raw_team_name=runner_up.team_name,
            normalized_raw_team_name="qa random runner",
            suggested_team_id=winner.team_id,
            suggested_team_name=winner.team_name,
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="medium",
            similarity_score=79,
            matched_counterpart_team=opponent.team_name,
            canonical_home_team=winner.team_name,
            canonical_away_team=opponent.team_name,
            candidate_teams=[
                {
                    "team_id": winner.team_id,
                    "team_name": winner.team_name,
                    "score": 79,
                    "slot_support": 11,
                    "canonical_home_team": winner.team_name,
                    "canonical_away_team": opponent.team_name,
                },
                {
                    "team_id": runner_up.team_id,
                    "team_name": runner_up.team_name,
                    "score": 79,
                    "slot_support": 2,
                    "canonical_home_team": runner_up.team_name,
                    "canonical_away_team": opponent.team_name,
                },
            ],
        )
    ]

    approved_cases, applied_aliases, pending_merge_pairings = await scheduler._auto_apply_anchored_aliases(cases)

    assert approved_cases == []
    assert applied_aliases == []
    assert pending_merge_pairings == []
    assert get_canonical_team(runner_up.team_id) is not None


@pytest.mark.asyncio
async def test_scheduler_run_cycle_applies_contextual_merge_before_storage(caplog):
    caplog.set_level("WARNING", logger="app.services.normalizer")
    winner = create_canonical_team(display_name="CSKA Moscow")
    runner_up = create_canonical_team(display_name="CSKA Moskva")
    opponent = create_canonical_team(display_name="Enisey")
    _register_test_scrapers(
        StubScraper(
            "book-a",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-a",
                        winner.team_name,
                        away_team=opponent.team_name,
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-b",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-b",
                        winner.team_name,
                        away_team=opponent.team_name,
                        league_id="VTB Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "superbet",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "superbet",
                        runner_up.team_name,
                        away_team=opponent.team_name,
                        league_id="VTB Liga",
                    ),
                    RawOddsData(
                        bookmaker_id="superbet",
                        league_id="VTB Liga",
                        home_team=runner_up.team_name,
                        away_team="D.Artis",
                        market_type="player_points",
                        player_name="D.Artis",
                        threshold=11.5,
                        over_odds=1.8,
                        under_odds=1.9,
                        start_time="2030-01-01T20:00:00+00:00",
                    ),
                ]
            },
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()
    pending_cases = await odds_store.get_team_review_cases(status="pending")
    approved_cases = await odds_store.get_team_review_cases(status="approved")
    merged_runner_up = get_canonical_team(runner_up.team_id, follow_merge=True)

    assert result["matches_scraped"] == 1
    assert pending_cases == []
    assert len(approved_cases) == 1
    assert approved_cases[0].review_kind == "auto_canonical_merge_suggestion"
    assert get_canonical_team(runner_up.team_id) is None
    assert merged_runner_up is not None
    assert merged_runner_up.id == winner.team_id
    assert "Dropping" not in caplog.text


@pytest.mark.asyncio
async def test_auto_apply_anchored_aliases_overrides_losing_label_and_merges_canonical_team():
    winner = create_canonical_team(display_name="QA Winner Team")
    runner_up = create_canonical_team(display_name="QA Runner Team")
    opponent = create_canonical_team(display_name="QA Opponent Team")
    remember_team_alias(
        bookmaker_id="book-a",
        raw_team_name="QA Winner Label",
        team_name=winner.team_name,
    )
    remember_team_alias(
        bookmaker_id="book-a",
        raw_team_name="QA Opponent Label",
        team_name=opponent.team_name,
    )
    remember_team_alias(
        bookmaker_id="book-b",
        raw_team_name="QA Opponent Label",
        team_name=opponent.team_name,
    )
    remember_team_alias(
        bookmaker_id="book-c",
        raw_team_name="QA Opponent Label",
        team_name=opponent.team_name,
    )

    scheduler = Scheduler(interval_minutes=1)
    raw_rows = [
        _anchored_team_raw("book-a", "QA Winner Label", away_team="QA Opponent Label"),
        _anchored_team_raw("book-b", runner_up.team_name, away_team="QA Opponent Label"),
        _anchored_team_raw("book-c", "QA Fresh Label", away_team="QA Opponent Label"),
    ]
    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="book-c",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            sport="basketball",
            raw_team_name="QA Fresh Label",
            normalized_raw_team_name="qa fresh label",
            suggested_team_id=winner.team_id,
            suggested_team_name=winner.team_name,
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="candidate_search",
            reason_code="candidate_team_match_same_start_time",
            confidence="medium",
            similarity_score=100,
            matched_counterpart_team=opponent.team_name,
            canonical_home_team=winner.team_name,
            canonical_away_team=opponent.team_name,
            candidate_teams=[
                {
                    "team_id": winner.team_id,
                    "team_name": winner.team_name,
                    "score": 100,
                    "slot_support": 1,
                    "canonical_home_team": winner.team_name,
                    "canonical_away_team": opponent.team_name,
                },
                {
                    "team_id": runner_up.team_id,
                    "team_name": runner_up.team_name,
                    "score": 95,
                    "slot_support": 1,
                    "canonical_home_team": runner_up.team_name,
                    "canonical_away_team": opponent.team_name,
                },
            ],
        )
    ]

    approved_cases, applied_aliases, pending_merge_pairings = await scheduler._auto_apply_anchored_aliases(
        cases,
        raw_rows,
    )

    assert len(approved_cases) == 1
    assert approved_cases[0].review_kind == "auto_alias_suggestion"
    assert set(applied_aliases) == {
        ("book-c", "QA Fresh Label", "basketball"),
        ("book-b", runner_up.team_name, "basketball"),
    }
    assert pending_merge_pairings == [(runner_up.team_id, winner.team_id)]
    assert normalize_team_name("QA Fresh Label", "NBL", "book-c") == winner.team_name
    assert normalize_team_name(runner_up.team_name, "NBL", "book-b") == winner.team_name

    await scheduler._apply_canonical_merges(pending_merge_pairings)

    assert normalize_team_name("QA Runner Team", "NBL", "book-b") == winner.team_name


@pytest.mark.asyncio
async def test_scheduler_run_cycle_rolls_back_auto_saved_alias_if_store_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={"euroleague": [_anchored_team_raw("mozzart", "Rilski Sportist")]},
        ),
        StubScraper(
            "meridian",
            payload_by_league={"euroleague": [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")]},
        ),
    )

    original_upsert_odds = odds_store.upsert_odds
    call_count = 0

    async def failing_upsert_odds(odds, *, scraped_at):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated store failure")
        return await original_upsert_odds(odds, scraped_at=scraped_at)

    monkeypatch.setattr(odds_store, "upsert_odds", failing_upsert_odds)

    with pytest.raises(RuntimeError, match="simulated store failure"):
        await Scheduler(interval_minutes=1).run_cycle()

    pending_cases = await odds_store.get_team_review_cases(status="pending")
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert pending_cases == []
    assert approved_cases == []
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sport."


@pytest.mark.asyncio
async def test_scheduler_run_cycle_rolls_back_auto_merge_if_store_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    source_home = create_canonical_team(display_name="QA Rollback City")
    source_away = create_canonical_team(display_name="QA Rollback United")
    target_home = create_canonical_team(display_name="BC QA Rollback City")
    target_away = create_canonical_team(display_name="BC QA Rollback United")

    _register_test_scrapers(
        StubScraper(
            "book-a",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-a",
                        target_home.team_name,
                        away_team=target_away.team_name,
                        league_id="Rollback Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-b",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-b",
                        target_home.team_name,
                        away_team=target_away.team_name,
                        league_id="Rollback Liga",
                    )
                ]
            },
        ),
        StubScraper(
            "book-c",
            leagues=("basketball",),
            payload_by_league={
                "basketball": [
                    _anchored_team_raw(
                        "book-c",
                        source_home.team_name,
                        away_team=source_away.team_name,
                        league_id="Rollback Liga",
                    )
                ]
            },
        ),
    )

    original_upsert_odds = odds_store.upsert_odds
    call_count = 0

    async def failing_upsert_odds(odds, *, scraped_at):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated store failure")
        return await original_upsert_odds(odds, scraped_at=scraped_at)

    monkeypatch.setattr(odds_store, "upsert_odds", failing_upsert_odds)

    with pytest.raises(RuntimeError, match="simulated store failure"):
        await Scheduler(interval_minutes=1).run_cycle()

    assert get_canonical_team(source_home.team_id) is not None
    assert get_canonical_team(source_away.team_id) is not None
    assert get_canonical_team(target_home.team_id) is not None
    assert get_canonical_team(target_away.team_id) is not None


@pytest.mark.asyncio
async def test_scheduler_run_cycle_ignores_unsnapshotted_review_history_for_auto_approval(
    monkeypatch: pytest.MonkeyPatch,
):
    _register_test_scrapers(
        StubScraper(
            "mozzart",
            payload_by_league={"euroleague": [_anchored_team_raw("mozzart", "Rilski Sportist")]},
        ),
        StubScraper(
            "meridian",
            payload_by_league={"euroleague": [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")]},
        ),
    )
    await odds_store.set_current_snapshot("2020-01-01T00:00:00+00:00")

    original_set_current_snapshot = odds_store.set_current_snapshot
    call_count = 0

    async def flaky_set_current_snapshot(snapshot_at: str) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated snapshot failure")
        await original_set_current_snapshot(snapshot_at)

    monkeypatch.setattr(odds_store, "set_current_snapshot", flaky_set_current_snapshot)

    with pytest.raises(RuntimeError, match="simulated snapshot failure"):
        await Scheduler(interval_minutes=1).run_cycle()

    result = await Scheduler(interval_minutes=1).run_cycle()
    pending_cases = await odds_store.get_team_review_cases(status="pending")
    approved_cases = await odds_store.get_team_review_cases(status="approved")

    assert result["odds_scraped"] == 2
    assert pending_cases == []
    assert len(approved_cases) == 1
    assert approved_cases[0].review_kind == "auto_alias_suggestion"
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sportist"


@pytest.mark.asyncio
async def test_auto_apply_anchored_aliases_rolls_back_partial_alias_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler = Scheduler(interval_minutes=1)
    history_calls = 0

    async def flaky_history_summary(**kwargs):
        nonlocal history_calls
        history_calls += 1
        if history_calls == 2:
            raise RuntimeError("simulated history lookup failure")
        return set(), False

    monkeypatch.setattr(
        odds_store,
        "get_team_review_case_history_summary",
        flaky_history_summary,
    )

    cases = [
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            sport="basketball",
            raw_team_name="Rilski Sport.",
            normalized_raw_team_name="rilski sport",
            suggested_team_id=101,
            suggested_team_name="Rilski Sportist",
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=95,
            matched_counterpart_team="Levski Sofia",
            canonical_home_team="Rilski Sportist",
            canonical_away_team="Levski Sofia",
        ),
        TeamReviewDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="NBL",
            normalized_raw_league_id="nbl",
            sport="basketball",
            raw_team_name="Rilski Sport.",
            normalized_raw_team_name="rilski sport",
            suggested_team_id=101,
            suggested_team_name="Rilski Sportist",
            start_time="2030-01-01T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=95,
            matched_counterpart_team="Levski Sofia",
            canonical_home_team="Rilski Sportist",
            canonical_away_team="Levski Sofia",
        ),
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="A1",
            normalized_raw_league_id="a1",
            sport="basketball",
            raw_team_name="Panathinaikos AKTOR",
            normalized_raw_team_name="panathinaikos aktor",
            suggested_team_id=202,
            suggested_team_name="Panathinaikos",
            start_time="2030-01-02T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=95,
            matched_counterpart_team="Olympiacos",
            canonical_home_team="Panathinaikos",
            canonical_away_team="Olympiacos",
        ),
        TeamReviewDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="A1",
            normalized_raw_league_id="a1",
            sport="basketball",
            raw_team_name="Panathinaikos AKTOR",
            normalized_raw_team_name="panathinaikos aktor",
            suggested_team_id=202,
            suggested_team_name="Panathinaikos",
            start_time="2030-01-02T20:00:00+00:00",
            review_kind="alias_suggestion",
            reason_code="candidate_team_match_same_start_time",
            confidence="high",
            similarity_score=95,
            matched_counterpart_team="Olympiacos",
            canonical_home_team="Panathinaikos",
            canonical_away_team="Olympiacos",
        ),
    ]

    with pytest.raises(RuntimeError, match="simulated history lookup failure"):
        await scheduler._auto_apply_anchored_aliases(cases)

    assert resolve_team_alias("Rilski Sport.", bookmaker_id="meridian") is None
    assert resolve_team_alias("Rilski Sport.", bookmaker_id="maxbet") is None


@pytest.mark.asyncio
async def test_scheduler_run_cycle_reports_timing_when_available():
    _register_test_scrapers(
        StubScraper(
            "alpha",
            delay=0.02,
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5)]},
        ),
        StubScraper(
            "beta",
            delay=0.02,
            payload_by_league={"euroleague": [_raw_odds("beta", 20.5)]},
        ),
    )

    result = await Scheduler(interval_minutes=1).run_cycle()

    if "cycle_duration_ms" not in result:
        pytest.skip("Scheduler does not expose cycle timing")

    assert isinstance(result["cycle_duration_ms"], int)
    assert result["cycle_duration_ms"] > 0

    if "scrape_duration_ms" in result:
        assert isinstance(result["scrape_duration_ms"], int)
        assert result["scrape_duration_ms"] > 0
        assert result["cycle_duration_ms"] >= result["scrape_duration_ms"]


@pytest.mark.asyncio
async def test_scheduler_start_stop():
    s = Scheduler(interval_minutes=60)
    await s.start()
    assert s.is_running
    await s.stop()
    assert not s.is_running


@pytest.mark.asyncio
async def test_scheduler_double_start():
    s = Scheduler(interval_minutes=60)
    await s.start()
    await s.start()  # should not raise
    assert s.is_running
    await s.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_waits_for_active_cycle_to_finish():
    _register_test_scrapers(
        StubScraper(
            "alpha",
            delay=0.05,
            payload_by_league={"euroleague": [_raw_odds("alpha", 18.5)]},
        ),
        StubScraper(
            "beta",
            delay=0.05,
            payload_by_league={"euroleague": [_raw_odds("beta", 20.5)]},
        ),
    )

    scheduler_under_test = Scheduler(interval_minutes=60)
    await scheduler_under_test.start()

    for _ in range(10):
        if scheduler_under_test.progress_snapshot().in_progress:
            break
        await asyncio.sleep(0.01)

    assert scheduler_under_test.progress_snapshot().in_progress is True

    await scheduler_under_test.stop()

    status = await odds_store.get_system_status()
    assert scheduler_under_test.is_running is False
    assert scheduler_under_test.progress_snapshot().in_progress is False
    assert status.last_scrape_at is not None
