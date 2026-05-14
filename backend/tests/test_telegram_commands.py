from __future__ import annotations

import asyncio

import pytest

from app.models.schemas import (
    BookmakerOut,
    BookmakerCoverageOut,
    OpportunityLeg,
    OpportunityOut,
    ScanProgressOut,
    SystemStatus,
    TelegramNotificationProfileCreate,
    TelegramNotificationProfileUpdate,
)
from app.services.notifications import TelegramBotAPIError, TelegramSendMessageResult
from app.services.notifications import TelegramOpportunityMessage
from app.services.telegram_commands import (
    TELEGRAM_COMMAND_BOOKMAKERS,
    TELEGRAM_COMMAND_NOTIFICATIONS,
    TELEGRAM_COMMAND_PROFILE,
    TELEGRAM_COMMAND_REFRESH,
    TELEGRAM_COMMAND_STATUS,
    TelegramCommandDispatcher,
    TelegramCommandPoller,
    parse_telegram_command_update,
    profile_allows_telegram_command,
    wait_for_telegram_command_tasks,
)
from app.store import odds_store


class StubBotClient:
    def __init__(
        self,
        *,
        fail_first_send: bool = False,
        fail_send_numbers: set[int] | None = None,
    ) -> None:
        self.messages: list[tuple[str, str]] = []
        self.fail_first_send = fail_first_send
        self.fail_send_numbers = fail_send_numbers or set()
        self.send_attempts = 0

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
    ) -> TelegramSendMessageResult:
        self.send_attempts += 1
        if (
            (self.fail_first_send and self.send_attempts == 1)
            or self.send_attempts in self.fail_send_numbers
        ):
            raise RuntimeError("telegram send failed")
        self.messages.append((chat_id, text))
        return TelegramSendMessageResult(message_id=len(self.messages))


REGISTERED_COMMANDS = {
    "help",
    "status",
    "profile",
    "bookmakers",
    "refresh",
    "notifications",
}


class PollerBotClient:
    def __init__(
        self,
        updates: list[dict],
        *,
        fail_first_delete_webhook: bool = False,
        fail_poll_after_delete_failure: bool = False,
        fail_first_get_me: bool = False,
        fail_get_me_call_numbers: set[int] | None = None,
        fail_first_initial_get_updates: bool = False,
    ) -> None:
        self.updates = updates
        self.get_updates_calls = 0
        self.delete_webhook_calls = 0
        self.get_me_calls = 0
        self.offsets: list[int | None] = []
        self.fail_first_delete_webhook = fail_first_delete_webhook
        self.fail_poll_after_delete_failure = fail_poll_after_delete_failure
        self.fail_get_me_call_numbers = fail_get_me_call_numbers or (
            {1} if fail_first_get_me else set()
        )
        self.fail_first_initial_get_updates = fail_first_initial_get_updates
        self.waiting_for_second_poll = asyncio.Event()
        self.get_me_failed = asyncio.Event()
        self.get_me_succeeded = asyncio.Event()
        self.get_me_retry_succeeded = asyncio.Event()
        self.initial_get_updates_failed = asyncio.Event()
        self._never = asyncio.Event()

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> bool:
        self.delete_webhook_calls += 1
        if self.fail_first_delete_webhook and self.delete_webhook_calls == 1:
            raise TelegramBotAPIError("Telegram HTTP 409: webhook active")
        return True

    async def get_me(self) -> dict:
        self.get_me_calls += 1
        if self.get_me_calls in self.fail_get_me_call_numbers:
            self.get_me_failed.set()
            raise TelegramBotAPIError("Telegram HTTP 502: temporary getMe failure")
        self.get_me_succeeded.set()
        if self.get_me_calls > 1:
            self.get_me_retry_succeeded.set()
        return {"username": "KvotoLovacBot"}

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout_seconds: int = 25,
        limit: int = 100,
    ) -> list[dict]:
        self.get_updates_calls += 1
        self.offsets.append(offset)
        if (
            self.fail_first_initial_get_updates
            and offset == -1
            and self.get_updates_calls == 1
        ):
            self.initial_get_updates_failed.set()
            raise TelegramBotAPIError("Telegram HTTP 502: initial poll failed")
        if (
            self.fail_poll_after_delete_failure
            and self.delete_webhook_calls == 1
        ):
            raise TelegramBotAPIError("Telegram HTTP 409: webhook active")
        if self.get_updates_calls == 1:
            return self.updates
        self.waiting_for_second_poll.set()
        await self._never.wait()
        return []


