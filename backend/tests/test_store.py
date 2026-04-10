from __future__ import annotations

import pytest

from app.models.schemas import NormalizedOdds
from app.store import odds_store


@pytest.mark.asyncio
async def test_upsert_and_get_bookmaker():
    await odds_store.upsert_bookmaker("mozzart", "Mozzart", "https://mozzartbet.com")
    bookmakers = await odds_store.get_bookmakers()
    assert len(bookmakers) == 1
    assert bookmakers[0].id == "mozzart"
    assert bookmakers[0].name == "Mozzart"


@pytest.mark.asyncio
async def test_upsert_and_get_league():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    leagues = await odds_store.get_leagues()
    assert len(leagues) == 1
    assert leagues[0].sport == "basketball"


@pytest.mark.asyncio
async def test_upsert_and_get_match():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    matches = await odds_store.get_matches()
    assert len(matches) == 1
    assert matches[0].home_team == "Partizan"


@pytest.mark.asyncio
async def test_get_match_by_id():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    match = await odds_store.get_match("m1")
    assert match is not None
    assert match.away_team == "Crvena Zvezda"


@pytest.mark.asyncio
async def test_get_nonexistent_match():
    match = await odds_store.get_match("nonexistent")
    assert match is None


@pytest.mark.asyncio
async def test_upsert_odds_and_history():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")

    odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="mozzart",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points",
        player_name="Iffe Lundberg",
        threshold=16.5,
        over_odds=1.85,
        under_odds=1.95,
    )
    await odds_store.upsert_odds(odds)

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 1
    assert current[0].threshold == 16.5

    history = await odds_store.get_odds_history_for_match("m1")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_upsert_odds_keeps_line_and_milestone_rows_separate():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("oktagonbet", "OktagonBet")

    line = NormalizedOdds(
        match_id="m1",
        bookmaker_id="oktagonbet",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points",
        player_name="Iffe Lundberg",
        threshold=9.5,
        over_odds=1.85,
        under_odds=1.95,
    )
    milestone = NormalizedOdds(
        match_id="m1",
        bookmaker_id="oktagonbet",
        league_id="euroleague",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points_milestones",
        player_name="Iffe Lundberg",
        threshold=9.5,
        over_odds=1.85,
        under_odds=None,
    )

    await odds_store.upsert_odds(line)
    await odds_store.upsert_odds(milestone)

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 2
    assert {offer.market_type for offer in current} == {
        "player_points",
        "player_points_milestones",
    }


@pytest.mark.asyncio
async def test_insert_and_get_discrepancy():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    disc_id = await odds_store.insert_discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name="Iffe Lundberg",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=18.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.04,
    )
    assert disc_id > 0

    discs = await odds_store.get_discrepancies()
    assert len(discs) == 1
    assert discs[0].gap == 2.0


@pytest.mark.asyncio
async def test_get_discrepancy_detail():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    disc_id = await odds_store.insert_discrepancy(
        match_id="m1", market_type="player_points", player_name="Lundberg",
        bookmaker_a_id="mozzart", bookmaker_b_id="meridian",
        threshold_a=16.5, threshold_b=18.5,
        odds_a=1.85, odds_b=2.0, gap=2.0, profit_margin=0.04,
    )
    detail = await odds_store.get_discrepancy(disc_id)
    assert detail is not None
    assert detail.bookmaker_a_name == "Mozzart"
    assert detail.home_team == "Partizan"


@pytest.mark.asyncio
async def test_deactivate_discrepancies():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "A", "B")
    await odds_store.upsert_bookmaker("a", "A")
    await odds_store.upsert_bookmaker("b", "B")

    await odds_store.insert_discrepancy(
        "m1", "player_points", "P", "a", "b", 10, 12, 1.9, 2.0, 2.0, 0.03
    )
    await odds_store.deactivate_all_discrepancies()
    active = await odds_store.get_discrepancies(active_only=True)
    assert len(active) == 0


@pytest.mark.asyncio
async def test_notifications_crud():
    nid = await odds_store.insert_notification("discrepancy", "Test", "msg", {"gap": 2.0})
    assert nid > 0
    notifs = await odds_store.get_notifications()
    assert len(notifs) == 1
    assert notifs[0].title == "Test"


@pytest.mark.asyncio
async def test_system_status():
    status = await odds_store.get_system_status()
    assert status.status == "ok"
    assert status.total_matches == 0
