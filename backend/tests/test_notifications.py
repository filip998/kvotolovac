from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from app.database import get_db
from app.models.schemas import (
    OpportunityLeg,
    ResolvedEventIn,
    TelegramNotificationProfileCreate,
    TelegramNotificationProfileOut,
)
from app.services.opportunity_analyzer import Opportunity
from app.services.notifications import (
    InAppNotificationProvider,
    NotificationProvider,
    NotificationService,
    TelegramBotAPIError,
    TelegramBotClient,
    TelegramBotConfigError,
    TelegramNotificationProvider,
    TelegramOpportunityDisplayContext,
    TelegramSendMessageResult,
    format_telegram_opportunity,
    telegram_opportunity_fingerprint,
    telegram_profile_matches_opportunity,
)
from app.store import odds_store


def _make_opportunity(
    gap: float,
    subject: str = "Lundberg",
    *,
    match_id: str = "m1",
    resolved_event_id: str | None = None,
    market_type: str = "player_points",
    subject_type: str | None = "player",
    line: float = 16.5,
    first_bookmaker: str = "mozzart",
    second_bookmaker: str = "meridian",
    middle_ev: float | None = None,
    middle_ev_rank: float | None = None,
) -> Opportunity:
    return Opportunity(
        sport="basketball",
        match_id=match_id,
        resolved_event_id=resolved_event_id,
        opportunity_type="middle",
        market_type=market_type,
        line=None,
        profit_margin=-0.04,
        middle_profit_margin=0.5,
        subject_type=subject_type,
        subject_name=subject,
        middle_ev=middle_ev,
        middle_ev_rank=middle_ev_rank,
        legs=[
            OpportunityLeg(
                bookmaker_id=first_bookmaker,
                market_type=market_type,
                outcome_code="over",
                line=line,
                odds=1.85,
            ),
            OpportunityLeg(
                bookmaker_id=second_bookmaker,
                market_type=market_type,
                outcome_code="under",
                line=line + gap,
                odds=2.00,
            ),
        ],
    )


def _make_arbitrage_opportunity(
    subject: str = "Match winner",
    *,
    match_id: str | None = None,
    resolved_event_id: str | None = None,
    market_type: str = "football_total_goals",
    subject_type: str | None = None,
    line: float = 2.5,
    profit_margin: float = 0.05,
) -> Opportunity:
    return Opportunity(
        sport="football",
        match_id=match_id or f"football-{subject}",
        resolved_event_id=resolved_event_id,
        opportunity_type="same_line_arbitrage",
        market_type=market_type,
        line=line,
        profit_margin=profit_margin,
        middle_profit_margin=None,
        subject_type=subject_type,
        subject_name=subject,
        legs=[
            OpportunityLeg(
                bookmaker_id="mozzart",
                market_type=market_type,
                outcome_code="over",
                line=line,
                odds=2.10,
            ),
            OpportunityLeg(
                bookmaker_id="meridian",
                market_type=market_type,
                outcome_code="under",
                line=line,
                odds=2.10,
            ),
        ],
    )


def _make_profile(
    *,
    bookmaker_ids: list[str] | None = None,
    min_gap: float = 0,
    min_roi_percent: float = 0,
) -> TelegramNotificationProfileOut:
    return TelegramNotificationProfileOut(
        id=1,
        label="Main",
        chat_id="12345",
        enabled=True,
        min_gap=min_gap,
        min_roi_percent=min_roi_percent,
        bookmaker_ids=bookmaker_ids or [],
    )


@pytest.mark.asyncio
async def test_notification_provider_interface():
    """InAppNotificationProvider implements the abstract interface."""
    provider = InAppNotificationProvider()
    assert isinstance(provider, NotificationProvider)


@pytest.mark.asyncio
async def test_in_app_provider_stores_notification():
    provider = InAppNotificationProvider()
    await provider.send("opportunity", "Test Alert", "body", {"gap": 2.0})
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1
    assert notifs[0].title == "Test Alert"


