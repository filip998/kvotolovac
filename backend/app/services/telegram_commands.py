from __future__ import annotations

import asyncio
import html
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from ..config import settings
from ..models.schemas import (
    BookmakerOut,
    BookmakerCoverageOut,
    MatchUnificationCycleStatusOut,
    ScanProgressOut,
    SystemStatus,
    TelegramNotificationProfileOut,
)
from ..store import odds_store
from .notifications import (
    TelegramBotAPIError,
    TelegramBotClient,
    build_telegram_opportunity_message_items,
    telegram_profile_matches_opportunity,
)

logger = logging.getLogger(__name__)

TELEGRAM_COMMAND_REFRESH = "refresh"
TELEGRAM_COMMAND_NOTIFICATIONS = "notifications"
TELEGRAM_COMMAND_HELP = "help"
TELEGRAM_COMMAND_STATUS = "status"
TELEGRAM_COMMAND_BOOKMAKERS = "bookmakers"
TELEGRAM_COMMAND_PROFILE = "profile"
TELEGRAM_CONFIGURABLE_COMMANDS = (
    TELEGRAM_COMMAND_STATUS,
    TELEGRAM_COMMAND_PROFILE,
    TELEGRAM_COMMAND_BOOKMAKERS,
    TELEGRAM_COMMAND_REFRESH,
    TELEGRAM_COMMAND_NOTIFICATIONS,
)
_BACKGROUND_REFRESH_TASKS: set[asyncio.Task[Any]] = set()
_NOTIFICATION_OPPORTUNITY_PAGE_SIZE = 500
_NOTIFICATIONS_MIN_LIMIT = 1
_NOTIFICATIONS_MAX_LIMIT = 20
_NOTIFICATIONS_USAGE = "Usage: /notifications [1-20]"
_BOOKMAKERS_USAGE = "Usage: /bookmakers"
_PROFILE_USAGE = "Usage: /profile"
_PROFILE_DELIVERY_ERROR_MAX_LENGTH = 160


async def wait_for_telegram_command_tasks() -> None:
    if not _BACKGROUND_REFRESH_TASKS:
        return
    await asyncio.gather(*list(_BACKGROUND_REFRESH_TASKS), return_exceptions=True)


class TelegramCommandScheduler(Protocol):
    @property
    def is_running(self) -> bool:
        ...

    @property
    def is_cycle_in_progress(self) -> bool:
        ...

    def progress_snapshot(self) -> ScanProgressOut:
        ...

    def match_unification_status_snapshot(self) -> MatchUnificationCycleStatusOut:
        ...

    async def run_cycle(self) -> dict:
        ...


@dataclass(frozen=True)
class TelegramCommandRequest:
    update_id: int | None
    chat_id: str
    message_id: int | None
    user_id: int | None
    username: str | None
    text: str
    command: str
    args: str


@dataclass(frozen=True)
class TelegramCommandContext:
    request: TelegramCommandRequest
    profile: TelegramNotificationProfileOut
    bot_client: TelegramBotClient
    scheduler: TelegramCommandScheduler
    registry: "TelegramCommandRegistry"
    notifications_limit: int = 10

    @property
    def chat_id(self) -> str:
        return self.request.chat_id

    async def reply(self, text: str) -> None:
        await self.bot_client.send_message(chat_id=self.chat_id, text=text)

    def visible_commands(self) -> list[str]:
        return [
            command
            for command in TELEGRAM_CONFIGURABLE_COMMANDS
            if profile_allows_telegram_command(
                self.profile,
                command,
                registered_commands=self.registry.command_names,
            )
        ]


class TelegramCommandHandler(Protocol):
    name: str
    help_text: str

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        ...


class TelegramCommandRegistry:
    def __init__(self, handlers: list[TelegramCommandHandler] | None = None) -> None:
        self._handlers: dict[str, TelegramCommandHandler] = {}
        for handler in handlers or []:
            self.register(handler)

    @property
    def command_names(self) -> set[str]:
        return set(self._handlers)

    def register(self, handler: TelegramCommandHandler) -> None:
        self._handlers[handler.name.lower()] = handler

    def get(self, command: str) -> TelegramCommandHandler | None:
        return self._handlers.get(command.lower())

    @property
    def handlers(self) -> list[TelegramCommandHandler]:
        return list(self._handlers.values())


