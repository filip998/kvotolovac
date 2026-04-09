from __future__ import annotations

import abc
import json
import logging
from typing import Optional

from ..store import odds_store
from .analyzer import Discrepancy

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

    async def notify_discrepancies(self, discrepancies: list[Discrepancy]) -> int:
        """Send notifications for discrepancies above threshold. Returns count sent."""
        count = 0
        for d in discrepancies:
            if d.gap >= self.gap_threshold:
                title = f"Discrepancy: {d.player_name or 'game'} ({d.gap}pt gap)"
                message = (
                    f"{d.bookmaker_a_id} over {d.threshold_a} vs "
                    f"{d.bookmaker_b_id} under {d.threshold_b} — "
                    f"gap {d.gap}, margin {d.profit_margin}"
                )
                data = {
                    "match_id": d.match_id,
                    "player_name": d.player_name,
                    "gap": d.gap,
                    "profit_margin": d.profit_margin,
                    "bookmaker_a": d.bookmaker_a_id,
                    "bookmaker_b": d.bookmaker_b_id,
                }
                for provider in self._providers:
                    await provider.send("discrepancy", title, message, data)
                count += 1
        return count