@pytest.mark.asyncio
async def test_notification_service_threshold_filter():
    service = NotificationService(gap_threshold=2.0)
    service.register_provider(InAppNotificationProvider())

    opportunities = [_make_opportunity(1.0), _make_opportunity(2.5)]
    count = await service.notify_opportunities(opportunities)
    assert count == 1  # only gap=2.5 meets threshold

    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1


@pytest.mark.asyncio
async def test_notification_service_no_providers():
    service = NotificationService(gap_threshold=1.0)
    opportunities = [_make_opportunity(2.0)]
    count = await service.notify_opportunities(opportunities)
    assert count == 0


@pytest.mark.asyncio
async def test_notification_service_multiple_providers():
    service = NotificationService(gap_threshold=1.0)
    service.register_provider(InAppNotificationProvider())
    service.register_provider(InAppNotificationProvider())

    opportunities = [_make_opportunity(2.0)]
    count = await service.notify_opportunities(opportunities)
    assert count == 1

    # Both providers should have stored a notification
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 2


def test_telegram_profile_filtering_and_fingerprint_stability():
    opportunity = _make_opportunity(2.0)

    assert telegram_profile_matches_opportunity(
        _make_profile(bookmaker_ids=["mozzart", "meridian"], min_gap=2, min_roi_percent=10),
        opportunity,
    )
    assert not telegram_profile_matches_opportunity(
        _make_profile(bookmaker_ids=["mozzart"], min_gap=2, min_roi_percent=10),
        opportunity,
    )
    assert not telegram_profile_matches_opportunity(
        _make_profile(bookmaker_ids=["mozzart", "meridian"], min_gap=2.5),
        opportunity,
    )
    assert not telegram_profile_matches_opportunity(
        _make_profile(bookmaker_ids=["mozzart", "meridian"], min_roi_percent=60),
        opportunity,
    )

    fitted_middle = replace(
        opportunity,
        middle_ev=0.025,
        middle_hit_probability=0.20,
        middle_model_confidence="medium",
        middle_model_diagnostics={"mode": "fitted", "model_family": "normal"},
        middle_ev_rank=0.02125,
    )
    assert telegram_profile_matches_opportunity(
        _make_profile(bookmaker_ids=["mozzart", "meridian"], min_gap=99, min_roi_percent=99),
        fitted_middle,
    )
    assert not telegram_profile_matches_opportunity(
        TelegramNotificationProfileOut(
            id=2,
            label="EV",
            chat_id="12345",
            enabled=True,
            min_gap=0,
            min_roi_percent=0,
            min_middle_ev_percent=3.0,
            bookmaker_ids=["mozzart", "meridian"],
        ),
        fitted_middle,
    )

    changed_odds = replace(
        opportunity,
        profit_margin=0.99,
        middle_profit_margin=0.75,
        legs=[
            opportunity.legs[0].model_copy(update={"odds": 1.5}),
            opportunity.legs[1].model_copy(update={"odds": 2.5}),
        ],
    )
    changed_line = replace(
        opportunity,
        legs=[
            opportunity.legs[0],
            opportunity.legs[1].model_copy(update={"line": 21.5}),
        ],
    )

    assert telegram_opportunity_fingerprint(opportunity) == telegram_opportunity_fingerprint(
        changed_odds
    )
    assert telegram_opportunity_fingerprint(opportunity) != telegram_opportunity_fingerprint(
        changed_line
    )


def test_telegram_formatter_uses_matchup_and_escapes_html():
    opportunity = _make_arbitrage_opportunity(
        "Aaron <Doornekamp>",
        market_type="player_rebounds",
        subject_type="player",
        profit_margin=0.106,
    )
    text = format_telegram_opportunity(
        opportunity,
        TelegramOpportunityDisplayContext(
            home_team="Gran <Canaria>",
            away_team="Valencia & Co",
            fallback_label="evt_hidden",
        ),
    )

    assert "<b>Aaron &lt;Doornekamp&gt; - rebounds</b>" in text
    assert "Gran &lt;Canaria&gt; vs Valencia &amp; Co" in text
    assert "ROI 10.60%" in text
    assert "1) <b>mozzart</b> Over 2.5 @ 2.1" in text
    assert "evt_hidden" not in text