class FailingDispatcher:
    def __init__(self) -> None:
        self.seen_update_ids: list[int] = []
        self.failed = asyncio.Event()

    def set_bot_username(self, username: str | None) -> None:
        pass

    async def dispatch_update(self, update: dict) -> None:
        self.seen_update_ids.append(update["update_id"])
        self.failed.set()
        raise RuntimeError("transient failure")


class RecordingDispatcher:
    def __init__(self) -> None:
        self.seen_update_ids: list[int] = []

    def set_bot_username(self, username: str | None) -> None:
        pass

    async def dispatch_update(self, update: dict) -> None:
        self.seen_update_ids.append(update["update_id"])


class StubScheduler:
    def __init__(self, *, in_progress: bool = False, is_running: bool = True) -> None:
        self._in_progress = in_progress
        self._is_running = is_running
        self.run_count = 0
        self.finished = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_cycle_in_progress(self) -> bool:
        return self._in_progress

    def progress_snapshot(self) -> ScanProgressOut:
        return ScanProgressOut(
            in_progress=self._in_progress,
            phase="scraping",
            total_tasks=5,
            completed_tasks=2,
            failed_tasks=1,
            active_tasks=1,
        )

    async def run_cycle(self) -> dict:
        self.run_count += 1
        self._in_progress = True
        self.finished.set()
        return {"ok": True}


class SlowScheduler(StubScheduler):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def run_cycle(self) -> dict:
        self.run_count += 1
        self._in_progress = True
        self.finished.set()
        await self.release.wait()
        self._in_progress = False
        return {"ok": True}


def telegram_update(text: str, *, update_id: int = 1, chat_id: str = "123") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "chat": {"id": chat_id},
            "from": {"id": 99, "username": "tester"},
            "text": text,
        },
    }


def make_opportunity(
    opportunity_id: int,
    *,
    first_bookmaker: str = "mozzart",
    second_bookmaker: str = "meridian",
    profit_margin: float = 0.08,
    match_id: str | None = None,
) -> OpportunityOut:
    match_id = match_id or f"m{opportunity_id}"
    return OpportunityOut(
        id=opportunity_id,
        sport="basketball",
        match_id=match_id,
        home_team=f"Home {opportunity_id}",
        away_team=f"Away {opportunity_id}",
        league_name="ABA",
        start_time="2026-04-10T20:00:00+00:00",
        opportunity_type="arbitrage",
        market_type="game_total",
        line=154.5,
        profit_margin=profit_margin,
        middle_profit_margin=None,
        market_keys=[f"total:{opportunity_id}"],
        legs=[
            OpportunityLeg(
                bookmaker_id=first_bookmaker,
                market_type="game_total",
                outcome_code="over",
                odds=2.1,
                line=154.5,
            ),
            OpportunityLeg(
                bookmaker_id=second_bookmaker,
                market_type="game_total",
                outcome_code="under",
                odds=2.1,
                line=154.5,
            ),
        ],
    )


def make_middle_opportunity(
    opportunity_id: int,
    *,
    middle_ev: float,
    middle_ev_rank: float,
) -> OpportunityOut:
    return OpportunityOut(
        id=opportunity_id,
        sport="basketball",
        match_id=f"middle-{opportunity_id}",
        home_team=f"Middle Home {opportunity_id}",
        away_team=f"Middle Away {opportunity_id}",
        league_name="ABA",
        start_time="2026-04-10T20:00:00+00:00",
        opportunity_type="middle",
        market_type="player_points",
        subject_type="player",
        subject_name=f"Player {opportunity_id}",
        profit_margin=-0.04,
        middle_profit_margin=0.5,
        middle_ev=middle_ev,
        middle_ev_rank=middle_ev_rank,
        market_keys=[f"player_points:{opportunity_id}"],
        legs=[
            OpportunityLeg(
                bookmaker_id="mozzart",
                market_type="player_points",
                outcome_code="over",
                odds=1.85,
                line=16.5,
            ),
            OpportunityLeg(
                bookmaker_id="meridian",
                market_type="player_points",
                outcome_code="under",
                odds=2.0,
                line=18.5,
            ),
        ],
    )


async def create_command_profile(
    *,
    chat_id: str = "123",
    preset: str = "admin",
    allowed_commands: list[str] | None = None,
):
    return await odds_store.create_telegram_notification_profile(
        TelegramNotificationProfileCreate(
            label="Main",
            chat_id=chat_id,
            command_permission_preset=preset,
            allowed_commands=allowed_commands or [],
        )
    )


