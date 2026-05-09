from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, get_db, init_db
from app.migrations.runner import upgrade_database
from app.main import app
from app.models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    OpportunityLeg,
    RawOddsData,
    ResolvedEventIn,
    ResolvedEventMemberIn,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
from app.scrapers.base import BaseScraper
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
import app.services.scheduler as scheduler_service
from app.services.scheduler import scheduler
from app.services.notifications import TelegramBotClient, TelegramSendMessageResult
from app.services.normalizer import normalize_team_name
from app.services.opportunity_analyzer import Opportunity, analyze_outcome_offers
from app.services.team_registry import create_canonical_team, remember_team_alias
from app.store import odds_store


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


@pytest.fixture(autouse=True)
async def setup_app():
    """Set up fresh DB and register scrapers before each test."""
    await init_db(settings.db_path)
    # Clear and re-register scrapers
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian", "maxbet"):
        registry.register(MockScraper(bm))
    yield
    await close_db()


@pytest.fixture
async def client(setup_app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "KvotoLovac"


@pytest.mark.asyncio
async def test_status_endpoint(client: AsyncClient):
    resp = await client.get("/api/v1/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["scan"]["in_progress"] is False
    assert data["scan"]["phase"] == "idle"


@pytest.mark.asyncio
async def test_trigger_scrape(client: AsyncClient):
    resp = await client.post("/api/v1/scrape/trigger")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matches_scraped"] > 0
    assert data["odds_scraped"] > 0
    assert data["opportunities_found"] > 0


@pytest.mark.asyncio
async def test_trigger_scrape_rejects_when_cycle_is_already_running(client: AsyncClient):
    class SlowScraper(BaseScraper):
        def get_bookmaker_id(self) -> str:
            return "slow"

        def get_bookmaker_name(self) -> str:
            return "Slow"

        def get_supported_leagues(self) -> list[str]:
            return ["euroleague"]

        async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
            await asyncio.sleep(0.05)
            return [
                RawOddsData(
                    bookmaker_id="slow",
                    league_id=league_id,
                    home_team="Olympiacos",
                    away_team="Real Madrid",
                    market_type="player_points",
                    player_name="Sasha Vezenkov",
                    threshold=18.5,
                    over_odds=1.9,
                    under_odds=1.9,
                    start_time="2030-01-01T20:00:00+00:00",
                )
            ]

    registry._scrapers.clear()
    registry.register(SlowScraper())

    cycle_task = asyncio.create_task(scheduler.run_cycle())
    for _ in range(10):
        if scheduler.is_cycle_in_progress:
            break
        await asyncio.sleep(0.01)

    assert scheduler.is_cycle_in_progress is True

    resp = await client.post("/api/v1/scrape/trigger")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Scrape already in progress"

    await cycle_task


@pytest.mark.asyncio
async def test_get_scrape_settings_defaults(client: AsyncClient):
    resp = await client.get("/api/v1/settings/scrape")

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"]["scrape_market_scope"] == settings.scrape_market_scope
    assert data["applied"]["enabled_sports"] == settings.enabled_sport_list
    assert "football" in data["defaults"]["enabled_sports"]
    assert data["applied"]["analysis_markets"] == ["all"]
    assert data["applied"]["scrape_interval_minutes"] == settings.scrape_interval_minutes
    assert data["defaults"]["scrape_market_scope"] == settings.scrape_market_scope
    assert data["defaults"]["analysis_markets"] == ["all"]
    assert data["defaults"]["scrape_interval_minutes"] == settings.scrape_interval_minutes
    assert set(data["defaults"]["enabled_bookmakers"]) == set(settings.bookmaker_list)
    assert data["pending"] is None
    assert data["has_pending_changes"] is False
    assert {item["id"] for item in data["options"]["bookmakers"]} >= {
        "mozzart",
        "meridian",
        "maxbet",
    }
    assert "tennis" in data["options"]["sports"]
    assert {item["token"] for item in data["options"]["analysis_market_options"]} >= {
        "basketball:player_*",
        "basketball:home_handicap_ot",
        "football:football_total_goals",
        "tennis:tennis_match_winner",
    }


@pytest.mark.asyncio
async def test_patch_scrape_settings_applies_immediately_when_idle(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={
            "enabled_bookmakers": ["mozzart"],
            "enabled_sports": ["basketball"],
            "scrape_market_scope": "player_props",
            "scrape_lookahead_hours": 12,
            "scrape_interval_minutes": 7,
            "max_middle_opportunities_per_market": 5,
            "rate_limit_per_second": 3.0,
            "meridian_rate_limit_per_second": 4.0,
            "soccerbet_detail_mode": "full",
            "merkurxtip_detail_mode": "full",
            "pinnbet_detail_mode": "full",
            "betole_detail_mode": "full",
            "notification_gap_threshold": 2.5,
            "persist_inapp_notifications": True,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_immediately"] is True
    assert data["pending"] is None
    assert data["has_pending_changes"] is False
    assert data["applied"]["enabled_bookmakers"] == ["mozzart"]
    assert data["applied"]["scrape_market_scope"] == "player_props"
    assert data["applied"]["analysis_markets"] == ["*:player_*"]
    assert data["applied"]["scrape_interval_minutes"] == 7
    assert data["applied"]["soccerbet_detail_mode"] == "full"
    assert data["applied"]["pinnbet_detail_mode"] == "full"
    assert data["applied"]["betole_detail_mode"] == "full"

    get_resp = await client.get("/api/v1/settings/scrape")
    assert get_resp.json()["applied"]["enabled_bookmakers"] == ["mozzart"]


@pytest.mark.asyncio
async def test_patch_scrape_settings_accepts_analysis_markets(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={
            "enabled_bookmakers": ["mozzart"],
            "enabled_sports": ["basketball"],
            "analysis_markets": [
                "basketball:player_*",
                "basketball:home_handicap_ot",
            ],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"]["analysis_markets"] == [
        "basketball:player_*",
        "basketball:home_handicap_ot",
    ]
    assert data["applied"]["scrape_market_scope"] == "all"


@pytest.mark.asyncio
async def test_patch_scrape_settings_accepts_advertised_tennis_analysis_market(
    client: AsyncClient,
):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={
            "enabled_bookmakers": ["mozzart"],
            "enabled_sports": ["tennis"],
            "analysis_markets": ["tennis:tennis_match_winner"],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"]["enabled_sports"] == ["tennis"]
    assert data["applied"]["analysis_markets"] == ["tennis:tennis_match_winner"]


@pytest.mark.asyncio
async def test_telegram_settings_crud_redacts_token(client: AsyncClient):
    resp = await client.get("/api/v1/settings/telegram")

    assert resp.status_code == 200
    data = resp.json()
    assert data["token_configured"] is False
    assert data["profiles"] == []
    assert "telegram_bot_token" not in json.dumps(data)

    create_resp = await client.post(
        "/api/v1/settings/telegram/profiles",
        json={
            "label": "Main",
            "chat_id": "12345",
            "enabled": True,
            "min_gap": 1.5,
            "min_roi_percent": 5,
            "min_middle_ev_percent": 1.25,
            "bookmaker_ids": ["mozzart", "meridian"],
        },
    )
    assert create_resp.status_code == 201
    profile = create_resp.json()
    assert profile["id"] == 1
    assert profile["bookmaker_ids"] == ["mozzart", "meridian"]
    assert profile["min_middle_ev_percent"] == 1.25
    assert "token" not in json.dumps(profile).lower()

    patch_resp = await client.patch(
        "/api/v1/settings/telegram/profiles/1",
        json={"enabled": False, "bookmaker_ids": []},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["enabled"] is False
    assert patch_resp.json()["bookmaker_ids"] == []

    list_resp = await client.get("/api/v1/settings/telegram")
    assert [item["label"] for item in list_resp.json()["profiles"]] == ["Main"]

    delete_resp = await client.delete("/api/v1/settings/telegram/profiles/1")
    assert delete_resp.status_code == 200
    assert delete_resp.json() == {"profile_id": 1, "deleted": True}


@pytest.mark.asyncio
async def test_telegram_settings_rejects_invalid_profile(client: AsyncClient):
    resp = await client.post(
        "/api/v1/settings/telegram/profiles",
        json={
            "label": "Main",
            "chat_id": "12345",
            "bookmaker_ids": ["not-a-bookmaker"],
        },
    )

    assert resp.status_code == 422
    assert "Unknown bookmaker ids" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_telegram_test_message_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
):
    await client.post(
        "/api/v1/settings/telegram/profiles",
        json={"label": "Main", "chat_id": "12345"},
    )
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    calls: list[tuple[str, str]] = []

    async def fake_send_message(
        self: TelegramBotClient,
        *,
        chat_id: str,
        text: str,
    ) -> TelegramSendMessageResult:
        calls.append((chat_id, text))
        return TelegramSendMessageResult(message_id=77)

    monkeypatch.setattr(TelegramBotClient, "send_message", fake_send_message)

    resp = await client.post("/api/v1/settings/telegram/profiles/1/test")

    assert resp.status_code == 200
    assert resp.json() == {"profile_id": 1, "ok": True, "message_id": 77}
    assert calls == [("12345", "<b>KvotoLovac test</b>\nProfile: Main")]


@pytest.mark.asyncio
async def test_telegram_test_message_requires_token(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
):
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    await client.post(
        "/api/v1/settings/telegram/profiles",
        json={"label": "Main", "chat_id": "12345"},
    )

    resp = await client.post("/api/v1/settings/telegram/profiles/1/test")

    assert resp.status_code == 400
    assert "token is not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_scrape_settings_saves_pending_while_cycle_runs(client: AsyncClient):
    class SlowScraper(BaseScraper):
        def get_bookmaker_id(self) -> str:
            return "slow"

        def get_bookmaker_name(self) -> str:
            return "Slow"

        def get_supported_leagues(self) -> list[str]:
            return ["euroleague"]

        async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
            await asyncio.sleep(0.05)
            return [
                RawOddsData(
                    bookmaker_id="slow",
                    league_id=league_id,
                    home_team="Olympiacos",
                    away_team="Real Madrid",
                    market_type="player_points",
                    player_name="Sasha Vezenkov",
                    threshold=18.5,
                    over_odds=1.9,
                    under_odds=1.9,
                    start_time="2030-01-01T20:00:00+00:00",
                )
            ]

    registry._scrapers.clear()
    registry.register(SlowScraper())

    cycle_task = asyncio.create_task(scheduler.run_cycle())
    for _ in range(10):
        if scheduler.is_cycle_in_progress:
            break
        await asyncio.sleep(0.01)

    assert scheduler.is_cycle_in_progress is True

    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={
            "enabled_bookmakers": ["slow"],
            "scrape_interval_minutes": 3,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_immediately"] is False
    assert data["has_pending_changes"] is True
    assert data["pending"]["scrape_interval_minutes"] == 3
    assert data["applied"]["scrape_interval_minutes"] == settings.scrape_interval_minutes

    await cycle_task


@pytest.mark.asyncio
async def test_patch_scrape_settings_waits_for_cycle_snapshot_before_pending_decision(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    class SlowScraper(BaseScraper):
        def get_bookmaker_id(self) -> str:
            return "slow"

        def get_bookmaker_name(self) -> str:
            return "Slow"

        def get_supported_leagues(self) -> list[str]:
            return ["euroleague"]

        async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
            await asyncio.sleep(0.1)
            return [
                RawOddsData(
                    bookmaker_id="slow",
                    league_id=league_id,
                    home_team="Olympiacos",
                    away_team="Real Madrid",
                    market_type="player_points",
                    player_name="Sasha Vezenkov",
                    threshold=18.5,
                    over_odds=1.9,
                    under_odds=1.9,
                    start_time="2030-01-01T20:00:00+00:00",
                )
            ]

    registry._scrapers.clear()
    registry.register(SlowScraper())

    original_promote = scheduler_service.promote_pending_scrape_settings
    promote_started = asyncio.Event()
    release_promote = asyncio.Event()

    async def slow_promote_pending_scrape_settings():
        promote_started.set()
        await release_promote.wait()
        return await original_promote()

    monkeypatch.setattr(
        scheduler_service,
        "promote_pending_scrape_settings",
        slow_promote_pending_scrape_settings,
    )

    cycle_task = asyncio.create_task(scheduler.run_cycle())
    await asyncio.wait_for(promote_started.wait(), timeout=1)
    assert scheduler.is_cycle_in_progress is True

    patch_task = asyncio.create_task(
        client.patch(
            "/api/v1/settings/scrape",
            json={
                "enabled_bookmakers": ["slow"],
                "scrape_interval_minutes": 3,
            },
        )
    )
    await asyncio.sleep(0.01)
    assert patch_task.done() is False

    release_promote.set()
    resp = await patch_task

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied_immediately"] is False
    assert data["has_pending_changes"] is True
    assert data["pending"]["scrape_interval_minutes"] == 3

    await cycle_task


@pytest.mark.asyncio
async def test_patch_scrape_settings_rejects_unknown_bookmaker(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={"enabled_bookmakers": ["not-a-bookmaker"]},
    )

    assert resp.status_code == 422
    assert "Unknown bookmaker ids" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_scrape_settings_rejects_invalid_analysis_markets(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={"analysis_markets": ["all", "basketball:player_*"]},
    )

    assert resp.status_code == 422
    assert "'all' cannot be combined" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_scrape_settings_sanitizes_stale_persisted_values(
    client: AsyncClient,
):
    settings_resp = await client.get("/api/v1/settings/scrape")
    stale_config = settings_resp.json()["applied"]
    stale_config.update(
        {
            "enabled_bookmakers": ["retired-bookmaker", "mozzart"],
            "enabled_sports": ["retired-sport", "basketball"],
            "analysis_markets": ["basketball:player_*", "bad-token"],
            "rate_limit_per_second": 999.0,
        }
    )
    db = await get_db()
    await db.execute(
        "UPDATE runtime_scrape_settings SET applied_config = ? WHERE id = 1",
        (json.dumps(stale_config),),
    )
    await db.commit()

    resp = await client.get("/api/v1/settings/scrape")

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"]["enabled_bookmakers"] == ["mozzart"]
    assert data["applied"]["enabled_sports"] == ["basketball"]
    assert data["applied"]["analysis_markets"] == ["all"]
    assert data["applied"]["rate_limit_per_second"] == 20.0

    patch_resp = await client.patch(
        "/api/v1/settings/scrape",
        json={"scrape_interval_minutes": 4},
    )
    assert patch_resp.status_code == 200


@pytest.mark.asyncio
async def test_list_matches_after_scrape(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/matches")
    assert resp.status_code == 200
    matches = resp.json()
    assert len(matches) >= 4
    assert "available_bookmakers" in matches[0]
    assert len(matches[0]["available_bookmakers"]) > 0


@pytest.mark.asyncio
async def test_get_match_detail(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == match_id


@pytest.mark.asyncio
async def test_get_match_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/matches/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_match_odds(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}/odds")
    assert resp.status_code == 200
    assert len(resp.json()) > 0
    assert "source_url" in resp.json()[0]


@pytest.mark.asyncio
async def test_football_market_offers_and_opportunities_api(client: AsyncClient):
    home = create_canonical_team(display_name="Hatta SC", sport="football")
    away = create_canonical_team(display_name="Al Urooba UAE", sport="football")
    match_id = "football-api-match"
    scraped_at = "2030-01-01T12:00:00"

    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("balkanbet", "BalkanBet")
    await odds_store.upsert_league("uae_2", "UAE 2", "football")
    await odds_store.upsert_match(
        id=match_id,
        league_id="uae_2",
        sport="football",
        home_team_id=home.team_id,
        away_team_id=away.team_id,
        home_team=home.team_name,
        away_team=away.team_name,
        start_time="2030-01-01T20:00:00+00:00",
    )

    offers = [
        NormalizedOutcomeOffer(
            match_id=match_id,
            bookmaker_id="maxbet",
            league_id="uae_2",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            source_url="https://www.maxbet.rs/sr/pocetna#/sport/event/1",
            market_type="football_total_goals",
            outcome_code="under",
            odds=2.15,
            line=2.5,
            raw_label="0-2",
            start_time="2030-01-01T20:00:00+00:00",
        ),
        NormalizedOutcomeOffer(
            match_id=match_id,
            bookmaker_id="balkanbet",
            league_id="uae_2",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            source_url="https://sports-sm-web.7platform.net/event/1",
            market_type="football_total_goals",
            outcome_code="over",
            odds=2.85,
            line=2.5,
            raw_label="3+",
            start_time="2030-01-01T20:00:00+00:00",
        ),
    ]
    for offer in offers:
        await odds_store.upsert_outcome_offer(offer, scraped_at=scraped_at)
    await odds_store.set_current_snapshot(scraped_at)

    for opportunity in analyze_outcome_offers(offers):
        await odds_store.insert_opportunity(opportunity, detected_at=scraped_at)

    matches_resp = await client.get("/api/v1/matches", params={"sport": "football"})
    assert matches_resp.status_code == 200
    assert matches_resp.json()[0]["id"] == match_id
    assert {row["id"] for row in matches_resp.json()[0]["available_bookmakers"]} == {
        "balkanbet",
        "maxbet",
    }
    filtered_matches_resp = await client.get(
        "/api/v1/matches",
        params={"sport": "football", "bookmaker_ids": "maxbet"},
    )
    assert filtered_matches_resp.status_code == 200
    assert [row["id"] for row in filtered_matches_resp.json()] == [match_id]

    no_match_matches_resp = await client.get(
        "/api/v1/matches",
        params={"sport": "football", "bookmaker_ids": "mozzart"},
    )
    assert no_match_matches_resp.status_code == 200
    assert no_match_matches_resp.json() == []

    offers_resp = await client.get("/api/v1/market-offers", params={"sport": "football"})
    assert offers_resp.status_code == 200
    assert {row["outcome_code"] for row in offers_resp.json()} == {"under", "over"}

    opportunities_resp = await client.get("/api/v1/opportunities", params={"sport": "football"})
    assert opportunities_resp.status_code == 200
    opportunities = opportunities_resp.json()
    assert len(opportunities) == 1
    assert opportunities[0]["market_type"] == "football_total_goals"
    assert opportunities[0]["profit_margin"] > 0
    assert {leg["outcome_code"] for leg in opportunities[0]["legs"]} == {"under", "over"}
    assert {leg["bookmaker_name"] for leg in opportunities[0]["legs"]} == {
        "BalkanBet",
        "MaxBet",
    }
    assert all(leg["source_url"] for leg in opportunities[0]["legs"])

    filtered_resp = await client.get(
        "/api/v1/opportunities",
        params={"sport": "football", "bookmaker_ids": "maxbet"},
    )
    assert filtered_resp.status_code == 200
    assert len(filtered_resp.json()) == 1

    no_match_resp = await client.get(
        "/api/v1/opportunities",
        params={"sport": "football", "bookmaker_ids": "bet"},
    )
    assert no_match_resp.status_code == 200
    assert no_match_resp.json() == []


@pytest.mark.asyncio
async def test_match_market_offers_include_resolved_event_members(client: AsyncClient):
    home = create_canonical_team(display_name="Jelgava", sport="football")
    away = create_canonical_team(display_name="Super Nova", sport="football")
    current_snapshot = "2030-01-01T12:00:00"
    stale_snapshot = "2029-12-31T12:00:00"

    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("balkanbet", "BalkanBet")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_league("latvia_1", "Latvia 1", "football")
    for match_id, home_team, away_team in (
        ("primary-football-match", "Jelgava", "Super Nova"),
        ("sibling-football-match", "FS Jelgava", "SK Super Nova"),
        ("stale-football-match", "Jelgava Old", "Super Nova Old"),
    ):
        await odds_store.upsert_match(
            id=match_id,
            league_id="latvia_1",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home_team,
            away_team=away_team,
            start_time="2030-01-01T20:00:00+00:00",
        )

    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="primary-football-match",
            bookmaker_id="maxbet",
            league_id="latvia_1",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team="Jelgava",
            away_team="Super Nova",
            source_url="https://www.maxbet.rs/event/1",
            market_type="football_result",
            outcome_code="home",
            odds=2.4,
            start_time="2030-01-01T20:00:00+00:00",
        ),
        scraped_at=current_snapshot,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="sibling-football-match",
            bookmaker_id="balkanbet",
            league_id="latvia_1",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team="FS Jelgava",
            away_team="SK Super Nova",
            source_url="https://balkanbet.rs/event/1",
            market_type="football_result",
            outcome_code="away",
            odds=2.8,
            start_time="2030-01-01T20:00:00+00:00",
        ),
        scraped_at=current_snapshot,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="stale-football-match",
            bookmaker_id="mozzart",
            league_id="latvia_1",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team="Jelgava Old",
            away_team="Super Nova Old",
            source_url="https://mozzartbet.com/event/old",
            market_type="football_result",
            outcome_code="draw",
            odds=3.1,
            start_time="2030-01-01T20:00:00+00:00",
        ),
        scraped_at=stale_snapshot,
    )
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-latvia-football",
            sport="football",
            start_time="2030-01-01T20:00:00+00:00",
            primary_match_id="primary-football-match",
            method="auto_fuzzy_high",
        )
    )
    for match_id, bookmaker_id, snapshot_id in (
        ("primary-football-match", "maxbet", current_snapshot),
        ("sibling-football-match", "balkanbet", current_snapshot),
        ("stale-football-match", "mozzart", stale_snapshot),
    ):
        await odds_store.link_resolved_event_member(
            ResolvedEventMemberIn(
                snapshot_id=snapshot_id,
                resolved_event_id="evt-latvia-football",
                match_id=match_id,
                bookmaker_id=bookmaker_id,
            )
        )
    await odds_store.set_current_snapshot(current_snapshot)

    resp = await client.get("/api/v1/matches/sibling-football-match/market-offers")
    assert resp.status_code == 200
    rows = resp.json()
    assert {row["match_id"] for row in rows} == {
        "primary-football-match",
        "sibling-football-match",
    }
    assert {row["bookmaker_id"] for row in rows} == {"maxbet", "balkanbet"}

    filtered_resp = await client.get(
        "/api/v1/matches/primary-football-match/market-offers",
        params={"bookmaker_ids": "maxbet"},
    )
    assert filtered_resp.status_code == 200
    assert {row["bookmaker_id"] for row in filtered_resp.json()} == {"maxbet"}

    detail_resp = await client.get("/api/v1/matches/primary-football-match")
    assert detail_resp.status_code == 200
    assert {row["id"] for row in detail_resp.json()["available_bookmakers"]} == {
        "maxbet",
        "balkanbet",
    }


@pytest.mark.asyncio
async def test_match_market_offers_fall_back_to_exact_match(client: AsyncClient):
    home = create_canonical_team(display_name="Napredak", sport="football")
    away = create_canonical_team(display_name="Radnicki", sport="football")
    scraped_at = "2030-01-01T12:00:00"

    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_league("serbia_1", "Serbia 1", "football")
    await odds_store.upsert_match(
        id="standalone-football-match",
        league_id="serbia_1",
        sport="football",
        home_team_id=home.team_id,
        away_team_id=away.team_id,
        home_team=home.team_name,
        away_team=away.team_name,
        start_time="2030-01-01T18:00:00+00:00",
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="standalone-football-match",
            bookmaker_id="maxbet",
            league_id="serbia_1",
            sport="football",
            home_team_id=home.team_id,
            away_team_id=away.team_id,
            home_team=home.team_name,
            away_team=away.team_name,
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.9,
            line=2.5,
            raw_label="3+",
            start_time="2030-01-01T18:00:00+00:00",
        ),
        scraped_at=scraped_at,
    )
    await odds_store.set_current_snapshot(scraped_at)

    resp = await client.get("/api/v1/matches/standalone-football-match/market-offers")
    assert resp.status_code == 200
    assert [(row["match_id"], row["bookmaker_id"]) for row in resp.json()] == [
        ("standalone-football-match", "maxbet")
    ]

    missing_resp = await client.get("/api/v1/matches/not-a-match/market-offers")
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_opportunities_api_legacy_overlap_flag_is_noop(
    client: AsyncClient,
):
    await odds_store.upsert_league("euroleague", "EuroLeague", "basketball")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match(
        id="basketball-match",
        league_id="euroleague",
        sport="basketball",
        home_team="Olympiacos",
        away_team="Real Madrid",
        start_time="2030-01-01T20:00:00+00:00",
    )
    overlapping_id = await odds_store.insert_opportunity(
        Opportunity(
            sport="basketball",
            match_id="basketball-match",
            opportunity_type="middle",
            market_type="player_points",
            subject_type="player",
            subject_key="player:nikola jokic",
            subject_name="Nikola Jokic",
            line=18.5,
            profit_margin=0.02,
            middle_profit_margin=0.50,
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    market_type="player_points",
                    outcome_code="over",
                    line=18.5,
                    odds=1.90,
                ),
                OpportunityLeg(
                    bookmaker_id="meridian",
                    market_type="player_points",
                    outcome_code="under",
                    line=20.5,
                    odds=2.10,
                ),
            ],
        ),
        detected_at="2030-01-01T20:00:00",
    )
    canonical_only_id = await odds_store.insert_opportunity(
        Opportunity(
            sport="basketball",
            match_id="basketball-match",
            opportunity_type="same_line_arbitrage",
            market_type="player_points",
            subject_type="player",
            subject_key="player:nikola jokic",
            subject_name="Nikola Jokic",
            line=22.5,
            profit_margin=0.04,
            middle_profit_margin=None,
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    market_type="player_points",
                    outcome_code="over",
                    line=22.5,
                    odds=2.10,
                ),
                OpportunityLeg(
                    bookmaker_id="meridian",
                    market_type="player_points",
                    outcome_code="under",
                    line=22.5,
                    odds=2.10,
                ),
            ],
        ),
        detected_at="2030-01-01T20:01:00",
    )
    default_resp = await client.get("/api/v1/opportunities")
    basketball_resp = await client.get(
        "/api/v1/opportunities",
        params={"sport": "basketball"},
    )
    opt_in_resp = await client.get(
        "/api/v1/opportunities",
        params={
            "sport": "basketball",
            "include_legacy_discrepancy_overlap": "true",
        },
    )

    assert default_resp.status_code == 200
    assert basketball_resp.status_code == 200
    assert opt_in_resp.status_code == 200
    expected_ids = {
        overlapping_id,
        canonical_only_id,
    }
    assert {row["id"] for row in default_resp.json()} == expected_ids
    assert {row["id"] for row in basketball_resp.json()} == expected_ids
    assert {row["id"] for row in opt_in_resp.json()} == expected_ids
    default_row = next(row for row in default_resp.json() if row["id"] == canonical_only_id)
    assert default_row["opportunity_type"] == "same_line_arbitrage"
    assert default_row["event_id"] is None
    assert "subject_type" in default_row
    assert "market_keys" in default_row


