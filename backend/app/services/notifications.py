from __future__ import annotations

import abc
import hashlib
import html
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..models.schemas import TelegramNotificationProfileOut
from ..store import odds_store
from .opportunity_analyzer import Opportunity

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
TELEGRAM_MAX_INDIVIDUAL_MESSAGES_PER_PROFILE = 5
TELEGRAM_MIDDLE_DIGEST_LIMIT = 10
TELEGRAM_GROUP_SUMMARY_LIMIT = 6
TELEGRAM_MESSAGE_SOFT_LIMIT = 3900
_NEGATIVE_INFINITY = -1_000_000_000.0


@dataclass(frozen=True)
class TelegramOpportunityDisplayContext:
    home_team: str | None = None
    away_team: str | None = None
    league_name: str | None = None
    start_time: str | None = None
    fallback_label: str | None = None

    @classmethod
    def from_mapping(
        cls,
        payload: dict[str, str | None] | None,
        *,
        fallback_label: str | None,
    ) -> "TelegramOpportunityDisplayContext":
        payload = payload or {}
        return cls(
            home_team=payload.get("home_team"),
            away_team=payload.get("away_team"),
            league_name=payload.get("league_name"),
            start_time=payload.get("start_time"),
            fallback_label=payload.get("fallback_label") or fallback_label,
        )


@dataclass(frozen=True)
class _TelegramDeliveryItem:
    fingerprint: str
    opportunity: Opportunity
    context: TelegramOpportunityDisplayContext


@dataclass(frozen=True)
class _TelegramOpportunityGroup:
    key: tuple[str, ...]
    opportunities: tuple[Opportunity, ...]
    context: TelegramOpportunityDisplayContext


class NotificationProvider(abc.ABC):
    """Abstract notification provider interface."""

    @abc.abstractmethod
    async def send(self, type: str, title: str, message: str, data: dict | None = None) -> None:
        ...


class InAppNotificationProvider(NotificationProvider):
    """Stores notifications in SQLite for the in-app notification centre."""

    async def send(self, type: str, title: str, message: str, data: dict | None = None) -> None:
        await odds_store.insert_notification(
            type=type, title=title, message=message, data=data
        )
        logger.info("In-app notification: %s — %s", title, message)


class OpportunityNotificationProvider(abc.ABC):
    """Notification provider that receives the full opportunity model."""

    @abc.abstractmethod
    async def send_opportunity(
        self,
        opportunity: Opportunity,
        *,
        publish_id: str | None = None,
    ) -> bool:
        ...

    async def send_opportunities(
        self,
        opportunities: list[Opportunity],
        *,
        publish_id: str | None = None,
    ) -> int:
        count = 0
        for opportunity in opportunities:
            if await self.send_opportunity(opportunity, publish_id=publish_id):
                count += 1
        return count


class TelegramBotConfigError(RuntimeError):
    """Raised when Telegram delivery is requested without a bot token."""


class TelegramBotAPIError(RuntimeError):
    """Raised for Telegram API or transport failures with token-redacted messages."""

    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class TelegramSendMessageResult:
    message_id: int | None = None