def test_parse_telegram_command_accepts_mentions_and_args():
    request = parse_telegram_command_update(
        telegram_update("/notifications@KvotoLovacBot 5"),
        bot_username="kvotolovacbot",
    )

    assert request is not None
    assert request.command == TELEGRAM_COMMAND_NOTIFICATIONS
    assert request.args == "5"
    assert request.chat_id == "123"


def test_parse_telegram_command_ignores_other_bot_mentions():
    assert (
        parse_telegram_command_update(
            telegram_update("/refresh@OtherBot"),
            bot_username="kvotolovacbot",
        )
        is None
    )


def test_parse_telegram_command_ignores_mentions_when_bot_username_unknown():
    assert parse_telegram_command_update(telegram_update("/refresh@OtherBot")) is None


@pytest.mark.asyncio
async def test_dispatcher_silently_ignores_unauthorized_chat():
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/refresh"))

    assert bot.messages == []


@pytest.mark.asyncio
async def test_dispatcher_dedupes_update_ids():
    await create_command_profile()
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )
    update = telegram_update("/help", update_id=7)

    await dispatcher.dispatch_update(update)
    await dispatcher.dispatch_update(update)

    assert len(bot.messages) == 1
    assert "/status" in bot.messages[0][1]
    assert "/refresh" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_custom_profile_allows_only_configured_commands():
    profile = await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )

    assert profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_NOTIFICATIONS,
        registered_commands=REGISTERED_COMMANDS,
    )
    assert not profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_REFRESH,
        registered_commands=REGISTERED_COMMANDS,
    )
    assert not profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_STATUS,
        registered_commands=REGISTERED_COMMANDS,
    )
    assert not profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_BOOKMAKERS,
        registered_commands=REGISTERED_COMMANDS,
    )
    assert not profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_PROFILE,
        registered_commands=REGISTERED_COMMANDS,
    )


@pytest.mark.asyncio
async def test_custom_profile_can_allow_status_command():
    profile = await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_STATUS],
    )

    assert profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_STATUS,
        registered_commands=REGISTERED_COMMANDS,
    )
    assert not profile_allows_telegram_command(
        profile,
        TELEGRAM_COMMAND_REFRESH,
        registered_commands=REGISTERED_COMMANDS,
    )


@pytest.mark.asyncio
async def test_status_command_returns_idle_system_status(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile()

    async def fake_get_system_status(
        *,
        scheduler_running: bool = False,
        scan_progress: ScanProgressOut | None = None,
    ) -> SystemStatus:
        return SystemStatus(
            status="ok <safe>",
            last_scrape_at="2026-05-13T17:15:32<&",
            total_matches=824,
            total_odds=26186,
            total_opportunities=89,
            active_bookmakers=13,
            scheduler_running=scheduler_running,
            scan=scan_progress or ScanProgressOut(),
        )

    monkeypatch.setattr(odds_store, "get_system_status", fake_get_system_status)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(is_running=False),
    )

    await dispatcher.dispatch_update(telegram_update("/status"))

    assert len(bot.messages) == 1
    message = bot.messages[0][1]
    assert "<b>KvotoLovac status</b>" in message
    assert "Backend: OK &lt;SAFE&gt;" in message
    assert "Scheduler: stopped" in message
    assert "Last scrape: 2026-05-13T17:15:32&lt;&amp;" in message
    assert "Totals: 824 matches · 26186 odds · 89 opportunities · 13 bookmakers" in message
    assert "Cycle: idle" in message


@pytest.mark.asyncio
async def test_status_command_returns_in_progress_system_status(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile()

    async def fake_get_system_status(
        *,
        scheduler_running: bool = False,
        scan_progress: ScanProgressOut | None = None,
    ) -> SystemStatus:
        return SystemStatus(
            status="ok",
            total_matches=10,
            total_odds=20,
            total_opportunities=3,
            active_bookmakers=2,
            scheduler_running=scheduler_running,
            scan=scan_progress or ScanProgressOut(),
        )

    monkeypatch.setattr(odds_store, "get_system_status", fake_get_system_status)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(in_progress=True, is_running=True),
    )

    await dispatcher.dispatch_update(telegram_update("/status"))

    message = bot.messages[0][1]
    assert "Scheduler: running" in message
    assert "Cycle: scraping — 2/5 completed, 1 active, 1 failed" in message