def test_telegram_formatter_falls_back_to_internal_event_id():
    opportunity = _make_arbitrage_opportunity(
        "Aaron Doornekamp",
        resolved_event_id="evt_8fda3e10a634",
        market_type="player_rebounds",
        subject_type="player",
    )
    text = format_telegram_opportunity(
        opportunity,
        TelegramOpportunityDisplayContext(fallback_label="evt_8fda3e10a634"),
    )

    assert "Event: <code>evt_8fda3e10a634</code>" in text


@pytest.mark.asyncio
async def test_telegram_display_context_prefers_resolved_event_labels():
    await odds_store.upsert_league("euroleague", "EuroLeague", "basketball")
    await odds_store.upsert_match(
        id="m1",
        league_id="euroleague",
        home_team="Canonical Home",
        away_team="Canonical Away",
        sport="basketball",
        start_time="2026-05-08T18:00:00Z",
    )
    event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt_display",
            sport="basketball",
            start_time="2026-05-08T18:00:00Z",
            primary_match_id="m1",
            display_home_team="Display Home",
            display_away_team="Display Away",
            display_league_name="Display League",
        )
    )

    contexts = await odds_store.get_telegram_opportunity_display_contexts(
        [(event_id, "m1")]
    )

    assert contexts[(event_id, "m1")] == {
        "home_team": "Display Home",
        "away_team": "Display Away",
        "league_name": "Display League",
        "start_time": "2026-05-08T18:00:00Z",
        "fallback_label": "evt_display",
    }


@pytest.mark.asyncio
async def test_telegram_bot_client_sends_html_message_with_link_previews_disabled():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramBotClient(
            token="secret-token",
            api_base_url="https://telegram.test",
            http_client=http_client,
        )
        result = await client.send_message(chat_id="123", text="<b>Hello</b>")

    assert result.message_id == 42
    assert requests[0].url.path == "/botsecret-token/sendMessage"
    payload = json_payload(requests[0])
    assert payload["chat_id"] == "123"
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}


@pytest.mark.asyncio
async def test_telegram_bot_client_redacts_token_from_api_errors():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = TelegramBotClient(
            token="secret-token",
            api_base_url="https://telegram.test",
            http_client=http_client,
        )
        with pytest.raises(TelegramBotAPIError) as exc_info:
            await client.send_message(chat_id="123", text="Hello")

    assert "Unauthorized" in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_telegram_bot_client_missing_token():
    client = TelegramBotClient(token="")

    with pytest.raises(TelegramBotConfigError):
        await client.send_message(chat_id="123", text="Hello")


@pytest.mark.asyncio
async def test_telegram_provider_dedupes_sent_deliveries():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
            bookmaker_ids=["mozzart", "meridian"],
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(chat_id)
            return TelegramSendMessageResult(message_id=len(calls))

    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]
    opportunity = _make_opportunity(2.0)

    assert await provider.send_opportunity(opportunity, publish_id="pub-1") is True
    assert await provider.send_opportunity(opportunity, publish_id="pub-2") is False
    assert calls == ["123"]

    rows = await (await get_db()).execute_fetchall(
        "SELECT status, attempt_count, telegram_message_id FROM telegram_notification_deliveries"
    )
    assert [(row["status"], row["attempt_count"], row["telegram_message_id"]) for row in rows] == [
        ("sent", 1, 1)
    ]


@pytest.mark.asyncio
async def test_telegram_provider_records_failure_and_retries_later():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
        )
    )
    attempts = 0

    class FlakyTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TelegramBotAPIError("Telegram HTTP 500: server error")
            return TelegramSendMessageResult(message_id=99)

    provider = TelegramNotificationProvider(bot_client=FlakyTelegramClient())  # type: ignore[arg-type]
    opportunity = _make_opportunity(2.0)

    assert await provider.send_opportunity(opportunity, publish_id="pub-1") is False
    assert await provider.send_opportunity(opportunity, publish_id="pub-2") is True

    rows = await (await get_db()).execute_fetchall(
        """SELECT status, attempt_count, telegram_message_id, error
           FROM telegram_notification_deliveries"""
    )
    assert [(row["status"], row["attempt_count"], row["telegram_message_id"]) for row in rows] == [
        ("sent", 2, 99)
    ]
    assert rows[0]["error"] is None