@pytest.mark.asyncio
async def test_opportunities_api_returns_generic_shape_across_sports(client: AsyncClient):
    await odds_store.upsert_league("euroleague", "EuroLeague", "basketball")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_league("atp", "ATP", "tennis")
    for bookmaker_id, bookmaker_name in (
        ("mozzart", "Mozzart"),
        ("meridian", "Meridian"),
        ("maxbet", "MaxBet"),
        ("balkanbet", "BalkanBet"),
        ("alpha", "Alpha"),
        ("beta", "Beta"),
    ):
        await odds_store.upsert_bookmaker(bookmaker_id, bookmaker_name)

    await odds_store.upsert_match(
        id="basketball-match",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        start_time="2030-01-01T20:00:00+00:00",
    )
    await odds_store.upsert_match(
        id="football-match",
        league_id="premier_league",
        sport="football",
        home_team="Team Alpha",
        away_team="Team Beta",
        start_time="2030-01-01T18:00:00+00:00",
    )
    await odds_store.upsert_match(
        id="tennis-match",
        league_id="atp",
        sport="tennis",
        home_team="Novak Djokovic",
        away_team="Carlos Alcaraz",
        start_time="2030-01-01T16:00:00+00:00",
    )

    seeded_opportunities = [
        Opportunity(
            sport="basketball",
            match_id="basketball-match",
            opportunity_type="middle",
            market_type="player_points",
            subject_type="player",
            subject_key="ply_jokic",
            subject_name="Nikola Jokić",
            line=18.5,
            profit_margin=0.02,
            middle_profit_margin=0.50,
            market_keys=("mk-player-points-18.5", "mk-player-points-20.5"),
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    market_type="player_points",
                    outcome_code="over",
                    line=18.5,
                    odds=1.90,
                ),
                OpportunityLeg(
                    bookmaker_id="meridian",
                    market_type="player_points",
                    outcome_code="under",
                    line=20.5,
                    odds=2.10,
                ),
            ],
        ),
        Opportunity(
            sport="basketball",
            match_id="basketball-match",
            opportunity_type="same_line_arbitrage",
            market_type="player_rebounds",
            subject_type="player",
            subject_key="ply_micic",
            subject_name="Vasilije Micić",
            line=7.5,
            profit_margin=0.03,
            middle_profit_margin=None,
            market_keys=("mk-player-rebounds-7.5",),
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    market_type="player_rebounds",
                    outcome_code="over",
                    line=7.5,
                    odds=2.10,
                ),
                OpportunityLeg(
                    bookmaker_id="meridian",
                    market_type="player_rebounds",
                    outcome_code="under",
                    line=7.5,
                    odds=2.10,
                ),
            ],
        ),
        Opportunity(
            sport="football",
            match_id="football-match",
            opportunity_type="middle",
            market_type="football_total_goals",
            subject_type="event",
            line=2.5,
            profit_margin=-0.01,
            middle_profit_margin=0.08,
            market_keys=("mk-football-total-2.5", "mk-football-total-3.5"),
            legs=[
                OpportunityLeg(
                    bookmaker_id="maxbet",
                    market_type="football_total_goals",
                    outcome_code="over",
                    line=2.5,
                    odds=1.90,
                ),
                OpportunityLeg(
                    bookmaker_id="balkanbet",
                    market_type="football_total_goals",
                    outcome_code="under",
                    line=3.5,
                    odds=2.10,
                ),
            ],
        ),
        Opportunity(
            sport="football",
            match_id="football-match",
            opportunity_type="complementary_outcomes",
            market_type="football_result_double_chance",
            subject_type="event",
            line=None,
            profit_margin=0.04,
            middle_profit_margin=None,
            market_keys=("mk-football-result-home", "mk-football-dc-draw-away"),
            legs=[
                OpportunityLeg(
                    bookmaker_id="maxbet",
                    market_type="football_result",
                    outcome_code="home",
                    odds=2.10,
                ),
                OpportunityLeg(
                    bookmaker_id="balkanbet",
                    market_type="football_double_chance",
                    outcome_code="draw_or_away",
                    odds=2.10,
                ),
            ],
        ),
        Opportunity(
            sport="tennis",
            match_id="tennis-match",
            opportunity_type="same_line_arbitrage",
            market_type="match_winner",
            subject_type="event",
            line=None,
            profit_margin=0.05,
            middle_profit_margin=None,
            market_keys=("mk-tennis-winner",),
            legs=[
                OpportunityLeg(
                    bookmaker_id="alpha",
                    market_type="tennis_match_winner",
                    outcome_code="home",
                    odds=2.10,
                ),
                OpportunityLeg(
                    bookmaker_id="beta",
                    market_type="tennis_match_winner",
                    outcome_code="away",
                    odds=2.10,
                ),
            ],
        ),
    ]
    for opportunity in seeded_opportunities:
        await odds_store.insert_opportunity(
            opportunity,
            detected_at="2030-01-01T20:00:00",
        )

    resp = await client.get(
        "/api/v1/opportunities",
        params={"include_legacy_discrepancy_overlap": "true", "limit": 10},
    )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 5
    assert {
        (row["sport"], row["opportunity_type"], row["market_type"])
        for row in rows
    } == {
        ("basketball", "middle", "player_points"),
        ("basketball", "same_line_arbitrage", "player_rebounds"),
        ("football", "middle", "football_total_goals"),
        ("football", "complementary_outcomes", "football_result_double_chance"),
        ("tennis", "same_line_arbitrage", "match_winner"),
    }

    player_middle = next(row for row in rows if row["subject_key"] == "ply_jokic")
    assert player_middle["event_id"] is None
    assert player_middle["resolved_event_id"] is None
    assert player_middle["subject_type"] == "player"
    assert player_middle["subject_name"] == "Nikola Jokić"
    assert player_middle["market_keys"] == [
        "mk-player-points-18.5",
        "mk-player-points-20.5",
    ]
    assert [leg["bookmaker_name"] for leg in player_middle["legs"]] == [
        "Mozzart",
        "Meridian",
    ]

    football_complement = next(
        row for row in rows if row["opportunity_type"] == "complementary_outcomes"
    )
    assert football_complement["subject_type"] == "event"
    assert {leg["market_type"] for leg in football_complement["legs"]} == {
        "football_result",
        "football_double_chance",
    }

    tennis_winner = next(row for row in rows if row["sport"] == "tennis")
    assert tennis_winner["market_type"] == "match_winner"
    assert {leg["market_type"] for leg in tennis_winner["legs"]} == {
        "tennis_match_winner"
    }