@pytest.mark.asyncio
async def test_status_command_respects_custom_profile_allowlist():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/status"))

    assert bot.messages == [("123", "/status is not enabled for Main.")]


@pytest.mark.asyncio
async def test_bookmakers_command_returns_coverage(monkeypatch: pytest.MonkeyPatch):
    await create_command_profile()

    async def fake_get_bookmaker_coverage() -> list[BookmakerCoverageOut]:
        return [
            BookmakerCoverageOut(
                id="mozzart",
                name="Mozzart <safe>",
                current_match_count=143,
                last_seen_at="2026-05-13T18:00:00<&",
                current_snapshot_at="2026-05-13T18:00:00<&",
            ),
            BookmakerCoverageOut(
                id="pinnbet",
                name="PinnBet",
                current_match_count=0,
                last_seen_at="2026-05-13T17:30:00",
                current_snapshot_at="2026-05-13T18:00:00<&",
            ),
            BookmakerCoverageOut(
                id="newbook",
                name="NewBook",
                current_match_count=0,
                last_seen_at=None,
                current_snapshot_at="2026-05-13T18:00:00<&",
            ),
        ]

    monkeypatch.setattr(odds_store, "get_bookmaker_coverage", fake_get_bookmaker_coverage)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/bookmakers"))

    assert len(bot.messages) == 1
    message = bot.messages[0][1]
    assert "<b>Bookmaker coverage</b>" in message
    assert "Current snapshot: 2026-05-13T18:00:00&lt;&amp;" in message
    assert "✅ Mozzart &lt;safe&gt; — 143 current matches" in message
    assert "last seen 2026-05-13T18:00:00&lt;&amp;" in message
    assert "⚠️ PinnBet — no current rows · last seen 2026-05-13T17:30:00" in message
    assert "⚪ NewBook — no current rows · last seen never" in message


@pytest.mark.asyncio
async def test_bookmakers_command_returns_empty_state(monkeypatch: pytest.MonkeyPatch):
    await create_command_profile()

    async def fake_get_bookmaker_coverage() -> list[BookmakerCoverageOut]:
        return []

    monkeypatch.setattr(odds_store, "get_bookmaker_coverage", fake_get_bookmaker_coverage)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/bookmakers"))

    assert bot.messages == [("123", "No active bookmakers configured.")]


@pytest.mark.asyncio
async def test_bookmakers_command_rejects_arguments(monkeypatch: pytest.MonkeyPatch):
    await create_command_profile()

    async def fail_get_bookmaker_coverage() -> list[BookmakerCoverageOut]:
        raise AssertionError("/bookmakers arguments should not load coverage")

    monkeypatch.setattr(odds_store, "get_bookmaker_coverage", fail_get_bookmaker_coverage)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/bookmakers stale"))

    assert bot.messages == [("123", "Usage: /bookmakers")]


@pytest.mark.asyncio
async def test_bookmakers_command_respects_custom_profile_allowlist():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/bookmakers"))

    assert bot.messages == [("123", "/bookmakers is not enabled for Main.")]


@pytest.mark.asyncio
async def test_help_includes_bookmakers_for_custom_profile():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_BOOKMAKERS],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/help"))

    assert "/bookmakers" in bot.messages[0][1]
    assert "/notifications" not in bot.messages[0][1]