class HelpCommand:
    name = TELEGRAM_COMMAND_HELP
    help_text = "Show available bot commands."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        visible = context.visible_commands()
        if not visible:
            await context.reply("No backend commands are enabled for this Telegram profile.")
            return
        help_lines = ["<b>KvotoLovac commands</b>"]
        for command in visible:
            handler = context.registry.get(command)
            if handler is None:
                continue
            help_lines.append(f"/{command} — {html.escape(handler.help_text)}")
        await context.reply("\n".join(help_lines))


class RefreshCommand:
    name = TELEGRAM_COMMAND_REFRESH
    help_text = "Start a new scrape and analysis cycle."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        if context.request.update_id is not None:
            should_start = await odds_store.begin_telegram_command_execution(
                update_id=context.request.update_id,
                chat_id=context.chat_id,
                command=self.name,
            )
            if not should_start:
                await context.reply("Refresh command was already handled.")
                return

        if context.scheduler.is_cycle_in_progress:
            await context.reply(_format_refresh_in_progress(context.scheduler.progress_snapshot()))
            return

        task = asyncio.create_task(context.scheduler.run_cycle())
        _BACKGROUND_REFRESH_TASKS.add(task)
        task.add_done_callback(_finish_background_refresh_task)
        await context.reply("Refresh started. I will not send a completion follow-up.")


class StatusCommand:
    name = TELEGRAM_COMMAND_STATUS
    help_text = "Show backend and scrape status."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        status = await odds_store.get_system_status(
            scheduler_running=context.scheduler.is_running,
            scan_progress=context.scheduler.progress_snapshot(),
            match_unification=context.scheduler.match_unification_status_snapshot(),
        )
        await context.reply(_format_system_status(status))


class BookmakersCommand:
    name = TELEGRAM_COMMAND_BOOKMAKERS
    help_text = "Show bookmaker coverage and last-seen status."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        if args.strip():
            await context.reply(_BOOKMAKERS_USAGE)
            return
        coverage = await odds_store.get_bookmaker_coverage()
        await context.reply(_format_bookmaker_coverage(coverage))


class ProfileCommand:
    name = TELEGRAM_COMMAND_PROFILE
    help_text = "Show this chat's Telegram notification profile."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        if args.strip():
            await context.reply(_PROFILE_USAGE)
            return
        bookmaker_names: dict[str, str] = {}
        if context.profile.bookmaker_ids:
            bookmakers = await odds_store.get_bookmakers(active_only=False)
            bookmaker_names = _bookmaker_name_lookup(bookmakers)
        await context.reply(_format_telegram_profile(context.profile, bookmaker_names))


class NotificationsCommand:
    name = TELEGRAM_COMMAND_NOTIFICATIONS
    help_text = "Return the current top opportunity groups."

    async def execute(self, context: TelegramCommandContext, args: str) -> None:
        effective_limit, usage_error = _parse_notifications_limit(
            args,
            default=context.notifications_limit,
        )
        if usage_error is not None:
            await context.reply(usage_error)
            return
        matching_opportunities = await _load_matching_notification_opportunities(
            context.profile
        )
        messages = build_telegram_opportunity_message_items(
            matching_opportunities,
            limit=effective_limit,
        )
        if not messages:
            await context.reply("No current opportunities match this Telegram profile.")
            return
        delivered_keys: set[str] = set()
        if context.request.update_id is not None:
            delivered_keys = (
                await odds_store.list_telegram_command_delivered_message_keys(
                    update_id=context.request.update_id,
                    command=self.name,
                )
            )
        for message_index, message in enumerate(messages):
            if message.key in delivered_keys:
                continue
            result = await context.bot_client.send_message(
                chat_id=context.chat_id,
                text=message.text,
            )
            if context.request.update_id is not None:
                await odds_store.mark_telegram_command_message_delivered(
                    update_id=context.request.update_id,
                    command=self.name,
                    message_key=message.key,
                    message_index=message_index,
                    telegram_message_id=result.message_id,
                )


