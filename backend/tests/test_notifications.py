from __future__ import annotations

import pytest

from app.models.schemas import OpportunityLeg
from app.services.opportunity_analyzer import Opportunity
from app.services.notifications import (
    InAppNotificationProvider,
    NotificationProvider,
    NotificationService,
)
from app.store import odds_store


def _make_opportunity(gap: float, subject: str = "Lundberg") -> Opportunity:
    return Opportunity(
        sport="basketball",
        match_id="m1",
        opportunity_type="middle",
        market_type="player_points",
        line=None,
        profit_margin=-0.04,
        middle_profit_margin=0.5,
        subject_type="player",
        subject_name=subject,
        legs=[
            OpportunityLeg(
                bookmaker_id="mozzart",
                market_type="player_points",
                outcome_code="over",
                line=16.5,
                odds=1.85,
            ),
            OpportunityLeg(
                bookmaker_id="meridian",
                market_type="player_points",
                outcome_code="under",
                line=16.5 + gap,
                odds=2.00,
            ),
        ],
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