@pytest.mark.asyncio
async def test_profile_command_admin_returns_current_profile(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile(chat_id="secret-chat-789")

    async def fail_get_bookmakers(active_only: bool = True) -> list[BookmakerOut]:
        raise AssertionError("/profile with all bookmakers should not load names")

    monkeypatch.setattr(odds_store, "get_bookmakers", fail_get_bookmakers)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(
        telegram_update("/profile", chat_id="secret-chat-789")
    )

    assert len(bot.messages) == 1
    message = bot.messages[0][1]
    assert "<b>Telegram profile</b>" in message
    assert "Label: Main" in message
    assert "ROI ≥ 0%" in message
    assert "Middle EV ≥ 0%" in message
    assert "Middle gap ≥ 0" in message
    assert "Bookmakers: all" in message
    assert "Commands: admin (all commands)" in message
    assert "secret-chat-789" not in message


@pytest.mark.asyncio
async def test_profile_command_returns_filters_commands_and_delivery_state(
    monkeypatch: pytest.MonkeyPatch,
):
    profile = await create_command_profile(
        chat_id="profile-chat-456",
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_PROFILE, TELEGRAM_COMMAND_STATUS],
    )
    await odds_store.update_telegram_notification_profile(
        profile.id,
        TelegramNotificationProfileUpdate(
            label="Ops <main>",
            chat_id=profile.chat_id,
            min_gap=2.5,
            min_roi_percent=1.25,
            min_middle_ev_percent=3.5,
            bookmaker_ids=["mozzart", "unknown"],
            command_permission_preset="custom",
            allowed_commands=[TELEGRAM_COMMAND_PROFILE, TELEGRAM_COMMAND_STATUS],
        ),
    )
    await odds_store.mark_telegram_profile_rate_limited(
        profile_id=profile.id,
        retry_after_seconds=3600,
        error="Telegram <bad>\nretry",
    )
    get_bookmakers_calls: list[bool] = []

    async def fake_get_bookmakers(active_only: bool = True) -> list[BookmakerOut]:
        get_bookmakers_calls.append(active_only)
        return [
            BookmakerOut(
                id="mozzart",
                name="Mozzart <safe>",
                is_active=False,
            )
        ]

    monkeypatch.setattr(odds_store, "get_bookmakers", fake_get_bookmakers)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(
        telegram_update("/profile", chat_id="profile-chat-456")
    )

    assert get_bookmakers_calls == [False]
    message = bot.messages[0][1]
    assert "Label: Ops &lt;main&gt;" in message
    assert "ROI ≥ 1.25%" in message
    assert "Middle EV ≥ 3.5%" in message
    assert "Middle gap ≥ 2.5" in message
    assert "Bookmakers: Mozzart &lt;safe&gt;, unknown" in message
    assert "Commands: custom — /profile, /status" in message
    assert "Rate limited until:" in message
    rate_limit_line = next(
        line for line in message.splitlines() if line.startswith("Rate limited until:")
    )
    assert rate_limit_line.endswith("Z")
    assert "Last delivery error: Telegram &lt;bad&gt; retry" in message
    assert "profile-chat-456" not in message


@pytest.mark.asyncio
async def test_profile_command_hides_expired_rate_limit():
    profile = await create_command_profile()
    await odds_store.mark_telegram_profile_rate_limited(
        profile_id=profile.id,
        retry_after_seconds=-3600,
        error="old error",
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/profile"))

    message = bot.messages[0][1]
    assert "Rate limited until:" not in message
    assert "Last delivery error: old error" in message


@pytest.mark.asyncio
async def test_profile_command_rejects_arguments(monkeypatch: pytest.MonkeyPatch):
    profile = await create_command_profile()
    await odds_store.update_telegram_notification_profile(
        profile.id,
        TelegramNotificationProfileUpdate(
            label=profile.label,
            chat_id=profile.chat_id,
            bookmaker_ids=["mozzart"],
        ),
    )

    async def fail_get_bookmakers(active_only: bool = True) -> list[BookmakerOut]:
        raise AssertionError("/profile arguments should not load bookmakers")

    monkeypatch.setattr(odds_store, "get_bookmakers", fail_get_bookmakers)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/profile full"))

    assert bot.messages == [("123", "Usage: /profile")]


@pytest.mark.asyncio
async def test_profile_command_respects_custom_profile_allowlist():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/profile"))

    assert bot.messages == [("123", "/profile is not enabled for Main.")]


@pytest.mark.asyncio
async def test_help_includes_profile_for_custom_profile():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_PROFILE],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/help"))

    assert "/profile" in bot.messages[0][1]
    assert "/notifications" not in bot.messages[0][1]


@pytest.mark.asyncio
async def test_help_excludes_profile_when_custom_profile_does_not_allow_it():
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/help"))

    assert "/notifications" in bot.messages[0][1]
    assert "/profile" not in bot.messages[0][1]


@pytest.mark.asyncio
async def test_refresh_command_starts_cycle_and_replies_immediately():
    await create_command_profile()
    scheduler = StubScheduler()
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=scheduler,
    )

    await dispatcher.dispatch_update(telegram_update("/refresh"))
    await asyncio.wait_for(scheduler.finished.wait(), timeout=1)

    assert scheduler.run_count == 1
    assert bot.messages == [
        ("123", "Refresh started. I will not send a completion follow-up.")
    ]