@pytest.mark.asyncio
async def test_telegram_provider_records_missing_token_as_failed_delivery():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
        )
    )
    provider = TelegramNotificationProvider(bot_client=TelegramBotClient(token=""))

    assert await provider.send_opportunity(_make_opportunity(2.0), publish_id="pub-1") is False

    rows = await (await get_db()).execute_fetchall(
        "SELECT status, attempt_count, error FROM telegram_notification_deliveries"
    )
    assert rows[0]["status"] == "failed"
    assert rows[0]["attempt_count"] == 1
    assert "token is not configured" in rows[0]["error"]


@pytest.mark.asyncio
async def test_telegram_provider_sends_non_middles_individually_and_middles_as_digest():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
        )
    )
    calls: list[tuple[str, str]] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append((chat_id, text))
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        _make_arbitrage_opportunity("arb-1"),
        _make_arbitrage_opportunity("arb-2"),
        *[_make_opportunity(3.0, subject=f"middle-{index}") for index in range(12)],
    ]
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 12
    assert len(calls) == 3
    assert "middle digest" not in calls[0][1]
    assert "middle digest" not in calls[1][1]
    assert "middle digest" in calls[2][1]
    assert "Showing 10 new middle groups of 12 matched" in calls[2][1]
    assert "+2 matched groups remain eligible later" in calls[2][1]

    rows = await (await get_db()).execute_fetchall(
        """SELECT status, telegram_message_id, COUNT(*) AS c
           FROM telegram_notification_deliveries
           GROUP BY status, telegram_message_id
           ORDER BY telegram_message_id"""
    )
    assert [(row["status"], row["telegram_message_id"], row["c"]) for row in rows] == [
        ("sent", 1, 1),
        ("sent", 2, 1),
        ("sent", 3, 10),
    ]


@pytest.mark.asyncio
async def test_telegram_provider_caps_profile_messages_per_publish():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(text)
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        *[_make_arbitrage_opportunity(f"arb-{index}") for index in range(8)],
        *[_make_opportunity(3.0, subject=f"middle-{index}") for index in range(20)],
    ]
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 15
    assert len(calls) == 6
    assert sum("middle digest" in call for call in calls) == 1

    rows = await (await get_db()).execute_fetchall(
        "SELECT status, COUNT(*) AS c FROM telegram_notification_deliveries GROUP BY status"
    )
    assert [(row["status"], row["c"]) for row in rows] == [("sent", 15)]


@pytest.mark.asyncio
async def test_telegram_provider_groups_same_player_event_market_in_one_message():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_roi_percent=1,
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(text)
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        _make_arbitrage_opportunity(
            "Aaron Doornekamp",
            match_id="m1",
            market_type="player_rebounds",
            subject_type="player",
            line=4.5,
            profit_margin=0.106,
        ),
        _make_arbitrage_opportunity(
            "Aaron Doornekamp",
            match_id="m1",
            market_type="player_rebounds",
            subject_type="player",
            line=5.5,
            profit_margin=0.0744,
        ),
    ]
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 2
    assert len(calls) == 1
    assert "<b>Aaron Doornekamp - rebounds</b>" in calls[0]
    assert "More new options:" in calls[0]
    assert "ROI 7.44%" in calls[0]

    rows = await (await get_db()).execute_fetchall(
        """SELECT telegram_message_id, COUNT(*) AS c
           FROM telegram_notification_deliveries
           GROUP BY telegram_message_id"""
    )
    assert [(row["telegram_message_id"], row["c"]) for row in rows] == [(1, 2)]