@pytest.mark.asyncio
async def test_match_history(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    matches_resp = await client.get("/api/v1/matches")
    match_id = matches_resp.json()[0]["id"]

    resp = await client.get(f"/api/v1/matches/{match_id}/history")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_match_history_hides_first_unpublished_snapshot(client: AsyncClient):
    snapshot_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.persist_scrape_snapshot_batch(
        snapshot_at=snapshot_at,
        odds=[
            NormalizedOdds(
                match_id="hidden-match",
                bookmaker_id="meridian",
                league_id="euroleague",
                sport="basketball",
                home_team="Hidden Home",
                away_team="Hidden Away",
                market_type="player_points",
                player_name="Hidden Player",
                threshold=13.5,
                over_odds=2.1,
                under_odds=1.7,
                start_time="2026-04-11T22:00:00+00:00",
            )
        ],
        outcome_offers=[],
        unresolved_odds=[],
        team_review_cases=[],
    )

    resp = await client.get("/api/v1/matches/hidden-match/history")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_discrepancy_api_removed(client: AsyncClient):
    list_resp = await client.get("/api/v1/discrepancies")
    assert list_resp.status_code == 404
    resp = await client.get("/api/v1/discrepancies/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_leagues(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/leagues")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_bookmakers(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")
    resp = await client.get("/api/v1/bookmakers")
    assert resp.status_code == 200
    bms = resp.json()
    assert len(bms) == 3


@pytest.mark.asyncio
async def test_list_unresolved_odds(client: AsyncClient, team_registry_file):
    batch_scraped_at = "2026-04-13T16:36:09.440629"
    await odds_store.upsert_bookmaker("admiralbet", "AdmiralBet")
    target = create_canonical_team(display_name="Borac Cacak")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="admiralbet",
            raw_league_id="AdmiralBet ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="P. Nikolic",
            raw_team_name="Borac Cacak",
            normalized_team_name="Borac Cacak",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
            available_matchups_same_slot=["Dubai vs Buducnost"],
        ),
        scraped_at=batch_scraped_at,
    )
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic(
            bookmaker_id="admiralbet",
            raw_league_id="AdmiralBet ABA Liga",
            normalized_raw_league_id="admiralbet aba liga",
            sport="basketball",
            scope_league_id="aba_liga",
            raw_team_name="Borac Cacak",
            normalized_raw_team_name="Borac Cacak",
            suggested_team_id=target.team_id,
            suggested_team_name=target.team_name,
            start_time="2026-04-13T16:00:00+00:00",
            review_kind="candidate_search",
            reason_code="candidate_team_match_same_start_time",
            confidence="medium",
            similarity_score=88.0,
            matched_counterpart_team="Dubai",
            evidence=["Exact start time: 2026-04-13T16:00:00+00:00"],
            status="pending",
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    resp = await client.get("/api/v1/unresolved-odds?bookmaker_ids=admiralbet")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["bookmaker_name"] == "AdmiralBet"
    assert data[0]["reason_code"] == "no_canonical_matchup_for_team_at_slot"
    assert data[0]["team_review_case_id"] == case_id
    assert data[0]["team_review_suggested_team_name"] == "Borac Cacak"
    assert data[0]["team_review_confidence"] == "medium"
    assert data[0]["team_review_status"] == "pending"


@pytest.mark.asyncio
async def test_list_matches_can_filter_by_bookmaker(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")

    resp = await client.get("/api/v1/matches?bookmaker_ids=meridian")

    assert resp.status_code == 200
    for match in resp.json():
        assert any(book["id"] == "meridian" for book in match["available_bookmakers"])


@pytest.mark.asyncio
async def test_list_matches_includes_resolved_event_id(client: AsyncClient):
    await client.post("/api/v1/scrape/trigger")

    resp = await client.get("/api/v1/matches")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload, "scrape should produce at least one match"
    # resolved_event_id is populated by the resolver. It must always be a string
    # for matches that participated in the cycle (every scrape produces resolved
    # events covering its source matches).
    for match in payload:
        assert "resolved_event_id" in match
        assert match["resolved_event_id"] is not None
        assert match["resolved_event_id"].startswith("evt_")


@pytest.mark.asyncio
async def test_list_canonical_teams_filters_by_sport(
    client: AsyncClient,
    team_registry_file,
):
    basketball_team = create_canonical_team(
        display_name="Partizan",
        sport="basketball",
    )
    football_team = create_canonical_team(
        display_name="Partizan",
        sport="football",
    )

    basketball_resp = await client.get("/api/v1/canonical-teams?sport=basketball&search=Partizan")
    football_resp = await client.get("/api/v1/canonical-teams?sport=football&search=Partizan")

    assert basketball_resp.status_code == 200
    assert [row["id"] for row in basketball_resp.json()] == [basketball_team.team_id]
    assert football_resp.status_code == 200
    assert [row["id"] for row in football_resp.json()] == [football_team.team_id]


@pytest.mark.asyncio
async def test_merge_canonical_teams_reassigns_aliases(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA Merge Source")
    target = create_canonical_team(display_name="QA Merge Target")
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="QA Merge Alias",
        team_name="QA Merge Source",
    )
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="QA Merge Target Alias",
        team_name="QA Merge Target",
    )

    list_resp = await client.get("/api/v1/canonical-teams?search=QA%20Merge")
    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    merged_resp = await client.get("/api/v1/canonical-teams?search=QA%20Merge")

    assert list_resp.status_code == 200
    assert {team["display_name"] for team in list_resp.json()} == {
        "QA Merge Source",
        "QA Merge Target",
    }
    assert merge_resp.status_code == 200
    assert merge_resp.json()["merged_team_name"] == "QA Merge Target"
    assert normalize_team_name("QA Merge Alias", None, "maxbet") == "QA Merge Target"
    assert normalize_team_name("QA Merge Source", None, "maxbet") == "QA Merge Target"
    assert merged_resp.status_code == 200
    assert [team["display_name"] for team in merged_resp.json()] == ["QA Merge Target"]
    assert "QA Merge Source" in merged_resp.json()[0]["aliases"]


@pytest.mark.asyncio
async def test_canonical_teams_can_include_merged_sources(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA Listed Merged Source")
    target = create_canonical_team(display_name="QA Listed Merged Target")
    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )

    active_resp = await client.get("/api/v1/canonical-teams?search=QA%20Listed%20Merged")
    merged_resp = await client.get(
        "/api/v1/canonical-teams?search=QA%20Listed%20Merged&include_merged=true"
    )

    assert merge_resp.status_code == 200
    assert active_resp.status_code == 200
    assert [team["display_name"] for team in active_resp.json()] == [
        "QA Listed Merged Target"
    ]
    assert merged_resp.status_code == 200
    rows_by_name = {team["display_name"]: team for team in merged_resp.json()}
    assert rows_by_name["QA Listed Merged Target"]["merged_into_team_id"] is None
    assert (
        rows_by_name["QA Listed Merged Source"]["merged_into_team_id"]
        == target.team_id
    )


@pytest.mark.asyncio
async def test_canonical_teams_page_returns_total_and_slice(
    client: AsyncClient,
    team_registry_file,
):
    teams = [
        create_canonical_team(display_name=f"QA Page {name}")
        for name in ("Alpha", "Bravo", "Charlie", "Delta")
    ]

    resp = await client.get(
        "/api/v1/canonical-teams/page?search=QA%20Page&limit=2&offset=1"
    )
    beyond_resp = await client.get(
        "/api/v1/canonical-teams/page?search=QA%20Page&limit=2&offset=99"
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == len(teams)
    assert payload["limit"] == 2
    assert payload["offset"] == 1
    assert [row["display_name"] for row in payload["items"]] == [
        "QA Page Bravo",
        "QA Page Charlie",
    ]
    assert beyond_resp.status_code == 200
    assert beyond_resp.json()["total"] == len(teams)
    assert beyond_resp.json()["items"] == []


@pytest.mark.asyncio
async def test_canonical_teams_page_search_matches_normalized_text(
    client: AsyncClient,
    team_registry_file,
):
    matching = create_canonical_team(display_name="QA Search Čačak 94")
    create_canonical_team(display_name="QA Search Beograd")

    resp = await client.get(
        "/api/v1/canonical-teams/page?search=search%20cacak%2094&limit=25"
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert [row["id"] for row in payload["items"]] == [matching.team_id]


@pytest.mark.asyncio
async def test_canonical_teams_page_can_include_merged_sources(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA Page Merged Source")
    target = create_canonical_team(display_name="QA Page Merged Target")
    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )

    active_resp = await client.get(
        "/api/v1/canonical-teams/page?search=QA%20Page%20Merged&limit=25"
    )
    merged_resp = await client.get(
        "/api/v1/canonical-teams/page?search=QA%20Page%20Merged&limit=25&include_merged=true"
    )

    assert merge_resp.status_code == 200
    assert active_resp.status_code == 200
    assert [row["display_name"] for row in active_resp.json()["items"]] == [
        "QA Page Merged Target"
    ]
    assert active_resp.json()["total"] == 1
    assert merged_resp.status_code == 200
    rows_by_name = {team["display_name"]: team for team in merged_resp.json()["items"]}
    assert merged_resp.json()["total"] == 2
    assert rows_by_name["QA Page Merged Source"]["merged_into_team_id"] == target.team_id
    assert rows_by_name["QA Page Merged Target"]["merged_into_team_id"] is None


@pytest.mark.asyncio
async def test_merge_canonical_teams_rewrites_pending_team_review_cases(
    client: AsyncClient,
    team_registry_file,
):
    await close_db()
    await init_db(settings.db_path)
    batch_scraped_at = "2026-04-16T19:45:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    source = create_canonical_team(display_name="QA Pending Merge Source")
    target = create_canonical_team(display_name="QA Pending Merge Target")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "sport": "basketball",
                "scope_league_id": "euroleague",
                "raw_team_name": "QA Pending Raw Alias",
                "normalized_raw_team_name": "QA Pending Raw Alias",
                "suggested_team_id": source.team_id,
                "suggested_team_name": source.team_name,
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 95,
                "candidate_teams": [
                    {
                        "team_id": source.team_id,
                        "team_name": source.team_name,
                        "score": 95,
                    },
                    {
                        "team_id": target.team_id,
                        "team_name": target.team_name,
                        "score": 88,
                    },
                ],
                "canonical_home_team": source.team_name,
                "canonical_away_team": "Olympiacos",
                "evidence": ["Exact start time: 2026-04-16T19:45:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    approve_resp = await client.post(
        f"/api/v1/team-review/cases/{case_id}/approve",
        json={"team_id": source.team_id},
    )
    updated_case = await odds_store.get_team_review_case(case_id)

    assert merge_resp.status_code == 200
    assert updated_case is not None
    assert updated_case.suggested_team_id == target.team_id
    assert updated_case.suggested_team_name == target.team_name
    assert updated_case.canonical_home_team == target.team_name
    assert all(candidate.team_id != source.team_id for candidate in updated_case.candidate_teams)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_id"] == target.team_id
    assert normalize_team_name("QA Pending Raw Alias", None, "meridian") == target.team_name


@pytest.mark.asyncio
async def test_merge_canonical_teams_rewrites_legacy_name_only_team_review_cases(
    client: AsyncClient,
    team_registry_file,
):
    await close_db()
    await init_db(settings.db_path)
    batch_scraped_at = "2026-04-16T19:50:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    source = create_canonical_team(display_name="QA Legacy Merge Source")
    target = create_canonical_team(display_name="QA Legacy Merge Target")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "sport": "basketball",
                "scope_league_id": "euroleague",
                "raw_team_name": "QA Legacy Raw Alias",
                "normalized_raw_team_name": "QA Legacy Raw Alias",
                "suggested_team_name": source.team_name,
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 94,
                "evidence": ["Exact start time: 2026-04-16T19:50:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    updated_case = await odds_store.get_team_review_case(case_id)

    assert merge_resp.status_code == 200
    assert updated_case is not None
    assert updated_case.suggested_team_id == target.team_id
    assert updated_case.suggested_team_name == target.team_name
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_id"] == target.team_id
    assert normalize_team_name("QA Legacy Raw Alias", None, "meridian") == target.team_name


@pytest.mark.asyncio
async def test_merge_canonical_teams_rejects_same_team(
    client: AsyncClient,
    team_registry_file,
):
    team = create_canonical_team(display_name="QA Merge Same Team")

    resp = await client.post(
        f"/api/v1/canonical-teams/{team.team_id}/merge",
        json={"target_team_id": team.team_id},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Cannot merge a canonical team into itself"


@pytest.mark.asyncio
async def test_merge_canonical_teams_rejects_missing_target(
    client: AsyncClient,
    team_registry_file,
):
    team = create_canonical_team(display_name="QA Merge Missing Target")

    resp = await client.post(
        f"/api/v1/canonical-teams/{team.team_id}/merge",
        json={"target_team_id": 99999},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Both canonical teams must exist before merging"


@pytest.mark.asyncio
async def test_unmerge_canonical_teams_restores_aliases(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA API Unmerge Source")
    target = create_canonical_team(display_name="QA API Unmerge Target")
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="QA API Unmerge Alias",
        team_name=source.team_name,
    )

    merge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    unmerge_resp = await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/unmerge",
    )
    list_resp = await client.get("/api/v1/canonical-teams?search=QA%20API%20Unmerge")

    assert merge_resp.status_code == 200
    assert unmerge_resp.status_code == 200
    assert unmerge_resp.json() == {
        "source_team_id": source.team_id,
        "target_team_id": target.team_id,
        "restored_team_name": source.team_name,
    }
    assert normalize_team_name("QA API Unmerge Alias", None, "maxbet") == source.team_name
    assert {team["display_name"] for team in list_resp.json()} == {
        source.team_name,
        target.team_name,
    }


@pytest.mark.asyncio
async def test_unmerge_canonical_teams_rejects_during_scrape_cycle(
    client: AsyncClient,
    team_registry_file,
):
    source = create_canonical_team(display_name="QA Busy Unmerge Source")
    target = create_canonical_team(display_name="QA Busy Unmerge Target")
    await client.post(
        f"/api/v1/canonical-teams/{source.team_id}/merge",
        json={"target_team_id": target.team_id},
    )
    scheduler._cycle_task = asyncio.create_task(asyncio.sleep(0.1))
    try:
        resp = await client.post(f"/api/v1/canonical-teams/{source.team_id}/unmerge")
    finally:
        scheduler._cycle_task.cancel()
        try:
            await scheduler._cycle_task
        except asyncio.CancelledError:
            pass
        scheduler._cycle_task = None

    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "Cannot unmerge canonical teams while a scrape cycle is in progress; try again shortly"
    )


@pytest.mark.asyncio
async def test_team_review_cases_and_approval(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T20:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "sport": "basketball",
                "scope_league_id": "bulgaria_nbl",
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 92,
                "evidence": ["Exact start time: 2026-04-16T20:00:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    cases_resp = await client.get("/api/v1/team-review/cases")
    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    approved_resp = await client.get("/api/v1/team-review/cases?status=approved")

    assert cases_resp.status_code == 200
    assert len(cases_resp.json()) == 1
    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Rilski Sportist"
    assert approve_resp.json()["resolved_team_name"] is None
    assert normalize_team_name("Rilski Sport.", "bulgaria_nbl", "meridian") == "Rilski Sportist"
    assert approved_resp.status_code == 200
    assert approved_resp.json()[0]["status"] == "approved"


@pytest.mark.asyncio
async def test_team_review_endpoint_lists_auto_approved_alias_audit_rows(
    client: AsyncClient,
):
    class AnchoredAliasScraper(BaseScraper):
        def __init__(self, bookmaker_id: str, payload: list[RawOddsData]) -> None:
            self._bookmaker_id = bookmaker_id
            self._payload = payload

        def get_bookmaker_id(self) -> str:
            return self._bookmaker_id

        def get_bookmaker_name(self) -> str:
            return self._bookmaker_id.title()

        def get_supported_leagues(self) -> list[str]:
            return ["euroleague"]

        async def scrape_odds(self, league_id: str) -> list[RawOddsData]:
            return list(self._payload)

    registry._scrapers.clear()
    registry.register(
        AnchoredAliasScraper("mozzart", [_anchored_team_raw("mozzart", "Rilski Sportist")])
    )
    registry.register(
        AnchoredAliasScraper(
            "meridian",
            [_anchored_team_raw("meridian", "Rilski Sport.", league_id="NBL")],
        )
    )
    registry.register(
        AnchoredAliasScraper(
            "maxbet",
            [_anchored_team_raw("maxbet", "Rilski Sport.", league_id="NBL")],
        )
    )

    trigger_resp = await client.post("/api/v1/scrape/trigger")
    approved_resp = await client.get("/api/v1/team-review/cases?status=approved")

    assert trigger_resp.status_code == 200
    assert trigger_resp.json()["odds_scraped"] == 1
    assert approved_resp.status_code == 200
    assert len(approved_resp.json()) == 2
    assert {case["bookmaker_id"] for case in approved_resp.json()} == {"meridian", "maxbet"}
    assert {case["review_kind"] for case in approved_resp.json()} == {"auto_alias_suggestion"}


@pytest.mark.asyncio
async def test_auto_review_does_not_overwrite_manual_alias_source():
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Rilski Sport.",
        team_name="Rilski Sportist",
        sport="basketball",
    )

    resolution = remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Rilski Sport.",
        team_name="Rilski Sportist",
        sport="basketball",
        source="auto_review",
    )

    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT source
           FROM team_aliases
           WHERE sport = ? AND normalized_alias = ? AND bookmaker_id = ?""",
        ("basketball", "rilski sport", "meridian"),
    )

    assert resolution.source == "manual_review"
    assert len(rows) == 1
    assert rows[0]["source"] == "manual_review"


@pytest.mark.asyncio
async def test_team_review_approval_flattens_to_final_canonical_team(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T20:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("brazil_nbb", "Brazil NBB", "basketball", "Brazil")
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Uniao Corinthians",
        team_name="EC Uniao Corinthians",
        competition_id="brazil_nbb",
    )
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Brazil NBB",
                "normalized_raw_league_id": "brazil nbb",
                "sport": "basketball",
                "scope_league_id": "brazil_nbb",
                "raw_team_name": "U.Corinthians",
                "normalized_raw_team_name": "U.Corinthians",
                "suggested_team_name": "Uniao Corinthians",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 93,
                "evidence": ["Exact start time: 2026-04-16T20:30:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Uniao Corinthians"
    assert approve_resp.json()["resolved_team_name"] == "EC Uniao Corinthians"
    assert normalize_team_name("Uniao Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"
    assert normalize_team_name("U.Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"


@pytest.mark.asyncio
async def test_team_review_case_can_be_declined(client: AsyncClient):
    batch_scraped_at = "2026-04-16T21:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "sport": "basketball",
                "scope_league_id": None,
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    decline_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/decline")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert decline_resp.status_code == 200
    assert decline_resp.json()["status"] == "declined"
    assert pending_resp.status_code == 200
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_team_review_approval_accepts_unscoped_alias(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T21:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "sport": "basketball",
                "scope_league_id": None,
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_name"] == "Rilski Sportist"
    assert normalize_team_name("Rilski Sport.", None, "meridian") == "Rilski Sportist"
    assert pending_resp.status_code == 200
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_team_review_approval_rejects_circular_alias(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T21:45:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Baskonia Gatez",
        team_name="Baskonia",
        competition_id="euroleague",
    )
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "Euroleague",
                "normalized_raw_league_id": "euroleague",
                "sport": "basketball",
                "scope_league_id": "euroleague",
                "raw_team_name": "Baskonia",
                "normalized_raw_team_name": "Baskonia",
                "suggested_team_name": "Baskonia Gatez",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 91,
                "evidence": ["Exact start time: 2026-04-16T21:45:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")

    assert approve_resp.status_code == 409
    assert "Circular alias" in approve_resp.json()["detail"]


@pytest.mark.asyncio
async def test_team_review_approval_merges_existing_canonical_duplicate(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T21:50:00+00:00"
    await odds_store.upsert_bookmaker("volcanobet", "VolcanoBet")
    await odds_store.upsert_league("paraguay_lnb", "Paraguay LNB", "basketball", "Paraguay")
    target = create_canonical_team(display_name="Deportivo Amambay", sport="basketball")
    duplicate = create_canonical_team(display_name="Amambay", sport="basketball")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "volcanobet",
                "raw_league_id": "Paraguay LNB",
                "normalized_raw_league_id": "paraguay lnb",
                "sport": "basketball",
                "scope_league_id": "paraguay_lnb",
                "raw_team_name": "Amambay",
                "normalized_raw_team_name": "Amambay",
                "suggested_team_id": target.team_id,
                "suggested_team_name": target.team_name,
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 100,
                "candidate_teams": [
                    {
                        "team_id": target.team_id,
                        "team_name": target.team_name,
                        "score": 100,
                    },
                    {
                        "team_id": duplicate.team_id,
                        "team_name": duplicate.team_name,
                        "score": 100,
                    },
                ],
                "evidence": ["Stronger competing canonical event: support x3"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(
        f"/api/v1/team-review/cases/{case_id}/approve",
        json={"team_id": target.team_id},
    )
    db = await get_db()
    duplicate_rows = await db.execute_fetchall(
        "SELECT is_active, merged_into_team_id FROM canonical_teams WHERE id = ?",
        (duplicate.team_id,),
    )

    assert approve_resp.status_code == 200
    assert approve_resp.json()["saved_team_id"] == target.team_id
    assert approve_resp.json()["merged_source_team_id"] == duplicate.team_id
    assert normalize_team_name("Amambay", None, "volcanobet") == target.team_name
    assert duplicate_rows[0]["is_active"] == 0
    assert duplicate_rows[0]["merged_into_team_id"] == target.team_id


@pytest.mark.asyncio
async def test_team_review_approval_only_updates_clicked_case(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T22:00:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")

    shared_payload = {
        "bookmaker_id": "meridian",
        "raw_league_id": "NBL",
        "normalized_raw_league_id": "nbl",
        "sport": "basketball",
        "scope_league_id": "bulgaria_nbl",
        "raw_team_name": "Rilski Sport.",
        "normalized_raw_team_name": "Rilski Sport.",
        "suggested_team_name": "Rilski Sportist",
        "reason_code": "candidate_team_match_same_start_time",
        "confidence": "high",
        "similarity_score": 92,
        "evidence": ["Exact start time: 2026-04-16T22:00:00+00:00"],
        "status": "pending",
    }
    first_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                **shared_payload,
                "start_time": "2026-04-16T22:00:00+00:00",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    second_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                **shared_payload,
                "start_time": "2026-04-16T23:00:00+00:00",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{first_case_id}/approve")
    approved_resp = await client.get("/api/v1/team-review/cases?status=approved")
    pending_resp = await client.get("/api/v1/team-review/cases?status=pending")

    assert approve_resp.status_code == 200
    assert approved_resp.status_code == 200
    assert [row["id"] for row in approved_resp.json()] == [first_case_id]
    assert pending_resp.status_code == 200
    assert [row["id"] for row in pending_resp.json()] == [second_case_id]


@pytest.mark.asyncio
async def test_team_review_approval_handles_null_scraped_at(
    client: AsyncClient,
    team_registry_file,
):
    batch_scraped_at = "2026-04-16T23:30:00+00:00"
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")
    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "sport": "basketball",
                "scope_league_id": "bulgaria_nbl",
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": "Rilski Sportist",
                "start_time": batch_scraped_at,
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "high",
                "similarity_score": 92,
                "evidence": ["Exact start time: 2026-04-16T23:30:00+00:00"],
                "status": "pending",
            }
        ),
        scraped_at=batch_scraped_at,
    )
    db = await get_db()
    await db.execute("UPDATE team_review_cases SET scraped_at = NULL WHERE id = ?", (case_id,))
    await db.commit()
    await odds_store.set_current_snapshot(batch_scraped_at)

    approve_resp = await client.post(f"/api/v1/team-review/cases/{case_id}/approve")
    approved_case = await odds_store.get_team_review_case(case_id)

    assert approve_resp.status_code == 200
    assert approved_case is not None
    assert approved_case.status == "approved"


@pytest.mark.asyncio
async def test_init_db_migrates_team_review_cases_to_nullable_suggested_name(tmp_path):
    await close_db()
    db_path = tmp_path / "legacy-team-review.db"

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE team_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmaker_id TEXT NOT NULL,
                raw_league_id TEXT NOT NULL,
                normalized_raw_league_id TEXT NOT NULL,
                scope_league_id TEXT NOT NULL,
                raw_team_name TEXT NOT NULL,
                normalized_raw_team_name TEXT NOT NULL,
                suggested_team_name TEXT NOT NULL,
                start_time TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                confidence TEXT NOT NULL,
                evidence TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                scraped_at TEXT,
                approved_at TEXT
            );
            """
        )
        await db.commit()

    upgrade_database(str(db_path))
    await init_db(str(db_path))
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("bulgaria_nbl", "Bulgaria NBL", "basketball", "Bulgaria")

    case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic.model_validate(
            {
                "bookmaker_id": "meridian",
                "raw_league_id": "NBL",
                "normalized_raw_league_id": "nbl",
                "sport": "basketball",
                "scope_league_id": "bulgaria_nbl",
                "raw_team_name": "Rilski Sport.",
                "normalized_raw_team_name": "Rilski Sport.",
                "suggested_team_name": None,
                "start_time": "2026-04-16T23:45:00+00:00",
                "reason_code": "candidate_team_match_same_start_time",
                "confidence": "medium",
                "similarity_score": 82,
                "evidence": ["Candidate team: Rilski Sportist"],
                "status": "pending",
            }
        ),
        scraped_at="2026-04-16T23:45:00+00:00",
    )

    inserted_case = await odds_store.get_team_review_case(case_id)

    assert inserted_case is not None
    assert inserted_case.suggested_team_name is None


@pytest.mark.asyncio
async def test_init_db_enables_foreign_keys_for_canonical_team_refs():
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    db = await get_db()
    pragma_row = await (await db.execute("PRAGMA foreign_keys")).fetchone()

    assert pragma_row is not None
    assert pragma_row[0] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO team_review_cases (
                bookmaker_id,
                raw_league_id,
                normalized_raw_league_id,
                sport,
                scope_league_id,
                raw_team_name,
                normalized_raw_team_name,
                suggested_team_id,
                start_time,
                reason_code,
                evidence,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meridian",
                "Euroleague",
                "euroleague",
                "basketball",
                "euroleague",
                "Invalid FK Team",
                "invalid fk team",
                999999,
                "2026-04-17T00:00:00+00:00",
                "candidate_team_match_same_start_time",
                "[]",
                "pending",
            ),
        )


@pytest.mark.asyncio
async def test_init_db_rebuilds_legacy_tables_with_canonical_team_foreign_keys(tmp_path):
    await close_db()
    db_path = tmp_path / "legacy-canonical-fk.db"

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE leagues (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                sport TEXT NOT NULL,
                country TEXT,
                is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE bookmakers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                website_url TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                league_id TEXT REFERENCES leagues(id),
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                start_time TIMESTAMP,
                status TEXT DEFAULT 'upcoming',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE team_review_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bookmaker_id TEXT REFERENCES bookmakers(id),
                raw_league_id TEXT NOT NULL,
                normalized_raw_league_id TEXT NOT NULL,
                scope_league_id TEXT,
                raw_team_name TEXT NOT NULL,
                normalized_raw_team_name TEXT NOT NULL,
                suggested_team_name TEXT,
                start_time TIMESTAMP,
                reason_code TEXT NOT NULL,
                confidence TEXT NOT NULL DEFAULT 'medium',
                evidence TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                declined_at TIMESTAMP
            );
            """
        )
        await db.commit()

    upgrade_database(str(db_path))
    await init_db(str(db_path))
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")

    db = await get_db()
    match_fks = await db.execute_fetchall("PRAGMA foreign_key_list(matches)")
    team_review_fks = await db.execute_fetchall("PRAGMA foreign_key_list(team_review_cases)")

    assert any(row[2] == "canonical_teams" and row[3] == "home_team_id" for row in match_fks)
    assert any(row[2] == "canonical_teams" and row[3] == "away_team_id" for row in match_fks)
    assert any(
        row[2] == "canonical_teams" and row[3] == "suggested_team_id" for row in team_review_fks
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO matches (
                id,
                league_id,
                sport,
                home_team_id,
                away_team_id,
                home_team,
                away_team
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-fk-match",
                "euroleague",
                "basketball",
                999999,
                999998,
                "Legacy Home",
                "Legacy Away",
            ),
        )
        await db.commit()
    await db.rollback()

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """
            INSERT INTO team_review_cases (
                bookmaker_id,
                raw_league_id,
                normalized_raw_league_id,
                sport,
                scope_league_id,
                raw_team_name,
                normalized_raw_team_name,
                suggested_team_id,
                start_time,
                reason_code,
                evidence,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "meridian",
                "Euroleague",
                "euroleague",
                "basketball",
                "euroleague",
                "Legacy FK Team",
                "legacy fk team",
                999999,
                "2026-04-17T00:30:00+00:00",
                "candidate_team_match_same_start_time",
                "[]",
                "pending",
            ),
        )
        await db.commit()
    await db.rollback()