@pytest.mark.asyncio
async def test_refresh_command_reports_existing_cycle_progress():
    await create_command_profile()
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(in_progress=True),
    )

    await dispatcher.dispatch_update(telegram_update("/refresh"))

    assert "Refresh already in progress" in bot.messages[0][1]
    assert "2/5 completed" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_refresh_command_retry_does_not_start_duplicate_cycle():
    await create_command_profile()
    scheduler = StubScheduler()
    bot = StubBotClient(fail_first_send=True)
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=scheduler,
    )
    update = telegram_update("/refresh", update_id=61)

    with pytest.raises(RuntimeError, match="telegram send failed"):
        await dispatcher.dispatch_update(update)
    await asyncio.wait_for(scheduler.finished.wait(), timeout=1)
    await dispatcher.dispatch_update(update)

    assert scheduler.run_count == 1
    assert bot.messages == [
        ("123", "Refresh command was already handled.")
    ]


@pytest.mark.asyncio
async def test_refresh_in_progress_retry_does_not_start_later_cycle():
    await create_command_profile()
    scheduler = StubScheduler(in_progress=True)
    bot = StubBotClient(fail_first_send=True)
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=scheduler,
    )
    update = telegram_update("/refresh", update_id=62)

    with pytest.raises(RuntimeError, match="telegram send failed"):
        await dispatcher.dispatch_update(update)
    scheduler._in_progress = False
    await dispatcher.dispatch_update(update)

    assert scheduler.run_count == 0
    assert bot.messages == [("123", "Refresh command was already handled.")]


@pytest.mark.asyncio
async def test_command_started_refresh_task_is_awaitable_on_shutdown():
    await create_command_profile()
    scheduler = SlowScheduler()
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=scheduler,
    )

    await dispatcher.dispatch_update(telegram_update("/refresh", update_id=63))
    await asyncio.wait_for(scheduler.finished.wait(), timeout=1)
    wait_task = asyncio.create_task(wait_for_telegram_command_tasks())
    await asyncio.sleep(0)

    assert not wait_task.done()
    scheduler.release.set()
    await asyncio.wait_for(wait_task, timeout=1)
    assert scheduler.run_count == 1


@pytest.mark.asyncio
async def test_notifications_command_sends_matching_top_messages(monkeypatch: pytest.MonkeyPatch):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    opportunity = OpportunityOut(
        id=1,
        sport="basketball",
        match_id="m1",
        home_team="Partizan",
        away_team="Zvezda",
        league_name="ABA",
        start_time="2026-04-10T20:00:00+00:00",
        opportunity_type="middle",
        market_type="game_total",
        line=154.5,
        profit_margin=-0.02,
        middle_profit_margin=0.45,
        middle_ev=0.08,
        market_keys=["total"],
        legs=[
            OpportunityLeg(
                bookmaker_id="mozzart",
                market_type="game_total",
                outcome_code="over",
                odds=1.91,
                line=154.5,
            ),
            OpportunityLeg(
                bookmaker_id="meridian",
                market_type="game_total",
                outcome_code="under",
                odds=1.91,
                line=156.5,
            ),
        ],
    )

    async def fake_get_opportunities(**_: object) -> list[OpportunityOut]:
        return [opportunity]

    monkeypatch.setattr(odds_store, "get_opportunities", fake_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/notifications"))

    assert len(bot.messages) == 1
    assert "middle" in bot.messages[0][1]
    assert "Partizan vs Zvezda" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_notifications_command_returns_empty_state(monkeypatch: pytest.MonkeyPatch):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )

    async def fake_get_opportunities(**_: object) -> list[OpportunityOut]:
        return []

    monkeypatch.setattr(odds_store, "get_opportunities", fake_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/notifications"))

    assert bot.messages == [("123", "No current opportunities match this Telegram profile.")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("argument", "expected_count"), [("1", 1), ("20", 20)])
async def test_notifications_command_respects_count_argument(
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
    expected_count: int,
):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )

    async def fake_get_opportunities(**_: object) -> list[OpportunityOut]:
        return [make_opportunity(opportunity_id) for opportunity_id in range(25)]

    monkeypatch.setattr(odds_store, "get_opportunities", fake_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update(f"/notifications {argument}"))

    assert len(bot.messages) == expected_count


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["0", "21", "abc", "1.5", "1 2", "²"])
async def test_notifications_command_rejects_invalid_count_argument(
    monkeypatch: pytest.MonkeyPatch,
    argument: str,
):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )

    async def fail_get_opportunities(**_: object) -> list[OpportunityOut]:
        raise AssertionError("invalid /notifications limit should not load opportunities")

    monkeypatch.setattr(odds_store, "get_opportunities", fail_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update(f"/notifications {argument}"))

    assert bot.messages == [("123", "Usage: /notifications [1-20]")]


