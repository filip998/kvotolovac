from __future__ import annotations

import pytest

from app.services.analyzer import Discrepancy
from app.services.notifications import (
    InAppNotificationProvider,
    NotificationProvider,
    NotificationService,
)
from app.store import odds_store


def _make_disc(gap: float, player: str = "Lundberg") -> Discrepancy:
    return Discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name=player,
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=16.5 + gap,
        odds_a=1.85,
        odds_b=2.00,
        gap=gap,
        profit_margin=-0.04,
    )


@pytest.mark.asyncio
async def test_notification_provider_interface():
    """InAppNotificationProvider implements the abstract interface."""
    provider = InAppNotificationProvider()
    assert isinstance(provider, NotificationProvider)


@pytest.mark.asyncio
async def test_in_app_provider_stores_notification():
    provider = InAppNotificationProvider()
    await provider.send("discrepancy", "Test Alert", "body", {"gap": 2.0})
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1
    assert notifs[0].title == "Test Alert"


@pytest.mark.asyncio
async def test_notification_service_threshold_filter():
    service = NotificationService(gap_threshold=2.0)
    service.register_provider(InAppNotificationProvider())

    discs = [_make_disc(1.0), _make_disc(2.5)]
    count = await service.notify_discrepancies(discs)
    assert count == 1  # only gap=2.5 meets threshold

    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1


@pytest.mark.asyncio
async def test_notification_service_no_providers():
    service = NotificationService(gap_threshold=1.0)
    discs = [_make_disc(2.0)]
    count = await service.notify_discrepancies(discs)
    assert count == 1  # counted even without providers


@pytest.mark.asyncio
async def test_notification_service_multiple_providers():
    service = NotificationService(gap_threshold=1.0)
    service.register_provider(InAppNotificationProvider())
    service.register_provider(InAppNotificationProvider())

    discs = [_make_disc(2.0)]
    count = await service.notify_discrepancies(discs)
    assert count == 1

    # Both providers should have stored a notification
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 2