class TelegramCommandDispatcher:
    def __init__(
        self,
        *,
        bot_client: TelegramBotClient,
        scheduler: TelegramCommandScheduler,
        registry: TelegramCommandRegistry | None = None,
        bot_username: str | None = None,
        max_seen_updates: int = 512,
        notifications_limit: int = 10,
    ) -> None:
        self._bot_client = bot_client
        self._scheduler = scheduler
        self._registry = registry or default_telegram_command_registry()
        self._bot_username = _normalize_bot_username(bot_username)
        self._seen_updates: deque[int] = deque(maxlen=max_seen_updates)
        self._seen_update_ids: set[int] = set()
        self._notifications_limit = notifications_limit

    @property
    def registry(self) -> TelegramCommandRegistry:
        return self._registry

    def set_bot_username(self, username: str | None) -> None:
        self._bot_username = _normalize_bot_username(username)

    async def dispatch_update(self, update: dict[str, Any]) -> None:
        request = parse_telegram_command_update(update, bot_username=self._bot_username)
        if request is None:
            return
        if request.update_id is not None and self._is_seen_update(request.update_id):
            return

        await self._dispatch_request(request)
        if request.update_id is not None:
            self._mark_seen_update(request.update_id)

    async def _dispatch_request(self, request: TelegramCommandRequest) -> None:
        profiles = await odds_store.list_telegram_command_profiles_for_chat(
            request.chat_id
        )
        if not profiles:
            logger.info(
                "Ignoring Telegram command from unauthorized chat_id=%s",
                request.chat_id,
            )
            return
        if len(profiles) != 1:
            logger.error(
                "Telegram command authorization failed closed for chat_id=%s: %d profiles",
                request.chat_id,
                len(profiles),
            )
            await self._bot_client.send_message(
                chat_id=request.chat_id,
                text=(
                    "Telegram command access is misconfigured for this chat. "
                    "Exactly one enabled command profile is required."
                ),
            )
            return

        profile = profiles[0]
        handler = self._registry.get(request.command)
        if handler is None:
            await self._bot_client.send_message(
                chat_id=request.chat_id,
                text=f"Unknown command: /{html.escape(request.command)}. Try /help.",
            )
            return
        if not profile_allows_telegram_command(
            profile,
            request.command,
            registered_commands=self._registry.command_names,
        ):
            await self._bot_client.send_message(
                chat_id=request.chat_id,
                text=(
                    f"/{html.escape(request.command)} is not enabled for "
                    f"{html.escape(profile.label)}."
                ),
            )
            return

        context = TelegramCommandContext(
            request=request,
            profile=profile,
            bot_client=self._bot_client,
            scheduler=self._scheduler,
            registry=self._registry,
            notifications_limit=self._notifications_limit,
        )
        await handler.execute(context, request.args)

    def _is_seen_update(self, update_id: int) -> bool:
        return update_id in self._seen_update_ids

    def _mark_seen_update(self, update_id: int) -> None:
        if len(self._seen_updates) == self._seen_updates.maxlen:
            expired = self._seen_updates.popleft()
            self._seen_update_ids.discard(expired)
        self._seen_updates.append(update_id)
        self._seen_update_ids.add(update_id)