@pytest.mark.asyncio
async def test_notifications_retry_skips_already_delivered_messages(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )

    async def fake_load_matching_opportunities(
        profile: TelegramNotificationProfileOut,
    ) -> list[OpportunityOut]:
        return [make_opportunity(1), make_opportunity(2)]

    monkeypatch.setattr(
        "app.services.telegram_commands._load_matching_notification_opportunities",
        fake_load_matching_opportunities,
    )
    build_attempts = 0
    limits: list[int] = []

    def fake_build_messages(
        opportunities: list[OpportunityOut],
        *,
        limit: int,
    ) -> list[TelegramOpportunityMessage]:
        nonlocal build_attempts
        build_attempts += 1
        limits.append(limit)
        if build_attempts == 1:
            return [
                TelegramOpportunityMessage(key="a", text="A"),
                TelegramOpportunityMessage(key="b", text="B"),
            ]
        return [
            TelegramOpportunityMessage(key="c", text="C"),
            TelegramOpportunityMessage(key="a", text="A changed"),
            TelegramOpportunityMessage(key="b", text="B"),
        ]

    monkeypatch.setattr(
        "app.services.telegram_commands.build_telegram_opportunity_message_items",
        fake_build_messages,
    )
    bot = StubBotClient(fail_send_numbers={2})
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )
    update = telegram_update("/notifications 3", update_id=71)

    with pytest.raises(RuntimeError, match="telegram send failed"):
        await dispatcher.dispatch_update(update)
    await dispatcher.dispatch_update(update)

    assert limits == [3, 3]
    assert bot.messages == [("123", "A"), ("123", "C"), ("123", "B")]


@pytest.mark.asyncio
async def test_notifications_pages_until_profile_match(monkeypatch: pytest.MonkeyPatch):
    profile = await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    await odds_store.update_telegram_notification_profile(
        profile.id,
        TelegramNotificationProfileUpdate(
            label=profile.label,
            chat_id=profile.chat_id,
            bookmaker_ids=["mozzart", "meridian"],
            command_permission_preset="custom",
            allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
        ),
    )
    calls: list[tuple[int, int]] = []
    non_matching_page = [
        make_opportunity(
            opportunity_id,
            first_bookmaker="other",
            second_bookmaker="other2",
        )
        for opportunity_id in range(500)
    ]
    matching_page = [make_opportunity(501)]

    async def fake_get_opportunities(
        *,
        limit: int = 100,
        offset: int = 0,
        **_: object,
    ) -> list[OpportunityOut]:
        calls.append((limit, offset))
        if offset == 0:
            return non_matching_page
        return matching_page

    monkeypatch.setattr(odds_store, "get_opportunities", fake_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/notifications", update_id=72))

    assert calls == [(500, 0), (500, 500)]
    assert len(bot.messages) == 1
    assert "Home 501 vs Away 501" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_notifications_scans_pages_to_preserve_telegram_ranking(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    calls: list[tuple[int, int]] = []

    async def fake_get_opportunities(
        *,
        limit: int = 100,
        offset: int = 0,
        **_: object,
    ) -> list[OpportunityOut]:
        calls.append((limit, offset))
        if offset == 0:
            return [
                make_opportunity(
                    opportunity_id,
                    profit_margin=0.99,
                )
                for opportunity_id in range(500)
            ]
        if offset == 500:
            return [
                make_middle_opportunity(
                    501,
                    middle_ev=0.25,
                    middle_ev_rank=10.0,
                )
            ]
        return []

    monkeypatch.setattr(odds_store, "get_opportunities", fake_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )

    await dispatcher.dispatch_update(telegram_update("/notifications", update_id=73))

    assert calls == [(500, 0), (500, 500)]
    assert len(bot.messages) == 10
    assert "PREMIUM MIDDLE" in bot.messages[0][1]


@pytest.mark.asyncio
async def test_dispatcher_retries_same_update_after_handler_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    await create_command_profile(
        preset="custom",
        allowed_commands=[TELEGRAM_COMMAND_NOTIFICATIONS],
    )
    attempts = 0

    async def flaky_get_opportunities(**_: object) -> list[OpportunityOut]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient store failure")
        return []

    monkeypatch.setattr(odds_store, "get_opportunities", flaky_get_opportunities)
    bot = StubBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot,  # type: ignore[arg-type]
        scheduler=StubScheduler(),
    )
    update = telegram_update("/notifications", update_id=31)

    with pytest.raises(RuntimeError, match="transient store failure"):
        await dispatcher.dispatch_update(update)
    await dispatcher.dispatch_update(update)

    assert attempts == 2
    assert bot.messages == [("123", "No current opportunities match this Telegram profile.")]


@pytest.mark.asyncio
async def test_poller_does_not_persist_failed_dispatch_update():
    await odds_store.set_telegram_command_last_update_id(20)
    bot = PollerBotClient(
        [
            telegram_update("/refresh", update_id=21),
            telegram_update("/help", update_id=22),
        ]
    )
    dispatcher = FailingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=60,
    )

    poller.start()
    await asyncio.wait_for(dispatcher.failed.wait(), timeout=1)
    await poller.stop()

    assert dispatcher.seen_update_ids == [21]
    assert await odds_store.get_telegram_command_last_update_id() == 20