@pytest.mark.asyncio
async def test_telegram_provider_does_not_group_different_player_markets():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_roi_percent=1,
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(text)
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        _make_arbitrage_opportunity(
            "Aaron Doornekamp",
            match_id="m1",
            market_type="player_rebounds",
            subject_type="player",
            line=4.5,
        ),
        _make_arbitrage_opportunity(
            "Aaron Doornekamp",
            match_id="m1",
            market_type="player_assists",
            subject_type="player",
            line=4.5,
        ),
    ]
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 2
    assert len(calls) == 2
    assert "More new options:" not in "\n".join(calls)


@pytest.mark.asyncio
async def test_telegram_provider_does_not_group_subjectless_event_markets():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_roi_percent=1,
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(text)
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        _make_arbitrage_opportunity(
            "Event totals",
            match_id="football-match",
            line=2.5,
        ),
        _make_arbitrage_opportunity(
            "Event totals",
            match_id="football-match",
            line=3.5,
        ),
    ]
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 2
    assert len(calls) == 2
    assert "More new options:" not in "\n".join(calls)


@pytest.mark.asyncio
async def test_telegram_provider_applies_top_limit_to_groups_not_raw_options():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_roi_percent=1,
        )
    )
    calls: list[str] = []

    class StubTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            calls.append(text)
            return TelegramSendMessageResult(message_id=len(calls))

    opportunities = [
        _make_arbitrage_opportunity(
            "Player A",
            match_id="m1",
            market_type="player_rebounds",
            subject_type="player",
            line=line,
            profit_margin=margin,
        )
        for line, margin in [(4.5, 0.30), (5.5, 0.29), (6.5, 0.28)]
    ]
    opportunities.extend(
        _make_arbitrage_opportunity(
            f"Player {name}",
            match_id=f"m{name}",
            market_type="player_rebounds",
            subject_type="player",
            profit_margin=margin,
        )
        for name, margin in [
            ("B", 0.27),
            ("C", 0.26),
            ("D", 0.25),
            ("E", 0.24),
            ("F", 0.23),
        ]
    )
    provider = TelegramNotificationProvider(bot_client=StubTelegramClient())  # type: ignore[arg-type]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 7
    assert len(calls) == 5
    combined = "\n".join(calls)
    for expected in ("Player A", "Player B", "Player C", "Player D", "Player E"):
        assert expected in combined
    assert "Player F" not in combined
    assert calls[0].index("ROI 29.00%") < calls[0].index("ROI 28.00%")


@pytest.mark.asyncio
async def test_telegram_provider_rate_limit_pauses_profile_and_stops_loop():
    await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id="123",
            min_gap=1,
            min_roi_percent=1,
        )
    )
    calls = 0

    class RateLimitedTelegramClient:
        async def send_message(self, *, chat_id: str, text: str) -> TelegramSendMessageResult:
            nonlocal calls
            calls += 1
            raise TelegramBotAPIError(
                "Telegram HTTP 429: Too Many Requests",
                retry_after=60,
            )

    provider = TelegramNotificationProvider(bot_client=RateLimitedTelegramClient())  # type: ignore[arg-type]
    opportunities = [
        _make_arbitrage_opportunity("arb-1"),
        _make_arbitrage_opportunity("arb-2"),
        _make_opportunity(3.0, subject="middle-1"),
    ]

    assert await provider.send_opportunities(opportunities, publish_id="pub-1") == 0
    assert await provider.send_opportunities(opportunities, publish_id="pub-2") == 0
    assert calls == 1

    db = await get_db()
    profile_rows = await db.execute_fetchall(
        "SELECT rate_limited_until, last_delivery_error FROM telegram_notification_profiles"
    )
    assert profile_rows[0]["rate_limited_until"] is not None
    assert "Too Many Requests" in profile_rows[0]["last_delivery_error"]
    delivery_rows = await db.execute_fetchall(
        "SELECT status, attempt_count, error FROM telegram_notification_deliveries"
    )
    assert [(row["status"], row["attempt_count"]) for row in delivery_rows] == [
        ("failed", 1)
    ]
    assert "Too Many Requests" in delivery_rows[0]["error"]


def json_payload(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())