class TelegramBotClient:
    """Small Telegram Bot API client, isolated for future command handling."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = (token if token is not None else settings.telegram_bot_token).strip()
        self._api_base_url = (
            api_base_url if api_base_url is not None else settings.telegram_api_base_url
        ).rstrip("/")
        self._http_client = http_client

    @property
    def token_configured(self) -> bool:
        return bool(self._token)

    @property
    def api_base_url(self) -> str:
        return self._api_base_url

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
    ) -> TelegramSendMessageResult:
        if not self._token:
            raise TelegramBotConfigError("Telegram bot token is not configured")

        url = f"{self._api_base_url}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        try:
            if self._http_client is not None:
                response = await self._http_client.post(url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise TelegramBotAPIError(
                f"Telegram request failed: {type(exc).__name__}"
            ) from exc

        response_payload = _telegram_response_payload(response)
        if response.status_code >= 400:
            description = response_payload.get("description") or response.reason_phrase
            retry_after = _telegram_retry_after(response_payload)
            raise TelegramBotAPIError(
                f"Telegram HTTP {response.status_code}: {description}",
                retry_after=retry_after,
            )
        if response_payload.get("ok") is not True:
            description = response_payload.get("description") or "Telegram API returned ok=false"
            raise TelegramBotAPIError(str(description))

        result = response_payload.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return TelegramSendMessageResult(
            message_id=int(message_id) if message_id is not None else None
        )


class TelegramNotificationProvider(OpportunityNotificationProvider):
    """Sends qualifying opportunities to enabled Telegram notification profiles."""

    def __init__(
        self,
        *,
        bot_client: TelegramBotClient | None = None,
    ) -> None:
        self._bot_client = bot_client or TelegramBotClient()

    async def send_opportunity(
        self,
        opportunity: Opportunity,
        *,
        publish_id: str | None = None,
    ) -> bool:
        return bool(await self.send_opportunities([opportunity], publish_id=publish_id))

    async def send_opportunities(
        self,
        opportunities: list[Opportunity],
        *,
        publish_id: str | None = None,
    ) -> int:
        opportunities = [item for item in opportunities if len(item.legs) == 2]
        if not opportunities:
            return 0
        profiles = await odds_store.list_telegram_notification_profiles(enabled_only=True)
        if not profiles:
            return 0

        display_contexts = await _load_telegram_display_contexts(opportunities)
        covered_fingerprints: set[str] = set()
        for profile in profiles:
            if _profile_is_rate_limited(profile):
                continue
            if profile.rate_limited_until:
                await odds_store.clear_telegram_profile_rate_limit(profile.id)

            individual_opportunities = [
                opportunity
                for opportunity in opportunities
                if opportunity.opportunity_type != "middle"
                and telegram_profile_matches_opportunity(profile, opportunity)
            ]
            middle_opportunities = [
                opportunity
                for opportunity in opportunities
                if opportunity.opportunity_type == "middle"
                and telegram_profile_matches_opportunity(profile, opportunity)
            ]
            _sent_count, rate_limited = await self._send_individual_opportunities(
                profile,
                individual_opportunities,
                display_contexts=display_contexts,
                publish_id=publish_id,
                covered_fingerprints=covered_fingerprints,
            )
            if rate_limited:
                continue
            await self._send_middle_digest(
                profile,
                middle_opportunities,
                display_contexts=display_contexts,
                publish_id=publish_id,
                covered_fingerprints=covered_fingerprints,
            )
        return len(covered_fingerprints)

    async def _send_individual_opportunities(
        self,
        profile: TelegramNotificationProfileOut,
        opportunities: list[Opportunity],
        *,
        display_contexts: dict[tuple[str | None, str], TelegramOpportunityDisplayContext],
        publish_id: str | None,
        covered_fingerprints: set[str],
    ) -> tuple[int, bool]:
        sent_count = 0
        groups = _telegram_opportunity_groups(opportunities, display_contexts)
        for group in groups:
            if sent_count >= TELEGRAM_MAX_INDIVIDUAL_MESSAGES_PER_PROFILE:
                break
            attempted = await _begin_telegram_group_delivery_attempts(
                profile=profile,
                group=group,
                display_contexts=display_contexts,
                publish_id=publish_id,
            )
            if not attempted:
                continue
            try:
                result = await self._bot_client.send_message(
                    chat_id=profile.chat_id,
                    text=format_telegram_opportunity_group(attempted),
                )
            except Exception as exc:
                rate_limited = await _record_telegram_delivery_failure(
                    profile_id=profile.id,
                    opportunity_fingerprints=[item.fingerprint for item in attempted],
                    exc=exc,
                )
                if rate_limited:
                    return sent_count, True
                continue
            for item in attempted:
                await odds_store.mark_telegram_delivery_sent(
                    profile_id=profile.id,
                    opportunity_fingerprint=item.fingerprint,
                    telegram_message_id=result.message_id,
                )
                covered_fingerprints.add(item.fingerprint)
            await odds_store.clear_telegram_profile_delivery_error(profile.id)
            sent_count += 1
        return sent_count, False

    async def _send_middle_digest(
        self,
        profile: TelegramNotificationProfileOut,
        opportunities: list[Opportunity],
        *,
        display_contexts: dict[tuple[str | None, str], TelegramOpportunityDisplayContext],
        publish_id: str | None,
        covered_fingerprints: set[str],
    ) -> bool:
        attempted_groups: list[list[_TelegramDeliveryItem]] = []
        groups = _telegram_opportunity_groups(opportunities, display_contexts)
        remaining_group_count = 0
        for index, group in enumerate(groups):
            if len(attempted_groups) >= TELEGRAM_MIDDLE_DIGEST_LIMIT:
                remaining_group_count = len(groups) - index
                break
            attempted = await _begin_telegram_group_delivery_attempts(
                profile=profile,
                group=group,
                display_contexts=display_contexts,
                publish_id=publish_id,
            )
            if attempted:
                attempted_groups.append(attempted)
        if not attempted_groups:
            return False

        try:
            result = await self._bot_client.send_message(
                chat_id=profile.chat_id,
                text=format_telegram_middle_digest(
                    profile,
                    attempted_groups,
                    matched_group_count=len(groups),
                    remaining_group_count=remaining_group_count,
                ),
            )
        except Exception as exc:
            await _record_telegram_delivery_failure(
                profile_id=profile.id,
                opportunity_fingerprints=[
                    item.fingerprint
                    for group_items in attempted_groups
                    for item in group_items
                ],
                exc=exc,
            )
            return False

        for group_items in attempted_groups:
            for item in group_items:
                await odds_store.mark_telegram_delivery_sent(
                    profile_id=profile.id,
                    opportunity_fingerprint=item.fingerprint,
                    telegram_message_id=result.message_id,
                )
                covered_fingerprints.add(item.fingerprint)
        await odds_store.clear_telegram_profile_delivery_error(profile.id)
        return True


class NotificationService:
    """Orchestrates notification delivery through registered providers."""

    def __init__(self, gap_threshold: float = 1.5) -> None:
        self._providers: list[NotificationProvider] = []
        self._opportunity_providers: list[OpportunityNotificationProvider] = []
        self.gap_threshold = gap_threshold

    def register_provider(self, provider: NotificationProvider) -> None:
        self._providers.append(provider)

    def register_opportunity_provider(self, provider: OpportunityNotificationProvider) -> None:
        self._opportunity_providers.append(provider)

    def clear_providers(self) -> None:
        self._providers.clear()
        self._opportunity_providers.clear()

    async def notify_opportunities(
        self,
        opportunities: list[Opportunity],
        *,
        publish_id: str | None = None,
    ) -> int:
        """Send notifications for generic opportunities above threshold. Returns count sent."""
        count = 0
        for opportunity in opportunities:
            sent = False
            gap = _opportunity_gap(opportunity)
            if gap is not None and gap >= self.gap_threshold and self._providers:
                first_leg, second_leg = opportunity.legs[:2]
                subject = opportunity.subject_name or opportunity.market_type
                title = f"Opportunity: {subject} ({gap}pt gap)"
                message = (
                    f"{first_leg.bookmaker_id} {first_leg.outcome_code} {first_leg.line} vs "
                    f"{second_leg.bookmaker_id} {second_leg.outcome_code} {second_leg.line} - "
                    f"gap {gap}, edge ROI {opportunity.profit_margin}, "
                    f"middle payout if hit {opportunity.middle_profit_margin}"
                )
                data = {
                    "match_id": opportunity.match_id,
                    "resolved_event_id": opportunity.resolved_event_id,
                    "sport": opportunity.sport,
                    "market_type": opportunity.market_type,
                    "subject_name": opportunity.subject_name,
                    "gap": gap,
                    "profit_margin": opportunity.profit_margin,
                    "middle_profit_margin": opportunity.middle_profit_margin,
                    "bookmaker_a": first_leg.bookmaker_id,
                    "bookmaker_b": second_leg.bookmaker_id,
                }
                for provider in self._providers:
                    await provider.send("opportunity", title, message, data)
                sent = True

            if sent:
                count += 1
        for provider in self._opportunity_providers:
            count += await provider.send_opportunities(
                opportunities,
                publish_id=publish_id,
            )
        return count


def _opportunity_gap(opportunity: Opportunity) -> float | None:
    if opportunity.opportunity_type != "middle" or len(opportunity.legs) != 2:
        return None
    first_leg, second_leg = opportunity.legs
    if first_leg.line is None or second_leg.line is None:
        return None
    return abs(first_leg.line - second_leg.line)


def telegram_profile_matches_opportunity(
    profile: TelegramNotificationProfileOut,
    opportunity: Opportunity,
) -> bool:
    if len(opportunity.legs) != 2:
        return False
    if profile.bookmaker_ids:
        allowed = set(profile.bookmaker_ids)
        if any(leg.bookmaker_id not in allowed for leg in opportunity.legs):
            return False

    if opportunity.opportunity_type == "middle":
        if opportunity.middle_ev is not None:
            return opportunity.middle_ev * 100 >= profile.min_middle_ev_percent
        gap = _opportunity_gap(opportunity)
        if gap is None or gap < profile.min_gap:
            return False
        if opportunity.middle_profit_margin is None:
            return False
        return opportunity.middle_profit_margin * 100 >= profile.min_roi_percent

    if opportunity.profit_margin is None:
        return False
    return opportunity.profit_margin * 100 >= profile.min_roi_percent


def telegram_opportunity_fingerprint(opportunity: Opportunity) -> str:
    event_identity = (
        ("resolved_event", opportunity.resolved_event_id)
        if opportunity.resolved_event_id
        else ("match", opportunity.match_id)
    )
    leg_identity = sorted(
        (
            leg.bookmaker_id,
            leg.match_id or opportunity.match_id,
            leg.market_type,
            leg.outcome_code,
            _stable_float(leg.line),
            leg.raw_label or "",
        )
        for leg in opportunity.legs
    )
    payload = {
        "sport": opportunity.sport,
        "event_identity": event_identity,
        "opportunity_type": opportunity.opportunity_type,
        "market_type": opportunity.market_type,
        "subject_type": opportunity.subject_type,
        "subject_key": opportunity.subject_key,
        "subject_name": opportunity.subject_name,
        "market_keys": sorted(opportunity.market_keys),
        "legs": leg_identity,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _opportunity_context_key(opportunity: Opportunity) -> tuple[str | None, str]:
    return (opportunity.resolved_event_id, opportunity.match_id)


async def _load_telegram_display_contexts(
    opportunities: list[Opportunity],
) -> dict[tuple[str | None, str], TelegramOpportunityDisplayContext]:
    keys = [_opportunity_context_key(opportunity) for opportunity in opportunities]
    raw_contexts = await odds_store.get_telegram_opportunity_display_contexts(keys)
    return {
        key: TelegramOpportunityDisplayContext.from_mapping(
            raw_contexts.get(key),
            fallback_label=key[0] or key[1],
        )
        for key in dict.fromkeys(keys)
    }


def _display_context_for(
    opportunity: Opportunity,
    display_contexts: dict[tuple[str | None, str], TelegramOpportunityDisplayContext],
) -> TelegramOpportunityDisplayContext:
    key = _opportunity_context_key(opportunity)
    return display_contexts.get(key) or TelegramOpportunityDisplayContext(
        fallback_label=key[0] or key[1],
    )


async def _begin_telegram_group_delivery_attempts(
    *,
    profile: TelegramNotificationProfileOut,
    group: _TelegramOpportunityGroup,
    display_contexts: dict[tuple[str | None, str], TelegramOpportunityDisplayContext],
    publish_id: str | None,
) -> list[_TelegramDeliveryItem]:
    attempted: list[_TelegramDeliveryItem] = []
    for opportunity in group.opportunities:
        fingerprint = telegram_opportunity_fingerprint(opportunity)
        should_attempt = await odds_store.begin_telegram_delivery_attempt(
            profile_id=profile.id,
            opportunity_fingerprint=fingerprint,
            publish_id=publish_id,
        )
        if should_attempt:
            attempted.append(
                _TelegramDeliveryItem(
                    fingerprint=fingerprint,
                    opportunity=opportunity,
                    context=_display_context_for(opportunity, display_contexts),
                )
            )
    return attempted


def _telegram_opportunity_groups(
    opportunities: list[Opportunity],
    display_contexts: dict[tuple[str | None, str], TelegramOpportunityDisplayContext],
) -> list[_TelegramOpportunityGroup]:
    grouped: dict[tuple[str, ...], list[Opportunity]] = {}
    for opportunity in opportunities:
        group_key = _subject_group_key(opportunity)
        if group_key is None:
            group_key = ("opportunity", telegram_opportunity_fingerprint(opportunity))
        grouped.setdefault(group_key, []).append(opportunity)

    groups = [
        _TelegramOpportunityGroup(
            key=key,
            opportunities=tuple(sorted(values, key=_opportunity_sort_key)),
            context=_display_context_for(values[0], display_contexts),
        )
        for key, values in grouped.items()
    ]
    return sorted(groups, key=_group_sort_key)


def _subject_group_key(opportunity: Opportunity) -> tuple[str, ...] | None:
    if not opportunity.subject_type:
        return None
    subject_identity = opportunity.subject_key or opportunity.subject_name
    if not subject_identity:
        return None
    event_identity = opportunity.resolved_event_id or opportunity.match_id
    return (
        "subject",
        event_identity,
        opportunity.subject_type,
        subject_identity,
        opportunity.market_type,
    )


def _group_sort_key(group: _TelegramOpportunityGroup) -> tuple:
    return _opportunity_sort_key(group.opportunities[0])


def _opportunity_sort_key(opportunity: Opportunity) -> tuple:
    if opportunity.opportunity_type == "middle":
        rank = (
            -_rank_value(opportunity.middle_ev_rank),
            -_rank_value(opportunity.middle_ev),
            -_rank_value(opportunity.middle_profit_margin),
            -_rank_value(_opportunity_gap(opportunity)),
        )
    else:
        rank = (
            -_rank_value(opportunity.profit_margin),
            -_rank_value(opportunity.middle_profit_margin),
            -_rank_value(_opportunity_gap(opportunity)),
            0.0,
        )
    return (*rank, _opportunity_tie_key(opportunity))


def _opportunity_tie_key(opportunity: Opportunity) -> tuple:
    leg_key = tuple(
        sorted(
            (
                leg.bookmaker_id,
                leg.market_type,
                leg.outcome_code,
                _stable_float(leg.line) or "",
                _stable_float(leg.odds) or "",
            )
            for leg in opportunity.legs
        )
    )
    return (
        opportunity.resolved_event_id or opportunity.match_id,
        opportunity.subject_type or "",
        opportunity.subject_key or opportunity.subject_name or "",
        opportunity.opportunity_type,
        opportunity.market_type,
        tuple(sorted(opportunity.market_keys)),
        leg_key,
    )


def _rank_value(value: float | None) -> float:
    return value if value is not None else _NEGATIVE_INFINITY


def format_telegram_opportunity(
    opportunity: Opportunity,
    context: TelegramOpportunityDisplayContext | None = None,
) -> str:
    context = context or TelegramOpportunityDisplayContext(
        fallback_label=opportunity.resolved_event_id or opportunity.match_id,
    )
    return format_telegram_opportunity_group(
        [
            _TelegramDeliveryItem(
                fingerprint="",
                opportunity=opportunity,
                context=context,
            )
        ]
    )


def format_telegram_opportunity_group(items: list[_TelegramDeliveryItem]) -> str:
    if not items:
        return ""
    primary = items[0].opportunity
    context = items[0].context
    subject = _format_subject(primary)
    market = _format_market_label(primary.market_type)
    lines = [
        f"<b>{html.escape(subject)} - {html.escape(market)}</b>",
        _format_context_line(context),
        _format_metric_line(primary),
    ]
    lines.append("")
    lines.extend(_format_numbered_legs(primary))

    extra_items = items[1:]
    if extra_items:
        lines.extend(["", "More new options:"])
        omitted = 0
        shown = 0
        for item in extra_items:
            summary = _format_compact_opportunity(item.opportunity)
            if shown >= TELEGRAM_GROUP_SUMMARY_LIMIT:
                omitted += 1
                continue
            if _message_length([*lines, summary]) > TELEGRAM_MESSAGE_SOFT_LIMIT:
                omitted += 1
                continue
            lines.append(summary)
            shown += 1
        if omitted:
            lines.append(f"+{omitted} more new options in this group")
    return "\n".join(lines)


def format_telegram_middle_digest(
    profile: TelegramNotificationProfileOut,
    groups: list[list[_TelegramDeliveryItem]],
    *,
    matched_group_count: int | None = None,
    remaining_group_count: int = 0,
) -> str:
    matched_group_count = matched_group_count if matched_group_count is not None else len(groups)
    lines = [
        "<b>KvotoLovac middle digest</b>",
        f"<b>{html.escape(profile.label)}</b>",
        f"Showing {len(groups)} new middle groups of {matched_group_count} matched",
        "",
    ]
    for index, group_items in enumerate(groups, start=1):
        primary = group_items[0].opportunity
        context = group_items[0].context
        lines.extend(
            [
                f"{index}) <b>{html.escape(_format_subject(primary))} - {html.escape(_format_market_label(primary.market_type))}</b>",
                f"   {_format_context_line(context)}",
                f"   {_format_metric_line(primary, bold=False)}",
                f"   {_format_inline_legs(primary)}",
            ]
        )
        if len(group_items) > 1:
            lines.append(f"   +{len(group_items) - 1} more new options in this group")
    if remaining_group_count > 0:
        lines.append("")
        lines.append(f"+{remaining_group_count} matched groups remain eligible later")
    return "\n".join(lines)


def _format_subject(opportunity: Opportunity) -> str:
    return _trim_text(
        opportunity.subject_name
        or opportunity.subject_key
        or _format_market_label(opportunity.market_type),
        80,
    )


def _format_market_label(market_type: str) -> str:
    label = market_type
    if label.startswith("player_"):
        label = label[len("player_") :]
    return label.replace("_", " ")


def _format_opportunity_type(opportunity_type: str) -> str:
    labels = {
        "same_line_arbitrage": "same-line arb",
        "complementary_outcomes": "complementary",
        "middle": "middle",
    }
    return labels.get(opportunity_type, opportunity_type.replace("_", " "))


def _format_context_line(context: TelegramOpportunityDisplayContext) -> str:
    if context.home_team and context.away_team:
        return (
            f"{html.escape(_trim_text(context.home_team, 80))} vs "
            f"{html.escape(_trim_text(context.away_team, 80))}"
        )
    fallback = context.fallback_label or "unknown"
    return f"Event: <code>{html.escape(fallback)}</code>"


def _format_metric_line(opportunity: Opportunity, *, bold: bool = True) -> str:
    parts: list[str] = []
    if opportunity.opportunity_type == "middle":
        ev = _format_percent(opportunity.middle_ev)
        if ev is not None:
            parts.append(_bold(f"EV {ev}", enabled=bold))
        else:
            parts.append(_bold("Middle", enabled=bold))
        gap = _opportunity_gap(opportunity)
        if gap is not None:
            parts.append(f"gap {_format_number(gap)}")
        hit_probability = _format_percent(opportunity.middle_hit_probability)
        if hit_probability is not None:
            parts.append(f"hit {hit_probability}")
        payout = _format_percent(opportunity.middle_profit_margin)
        if payout is not None:
            parts.append(f"payout {payout}")
    else:
        roi = _format_percent(opportunity.profit_margin)
        if roi is not None:
            parts.append(_bold(f"ROI {roi}", enabled=bold))
        else:
            parts.append(_bold("Opportunity", enabled=bold))
    parts.append(_format_opportunity_type(opportunity.opportunity_type))
    parts.append(f"{len({leg.bookmaker_id for leg in opportunity.legs[:2]})} books")
    return " | ".join(html.escape(part) if "<b>" not in part else part for part in parts)


def _bold(value: str, *, enabled: bool = True) -> str:
    escaped = html.escape(value)
    return f"<b>{escaped}</b>" if enabled else escaped


def _format_numbered_legs(opportunity: Opportunity) -> list[str]:
    return [
        f"{index}) {_format_leg(leg)}"
        for index, leg in enumerate(opportunity.legs[:2], start=1)
    ]


def _format_inline_legs(opportunity: Opportunity) -> str:
    return " / ".join(_format_leg(leg, bold_bookmaker=False) for leg in opportunity.legs[:2])


def _format_compact_opportunity(opportunity: Opportunity) -> str:
    return f"- {_format_short_metric(opportunity)} | {_format_inline_legs(opportunity)}"


def _format_short_metric(opportunity: Opportunity) -> str:
    if opportunity.opportunity_type == "middle":
        ev = _format_percent(opportunity.middle_ev)
        gap = _opportunity_gap(opportunity)
        parts = ["EV " + ev if ev is not None else "middle"]
        if gap is not None:
            parts.append(f"gap {_format_number(gap)}")
        return " | ".join(parts)
    roi = _format_percent(opportunity.profit_margin)
    return f"ROI {roi}" if roi is not None else _format_opportunity_type(opportunity.opportunity_type)


def _format_leg(leg, *, bold_bookmaker: bool = True) -> str:
    bookmaker = leg.bookmaker_name or leg.bookmaker_id
    bookmaker_text = html.escape(_trim_text(str(bookmaker), 40))
    if bold_bookmaker:
        bookmaker_text = f"<b>{bookmaker_text}</b>"
    outcome = html.escape(leg.outcome_code.replace("_", " ").title())
    line = "" if leg.line is None else f" {_format_number(leg.line)}"
    raw_label = ""
    if leg.raw_label:
        raw_label = f" ({html.escape(_trim_text(leg.raw_label, 80))})"
    return (
        f"{bookmaker_text} {outcome}{html.escape(line)} @ "
        f"{html.escape(_format_number(leg.odds))}{raw_label}"
    )


def _trim_text(value: str, max_length: int) -> str:
    value = " ".join(value.split())
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def _message_length(lines: list[str]) -> int:
    return len("\n".join(lines))


def _telegram_response_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _telegram_retry_after(payload: dict) -> int | None:
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after = parameters.get("retry_after")
    if isinstance(retry_after, bool):
        return None
    if isinstance(retry_after, int):
        return max(retry_after, 0)
    if isinstance(retry_after, str) and retry_after.isdigit():
        return int(retry_after)
    return None


async def _record_telegram_delivery_failure(
    *,
    profile_id: int,
    opportunity_fingerprints: list[str],
    exc: Exception,
) -> bool:
    error = str(exc) or type(exc).__name__
    retry_after = exc.retry_after if isinstance(exc, TelegramBotAPIError) else None
    for fingerprint in opportunity_fingerprints:
        await odds_store.mark_telegram_delivery_failed(
            profile_id=profile_id,
            opportunity_fingerprint=fingerprint,
            error=error,
        )
    if retry_after is not None:
        await odds_store.mark_telegram_profile_rate_limited(
            profile_id=profile_id,
            retry_after_seconds=retry_after,
            error=error,
        )
        logger.warning(
            "Telegram profile %s rate-limited for %s seconds: %s",
            profile_id,
            retry_after,
            error,
        )
        return True
    await odds_store.mark_telegram_profile_delivery_error(
        profile_id=profile_id,
        error=error,
    )
    logger.warning(
        "Telegram opportunity delivery failed for profile %s: %s",
        profile_id,
        error,
    )
    return False


def _profile_is_rate_limited(profile: TelegramNotificationProfileOut) -> bool:
    if not profile.rate_limited_until:
        return False
    try:
        until = datetime.fromisoformat(profile.rate_limited_until)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _middle_digest_rank(opportunity: Opportunity) -> tuple[float, float, float, float, float]:
    gap = _opportunity_gap(opportunity) or 0.0
    min_odds = min((leg.odds for leg in opportunity.legs[:2]), default=0.0)
    return (
        1.0 if opportunity.middle_ev_rank is not None else 0.0,
        opportunity.middle_ev_rank or -999.0,
        gap,
        opportunity.middle_profit_margin or -999.0,
        min_odds,
    )


def _stable_float(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.8g}"


def _format_number(value: float) -> str:
    return f"{value:g}"


def _format_line(value: float | None) -> str:
    return "-" if value is None else _format_number(value)


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value * 100:.2f}%"
