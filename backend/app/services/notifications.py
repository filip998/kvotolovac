from __future__ import annotations

import abc
import json
import logging
from typing import Optional

from ..store import odds_store
from .opportunity_analyzer import Opportunity

logger = logging.getLogger(__name__)


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


class NotificationService:
    """Orchestrates notification delivery through registered providers."""

    def __init__(self, gap_threshold: float = 1.5) -> None:
        self._providers: list[NotificationProvider] = []
        self.gap_threshold = gap_threshold

    def register_provider(self, provider: NotificationProvider) -> None:
        self._providers.append(provider)

    async def notify_opportunities(self, opportunities: list[Opportunity]) -> int:
        """Send notifications for generic opportunities above threshold. Returns count sent."""
        count = 0
        for opportunity in opportunities:
            gap = _opportunity_gap(opportunity)
            if gap is None or gap < self.gap_threshold:
                continue
            if not self._providers:
                continue
            first_leg, second_leg = opportunity.legs[:2]
            subject = opportunity.subject_name or opportunity.market_type
            title = f"Opportunity: {subject} ({gap}pt gap)"
            message = (
                f"{first_leg.bookmaker_id} {first_leg.outcome_code} {first_leg.line} vs "
                f"{second_leg.bookmaker_id} {second_leg.outcome_code} {second_leg.line} — "
                f"gap {gap}, edge ROI {opportunity.profit_margin}, "
                f"middle ROI {opportunity.middle_profit_margin}"
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
            count += 1
        return count


def _opportunity_gap(opportunity: Opportunity) -> float | None:
    if opportunity.opportunity_type != "middle" or len(opportunity.legs) != 2:
        return None
    first_leg, second_leg = opportunity.legs
    if first_leg.line is None or second_leg.line is None:
        return None
    return abs(first_leg.line - second_leg.line)
