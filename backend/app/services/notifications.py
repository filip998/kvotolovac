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
                publish_id=publish_id,
                covered_fingerprints=covered_fingerprints,
            )
            if rate_limited:
                continue
            await self._send_middle_digest(
                profile,
                middle_opportunities,
                publish_id=publish_id,
                covered_fingerprints=covered_fingerprints,
            )
        return len(covered_fingerprints)

    async def _send_individual_opportunities(
        self,
        profile: TelegramNotificationProfileOut,
        opportunities: list[Opportunity],
        *,
        publish_id: str | None,
        covered_fingerprints: set[str],
    ) -> tuple[int, bool]:
        sent_count = 0
        for opportunity in opportunities:
            if sent_count >= TELEGRAM_MAX_INDIVIDUAL_MESSAGES_PER_PROFILE:
                break
            fingerprint = telegram_opportunity_fingerprint(opportunity)
            should_attempt = await odds_store.begin_telegram_delivery_attempt(
                profile_id=profile.id,
                opportunity_fingerprint=fingerprint,
                publish_id=publish_id,
            )
            if not should_attempt:
                continue
            try:
                result = await self._bot_client.send_message(
                    chat_id=profile.chat_id,
                    text=format_telegram_opportunity(opportunity),
                )
            except Exception as exc:
                rate_limited = await _record_telegram_delivery_failure(
                    profile_id=profile.id,
                    opportunity_fingerprints=[fingerprint],
                    exc=exc,
                )
                if rate_limited:
                    return sent_count, True
                continue
            await odds_store.mark_telegram_delivery_sent(
                profile_id=profile.id,
                opportunity_fingerprint=fingerprint,
                telegram_message_id=result.message_id,
            )
            await odds_store.clear_telegram_profile_delivery_error(profile.id)
            covered_fingerprints.add(fingerprint)
            sent_count += 1
        return sent_count, False

    async def _send_middle_digest(
        self,
        profile: TelegramNotificationProfileOut,
        opportunities: list[Opportunity],
        *,
        publish_id: str | None,
        covered_fingerprints: set[str],
    ) -> bool:
        attempted: list[tuple[str, Opportunity]] = []
        for opportunity in opportunities:
            fingerprint = telegram_opportunity_fingerprint(opportunity)
            should_attempt = await odds_store.begin_telegram_delivery_attempt(
                profile_id=profile.id,
                opportunity_fingerprint=fingerprint,
                publish_id=publish_id,
            )
            if should_attempt:
                attempted.append((fingerprint, opportunity))
        if not attempted:
            return False

        digest_opportunities = [opportunity for _, opportunity in attempted]
        try:
            result = await self._bot_client.send_message(
                chat_id=profile.chat_id,
                text=format_telegram_middle_digest(profile, digest_opportunities),
            )
        except Exception as exc:
            await _record_telegram_delivery_failure(
                profile_id=profile.id,
                opportunity_fingerprints=[fingerprint for fingerprint, _ in attempted],
                exc=exc,
            )
            return False

        for fingerprint, _opportunity in attempted:
            await odds_store.mark_telegram_delivery_sent(
                profile_id=profile.id,
                opportunity_fingerprint=fingerprint,
                telegram_message_id=result.message_id,
            )
            covered_fingerprints.add(fingerprint)
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