class TelegramCommandPoller:
    def __init__(
        self,
        *,
        bot_client: TelegramBotClient,
        dispatcher: TelegramCommandDispatcher,
        poll_timeout_seconds: int,
        poll_interval_seconds: float,
    ) -> None:
        self._bot_client = bot_client
        self._dispatcher = dispatcher
        self._poll_timeout_seconds = poll_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            return
        self._task = asyncio.create_task(self._run(), name="telegram-command-poller")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        await self._prepare_bot_session_until_ready()
        offset = await self._initial_poll_offset()
        while True:
            try:
                updates = await self._bot_client.get_updates(
                    offset=offset,
                    timeout_seconds=self._poll_timeout_seconds,
                )
            except TelegramBotAPIError:
                logger.exception("Telegram command polling request failed")
                await self._prepare_bot_session_until_ready()
                await asyncio.sleep(max(self._poll_interval_seconds, 1.0))
                continue
            except Exception:
                logger.exception("Telegram command polling loop failed")
                await asyncio.sleep(max(self._poll_interval_seconds, 1.0))
                continue

            retry_failed_update = False
            for update in updates:
                update_id = _extract_update_id(update)
                try:
                    await self._dispatcher.dispatch_update(update)
                except Exception:
                    logger.exception("Telegram command update processing failed")
                    retry_failed_update = update_id is not None
                    if retry_failed_update:
                        break
                    continue
                if update_id is not None:
                    try:
                        await odds_store.set_telegram_command_last_update_id(update_id)
                    except Exception:
                        logger.exception(
                            "Telegram command update state persistence failed"
                        )
                        retry_failed_update = True
                        break
                    offset = update_id + 1

            if retry_failed_update:
                await asyncio.sleep(max(self._poll_interval_seconds, 1.0))
                continue

            if not updates and self._poll_interval_seconds > 0:
                await asyncio.sleep(self._poll_interval_seconds)

    async def _initial_poll_offset(self) -> int | None:
        while True:
            try:
                last_update_id = await odds_store.get_telegram_command_last_update_id()
                if last_update_id is not None:
                    return last_update_id + 1
                latest_updates = await self._bot_client.get_updates(
                    offset=-1,
                    timeout_seconds=0,
                    limit=1,
                )
                latest_update_ids = [
                    update_id
                    for update in latest_updates
                    if (update_id := _extract_update_id(update)) is not None
                ]
                if not latest_update_ids:
                    return None
                latest_update_id = max(latest_update_ids)
                await odds_store.set_telegram_command_last_update_id(latest_update_id)
                return latest_update_id + 1
            except TelegramBotAPIError:
                logger.exception("Telegram initial command offset request failed")
                await self._prepare_bot_session_until_ready()
            except Exception:
                logger.exception("Telegram initial command offset setup failed")
            await asyncio.sleep(max(self._poll_interval_seconds, 1.0))

    async def _prepare_bot_session_until_ready(self) -> None:
        while not await self._prepare_bot_session():
            await asyncio.sleep(max(self._poll_interval_seconds, 1.0))

    async def _prepare_bot_session(self) -> bool:
        try:
            await self._bot_client.delete_webhook(drop_pending_updates=False)
        except TelegramBotAPIError:
            logger.exception("Telegram deleteWebhook failed before command polling")
        try:
            me = await self._bot_client.get_me()
        except TelegramBotAPIError:
            logger.exception("Telegram getMe failed before command polling")
            self._dispatcher.set_bot_username(None)
            return False
        username = me.get("username")
        if not isinstance(username, str) or not username.strip():
            logger.error("Telegram getMe response did not include a bot username")
            self._dispatcher.set_bot_username(None)
            return False
        self._dispatcher.set_bot_username(username)
        return True


def default_telegram_command_registry() -> TelegramCommandRegistry:
    return TelegramCommandRegistry(
        [
            HelpCommand(),
            StatusCommand(),
            ProfileCommand(),
            BookmakersCommand(),
            RefreshCommand(),
            NotificationsCommand(),
        ]
    )


async def _load_matching_notification_opportunities(
    profile: TelegramNotificationProfileOut,
) -> list[Any]:
    matching_opportunities: list[Any] = []
    offset = 0
    while True:
        opportunities = await odds_store.get_opportunities(
            limit=_NOTIFICATION_OPPORTUNITY_PAGE_SIZE,
            offset=offset,
        )
        matching_opportunities.extend(
            opportunity
            for opportunity in opportunities
            if telegram_profile_matches_opportunity(profile, opportunity)
        )
        if len(opportunities) < _NOTIFICATION_OPPORTUNITY_PAGE_SIZE:
            return matching_opportunities
        offset += _NOTIFICATION_OPPORTUNITY_PAGE_SIZE


def create_telegram_command_poller(scheduler: TelegramCommandScheduler) -> TelegramCommandPoller:
    bot_client = TelegramBotClient()
    dispatcher = TelegramCommandDispatcher(
        bot_client=bot_client,
        scheduler=scheduler,
    )
    return TelegramCommandPoller(
        bot_client=bot_client,
        dispatcher=dispatcher,
        poll_timeout_seconds=settings.telegram_poll_timeout_seconds,
        poll_interval_seconds=settings.telegram_poll_interval_seconds,
    )


def parse_telegram_command_update(
    update: dict[str, Any],
    *,
    bot_username: str | None = None,
) -> TelegramCommandRequest | None:
    update_id = _extract_update_id(update)
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.startswith("/"):
        return None

    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    chat_id = str(chat["id"])

    first_token, _, args = text.partition(" ")
    command_token = first_token[1:]
    if not command_token:
        return None
    command_name, mention = _split_command_mention(command_token)
    expected_bot_username = _normalize_bot_username(bot_username)
    if mention:
        if expected_bot_username is None or mention != expected_bot_username:
            return None

    from_user = message.get("from")
    user_id: int | None = None
    username: str | None = None
    if isinstance(from_user, dict):
        raw_user_id = from_user.get("id")
        if raw_user_id is not None:
            try:
                user_id = int(raw_user_id)
            except (TypeError, ValueError):
                user_id = None
        raw_username = from_user.get("username")
        if isinstance(raw_username, str):
            username = raw_username

    raw_message_id = message.get("message_id")
    message_id: int | None = None
    if raw_message_id is not None:
        try:
            message_id = int(raw_message_id)
        except (TypeError, ValueError):
            message_id = None

    return TelegramCommandRequest(
        update_id=update_id,
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        username=username,
        text=text,
        command=command_name,
        args=args.strip(),
    )