# ── feature gate (issue #131): kill-switch + fitted-EV threshold ──────────


@pytest.mark.asyncio
async def test_get_scrape_settings_exposes_fitted_middle_gate(client: AsyncClient):
    """The settings response surfaces the new gate fields and their option bounds."""
    resp = await client.get("/api/v1/settings/scrape")

    assert resp.status_code == 200
    data = resp.json()
    # Schema-level default is True (preserves legacy persisted JSON), but the
    # user-facing default flows through config.py → False on a fresh seed.
    assert data["applied"]["enable_fitted_middles"] == settings.enable_fitted_middles
    assert (
        data["applied"]["min_fitted_middle_ev_percent"]
        == settings.min_fitted_middle_ev_percent
    )
    assert data["defaults"]["enable_fitted_middles"] == settings.enable_fitted_middles
    assert (
        data["defaults"]["min_fitted_middle_ev_percent"]
        == settings.min_fitted_middle_ev_percent
    )
    assert data["options"]["min_fitted_middle_ev_percent_min"] == 0.0
    assert data["options"]["min_fitted_middle_ev_percent_max"] == 100.0


@pytest.mark.asyncio
async def test_patch_scrape_settings_accepts_fitted_middle_gate(client: AsyncClient):
    """The PATCH endpoint accepts the gate fields and they roundtrip."""
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={
            "enable_fitted_middles": True,
            "min_fitted_middle_ev_percent": 3.5,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["applied"]["enable_fitted_middles"] is True
    assert data["applied"]["min_fitted_middle_ev_percent"] == 3.5

    get_resp = await client.get("/api/v1/settings/scrape")
    body = get_resp.json()
    assert body["applied"]["enable_fitted_middles"] is True
    assert body["applied"]["min_fitted_middle_ev_percent"] == 3.5


@pytest.mark.asyncio
async def test_patch_scrape_settings_rejects_min_fitted_middle_ev_percent_above_100(
    client: AsyncClient,
):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={"min_fitted_middle_ev_percent": 150.0},
    )
    assert resp.status_code == 422
    assert "min_fitted_middle_ev_percent" in resp.text