def format_telegram_opportunity(opportunity: Opportunity) -> str:
    subject = opportunity.subject_name or opportunity.subject_key or opportunity.market_type
    identity = opportunity.resolved_event_id or opportunity.match_id
    gap = _opportunity_gap(opportunity)
    lines = [
        "<b>KvotoLovac opportunity</b>",
        f"<b>{html.escape(subject)}</b>",
        (
            f"{html.escape(opportunity.sport)} | {html.escape(opportunity.market_type)} | "
            f"{html.escape(opportunity.opportunity_type)}"
        ),
        f"Event: <code>{html.escape(identity)}</code>",
    ]
    if gap is not None:
        lines.append(f"Gap: <b>{html.escape(_format_number(gap))}</b>")
    if opportunity.opportunity_type == "middle":
        ev = _format_percent(opportunity.middle_ev)
        if ev is not None:
            lines.append(f"Expected ROI: <b>{html.escape(ev)}</b>")
        hit_probability = _format_percent(opportunity.middle_hit_probability)
        if hit_probability is not None:
            lines.append(f"Hit probability: <b>{html.escape(hit_probability)}</b>")
        if opportunity.middle_model_confidence:
            lines.append(
                f"Model confidence: <b>{html.escape(opportunity.middle_model_confidence)}</b>"
            )
        payout = _format_percent(opportunity.middle_profit_margin)
        if payout is not None:
            lines.append(f"Middle payout if hit: <b>{html.escape(payout)}</b>")
        outside = _format_percent(opportunity.profit_margin)
        if outside is not None:
            lines.append(f"Outside result: <b>{html.escape(outside)}</b>")
    else:
        roi = _format_percent(opportunity.profit_margin)
        if roi is not None:
            lines.append(f"ROI: <b>{html.escape(roi)}</b>")
    lines.append("")
    for index, leg in enumerate(opportunity.legs[:2], start=1):
        line_value = "" if leg.line is None else f" line {html.escape(_format_number(leg.line))}"
        raw_label = f" ({html.escape(leg.raw_label)})" if leg.raw_label else ""
        lines.append(
            f"{index}. <b>{html.escape(leg.bookmaker_id)}</b> "
            f"{html.escape(leg.outcome_code)}{line_value} @ {html.escape(_format_number(leg.odds))}"
            f"{raw_label}"
        )
    return "\n".join(lines)


def format_telegram_middle_digest(
    profile: TelegramNotificationProfileOut,
    opportunities: list[Opportunity],
) -> str:
    ranked = sorted(opportunities, key=_middle_digest_rank, reverse=True)
    shown = ranked[:TELEGRAM_MIDDLE_DIGEST_LIMIT]
    lines = [
        "<b>KvotoLovac middle digest</b>",
        f"<b>{html.escape(profile.label)}</b>",
        f"{len(opportunities)} middles matched this profile",
        "",
    ]
    for index, opportunity in enumerate(shown, start=1):
        subject = opportunity.subject_name or opportunity.subject_key or opportunity.market_type
        gap = _opportunity_gap(opportunity)
        ev = _format_percent(opportunity.middle_ev) or "fallback"
        hit_probability = _format_percent(opportunity.middle_hit_probability) or "n/a"
        payout = _format_percent(opportunity.middle_profit_margin) or "n/a"
        outside = _format_percent(opportunity.profit_margin) or "n/a"
        first_leg, second_leg = opportunity.legs[:2]
        lines.extend(
            [
                (
                    f"{index}. <b>{html.escape(subject)}</b> | "
                    f"EV {html.escape(ev)} | hit {html.escape(hit_probability)} | "
                    f"gap {html.escape(_format_number(gap or 0))} | "
                    f"middle payout {html.escape(payout)} | outside {html.escape(outside)}"
                ),
                (
                    f"   {html.escape(first_leg.bookmaker_id)} {html.escape(first_leg.outcome_code)} "
                    f"{html.escape(_format_line(first_leg.line))} @ {html.escape(_format_number(first_leg.odds))}"
                ),
                (
                    f"   {html.escape(second_leg.bookmaker_id)} {html.escape(second_leg.outcome_code)} "
                    f"{html.escape(_format_line(second_leg.line))} @ {html.escape(_format_number(second_leg.odds))}"
                ),
            ]
        )
    if len(opportunities) > len(shown):
        lines.append("")
        lines.append(f"+{len(opportunities) - len(shown)} more covered by this digest")
    return "\n".join(lines)


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