def profile_allows_telegram_command(
    profile: TelegramNotificationProfileOut,
    command: str,
    *,
    registered_commands: set[str],
) -> bool:
    command = command.strip().lower()
    if not profile.enabled or profile.command_permission_preset == "none":
        return False
    if command == TELEGRAM_COMMAND_HELP:
        return True
    if command not in registered_commands:
        return False
    if profile.command_permission_preset == "admin":
        return True
    if profile.command_permission_preset == "custom":
        return command in {item.strip().lower() for item in profile.allowed_commands}
    return False


def _parse_notifications_limit(args: str, *, default: int) -> tuple[int, str | None]:
    normalized = args.strip()
    if not normalized:
        return default, None
    parts = normalized.split()
    if len(parts) != 1:
        return default, _NOTIFICATIONS_USAGE
    token = parts[0]
    if not token.isascii() or not token.isdigit():
        return default, _NOTIFICATIONS_USAGE
    limit = int(token)
    if not _NOTIFICATIONS_MIN_LIMIT <= limit <= _NOTIFICATIONS_MAX_LIMIT:
        return default, _NOTIFICATIONS_USAGE
    return limit, None


def _split_command_mention(command_token: str) -> tuple[str, str | None]:
    command, separator, mention = command_token.partition("@")
    normalized_command = command.strip().lower()
    if not separator:
        return normalized_command, None
    return normalized_command, _normalize_bot_username(mention)


def _normalize_bot_username(username: str | None) -> str | None:
    if username is None:
        return None
    normalized = username.strip().lstrip("@").lower()
    return normalized or None


def _extract_update_id(update: dict[str, Any]) -> int | None:
    raw_update_id = update.get("update_id")
    if raw_update_id is None:
        return None
    try:
        return int(raw_update_id)
    except (TypeError, ValueError):
        return None


def _format_refresh_in_progress(snapshot: ScanProgressOut) -> str:
    if snapshot.total_tasks > 0:
        progress = (
            f"{snapshot.completed_tasks}/{snapshot.total_tasks} completed, "
            f"{snapshot.active_tasks} active, {snapshot.failed_tasks} failed"
        )
    else:
        progress = "cycle is starting"
    return f"Refresh already in progress ({html.escape(snapshot.phase)}): {progress}."


def _format_system_status(status: SystemStatus) -> str:
    scheduler_state = "running" if status.scheduler_running else "stopped"
    last_scrape = status.last_scrape_at or "never"
    lines = [
        "<b>KvotoLovac status</b>",
        f"Backend: {html.escape(str(status.status).upper())}",
        f"Scheduler: {scheduler_state}",
        f"Last scrape: {html.escape(last_scrape)}",
        (
            "Totals: "
            f"{status.total_matches} matches · "
            f"{status.total_odds} odds · "
            f"{status.total_opportunities} opportunities · "
            f"{status.active_bookmakers} bookmakers"
        ),
    ]
    scan = status.scan
    if scan.in_progress:
        if scan.total_tasks > 0:
            progress = (
                f"{scan.completed_tasks}/{scan.total_tasks} completed, "
                f"{scan.active_tasks} active, {scan.failed_tasks} failed"
            )
        else:
            progress = "cycle is starting"
        lines.append(f"Cycle: {html.escape(scan.phase)} — {progress}")
    else:
        lines.append("Cycle: idle")
    match_unification = status.match_unification
    lines.append(
        "Match Unification: "
        f"{html.escape(match_unification.state)} "
        f"({html.escape(match_unification.mode)})"
    )
    if match_unification.fallback_reason:
        lines.append(
            "Match Unification fallback: "
            f"{html.escape(match_unification.fallback_reason)}"
        )
    for warning in match_unification.warnings:
        lines.append(f"Match Unification warning: {html.escape(warning)}")
    return "\n".join(lines)


