from __future__ import annotations

import asyncio

import pytest

from app.models.schemas import NormalizedOdds, RawOddsData
from app.scrapers.base import BaseScraper
from app.services.scheduler import Scheduler
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
    assert result["notifications_sent"] == 1


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
    assert result["notifications_sent"] == 1


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
    assert result["notifications_sent"] == 1


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