@pytest.mark.asyncio
async def test_patch_scrape_settings_rejects_negative_min_fitted_middle_ev_percent(
    client: AsyncClient,
):
    resp = await client.patch(
        "/api/v1/settings/scrape",
        json={"min_fitted_middle_ev_percent": -1.0},
    )
    # Pydantic ge=0 rejects this at parse time → 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_legacy_persisted_scrape_settings_without_gate_fields_load_with_schema_defaults(
    client: AsyncClient, monkeypatch
):
    """A pre-upgrade JSON blob without the new fields loads cleanly. Schema-level
    defaults are True / 0.0 (preserve existing dev installs)."""
    from app.services.runtime_settings import _settings_from_json

    legacy_json = (
        '{"enabled_bookmakers": ["mozzart"], "enabled_sports": ["basketball"], '
        '"scrape_market_scope": "all", "analysis_markets": ["all"], '
        '"scrape_lookahead_hours": 24, "scrape_interval_minutes": 10, '
        '"max_middle_opportunities_per_market": 10, "rate_limit_per_second": 1.0, '
        '"meridian_rate_limit_per_second": 2.0, '
        '"soccerbet_detail_mode": "partial", "merkurxtip_detail_mode": "partial", '
        '"pinnbet_detail_mode": "partial", "betole_detail_mode": "partial", '
        '"notification_gap_threshold": 1.5, "persist_inapp_notifications": false}'
    )
    parsed = _settings_from_json(legacy_json)
    # Schema-level default for enable_fitted_middles is True (legacy preservation).
    assert parsed.enable_fitted_middles is True
    assert parsed.min_fitted_middle_ev_percent == 0.0


def test_config_module_default_is_off():
    """The config-level default for the kill-switch must be OFF (per #131 product
    decision). Read directly from the Settings class to verify that the default
    isn't accidentally flipped by a future PR. Bypasses the autouse fixture
    which sets it to True for cycle tests."""
    from app.config import Settings

    fresh = Settings()
    assert fresh.enable_fitted_middles is False
    assert fresh.min_fitted_middle_ev_percent == 0.0