def _format_bookmaker_coverage(bookmakers: list[BookmakerCoverageOut]) -> str:
    if not bookmakers:
        return "No active bookmakers configured."

    current_snapshot = bookmakers[0].current_snapshot_at or "never"
    lines = [
        "<b>Bookmaker coverage</b>",
        f"Current snapshot: {html.escape(current_snapshot)}",
    ]
    for bookmaker in sorted(
        bookmakers,
        key=lambda item: (
            item.current_match_count <= 0,
            item.name.lower(),
            item.id,
        ),
    ):
        if bookmaker.current_match_count > 0:
            icon = "✅"
            current_label = _pluralize_matches(bookmaker.current_match_count)
        elif bookmaker.last_seen_at:
            icon = "⚠️"
            current_label = "no current rows"
        else:
            icon = "⚪"
            current_label = "no current rows"
        last_seen = bookmaker.last_seen_at or "never"
        lines.append(
            f"{icon} {html.escape(bookmaker.name)} — {current_label} · "
            f"last seen {html.escape(last_seen)}"
        )
    return "\n".join(lines)


def _format_telegram_profile(
    profile: TelegramNotificationProfileOut,
    bookmaker_names: dict[str, str],
) -> str:
    lines = [
        "<b>Telegram profile</b>",
        f"Label: {html.escape(profile.label)}",
        "Opportunity filters:",
        f"ROI ≥ {_format_profile_number(profile.min_roi_percent)}%",
        f"Middle EV ≥ {_format_profile_number(profile.min_middle_ev_percent)}%",
        f"Middle gap ≥ {_format_profile_number(profile.min_gap)}",
        f"Bookmakers: {_format_profile_bookmakers(profile, bookmaker_names)}",
        f"Commands: {_format_profile_commands(profile)}",
    ]
    rate_limited_until = _profile_rate_limited_until(profile)
    if rate_limited_until is not None:
        lines.append(
            f"Rate limited until: {html.escape(_format_profile_datetime(rate_limited_until))}"
        )
    delivery_error = _format_profile_delivery_error(profile.last_delivery_error)
    if delivery_error is not None:
        lines.append(f"Last delivery error: {html.escape(delivery_error)}")
    return "\n".join(lines)


def _bookmaker_name_lookup(bookmakers: list[BookmakerOut]) -> dict[str, str]:
    return {bookmaker.id: bookmaker.name for bookmaker in bookmakers}


def _format_profile_bookmakers(
    profile: TelegramNotificationProfileOut,
    bookmaker_names: dict[str, str],
) -> str:
    if not profile.bookmaker_ids:
        return "all"
    labels = [
        bookmaker_names.get(bookmaker_id, bookmaker_id)
        for bookmaker_id in profile.bookmaker_ids
    ]
    return html.escape(", ".join(labels))


def _format_profile_commands(profile: TelegramNotificationProfileOut) -> str:
    if profile.command_permission_preset == "admin":
        return "admin (all commands)"
    if profile.command_permission_preset == "none":
        return "none"
    if not profile.allowed_commands:
        return "custom — no commands"
    commands = ", ".join(f"/{command}" for command in profile.allowed_commands)
    return f"custom — {html.escape(commands)}"


def _format_profile_delivery_error(error: str | None) -> str | None:
    if error is None:
        return None
    normalized = " ".join(error.split())
    if not normalized:
        return None
    if len(normalized) <= _PROFILE_DELIVERY_ERROR_MAX_LENGTH:
        return normalized
    return f"{normalized[: _PROFILE_DELIVERY_ERROR_MAX_LENGTH - 3]}..."


def _profile_rate_limited_until(profile: TelegramNotificationProfileOut) -> datetime | None:
    if not profile.rate_limited_until:
        return None
    try:
        until = datetime.fromisoformat(profile.rate_limited_until)
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if until <= datetime.now(timezone.utc):
        return None
    return until


def _format_profile_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _format_profile_number(value: float) -> str:
    return f"{value:g}"


def _pluralize_matches(count: int) -> str:
    suffix = "match" if count == 1 else "matches"
    return f"{count} current {suffix}"


def _finish_background_refresh_task(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_REFRESH_TASKS.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info("Telegram /refresh background cycle task was cancelled")
    except Exception:
        logger.exception("Telegram /refresh background cycle failed")