@pytest.mark.asyncio
async def test_poller_seeds_initial_offset_without_dispatching_backlog():
    bot = PollerBotClient([telegram_update("/refresh", update_id=41)])
    dispatcher = RecordingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=60,
    )

    poller.start()
    await asyncio.wait_for(bot.waiting_for_second_poll.wait(), timeout=1)
    await poller.stop()

    assert bot.offsets == [-1, 42]
    assert dispatcher.seen_update_ids == []
    assert await odds_store.get_telegram_command_last_update_id() == 41


@pytest.mark.asyncio
async def test_poller_keeps_running_when_update_state_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    await odds_store.set_telegram_command_last_update_id(50)
    persistence_failed = asyncio.Event()

    async def fail_set_update_id(update_id: int) -> None:
        persistence_failed.set()
        raise RuntimeError("sqlite locked")

    monkeypatch.setattr(
        odds_store,
        "set_telegram_command_last_update_id",
        fail_set_update_id,
    )
    bot = PollerBotClient([telegram_update("/refresh", update_id=51)])
    dispatcher = RecordingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=60,
    )

    poller.start()
    await asyncio.wait_for(persistence_failed.wait(), timeout=1)
    assert poller.is_running
    await poller.stop()

    assert dispatcher.seen_update_ids == [51]


@pytest.mark.asyncio
async def test_poller_retries_delete_webhook_after_startup_conflict():
    bot = PollerBotClient(
        [],
        fail_first_delete_webhook=True,
        fail_poll_after_delete_failure=True,
    )
    dispatcher = RecordingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
    )

    poller.start()
    await asyncio.wait_for(bot.waiting_for_second_poll.wait(), timeout=2)
    await poller.stop()

    assert bot.delete_webhook_calls >= 2
    assert dispatcher.seen_update_ids == []


@pytest.mark.asyncio
async def test_poller_waits_for_get_me_before_polling():
    bot = PollerBotClient([], fail_first_get_me=True)
    dispatcher = RecordingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
    )

    poller.start()
    await asyncio.wait_for(bot.get_me_failed.wait(), timeout=1)
    assert bot.get_updates_calls == 0
    await asyncio.wait_for(bot.get_me_succeeded.wait(), timeout=2)
    await asyncio.wait_for(bot.waiting_for_second_poll.wait(), timeout=2)
    await poller.stop()

    assert bot.get_me_calls >= 2
    assert dispatcher.seen_update_ids == []


@pytest.mark.asyncio
async def test_initial_offset_retry_waits_for_get_me_before_polling():
    bot = PollerBotClient(
        [],
        fail_first_initial_get_updates=True,
        fail_get_me_call_numbers={2},
    )
    dispatcher = RecordingDispatcher()
    poller = TelegramCommandPoller(
        bot_client=bot,  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        poll_timeout_seconds=1,
        poll_interval_seconds=0,
    )

    poller.start()
    await asyncio.wait_for(bot.initial_get_updates_failed.wait(), timeout=1)
    await asyncio.wait_for(bot.get_me_failed.wait(), timeout=1)
    assert bot.get_updates_calls == 1
    await asyncio.wait_for(bot.get_me_retry_succeeded.wait(), timeout=2)
    assert bot.get_updates_calls == 1
    await asyncio.wait_for(bot.waiting_for_second_poll.wait(), timeout=3)
    await poller.stop()

    assert bot.get_me_calls >= 3
    assert dispatcher.seen_update_ids == []
