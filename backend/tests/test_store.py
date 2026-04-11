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
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_odds(
        NormalizedOdds(
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
        ),
        scraped_at="2026-04-11T20:06:00.735723",
    )
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
    await odds_store.upsert_odds(odds, scraped_at="2026-04-11T20:06:00.735723")

    current = await odds_store.get_odds_for_match("m1")
    assert len(current) == 1
    assert current[0].threshold == 16.5

    history = await odds_store.get_odds_history_for_match("m1")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_get_matches_returns_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )
    fresh_odds = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds, scraped_at="2026-04-11T20:06:00.735723")

    matches = await odds_store.get_matches()

    assert [match.id for match in matches] == ["fresh"]


@pytest.mark.asyncio
async def test_get_odds_for_match_returns_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=5.5,
        over_odds=1.91,
        under_odds=1.8,
    )
    fresh_odds = NormalizedOdds(
        match_id="m1",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds, scraped_at="2026-04-11T20:06:00.735723")

    current = await odds_store.get_odds_for_match("m1")
    history = await odds_store.get_odds_history_for_match("m1")

    assert len(current) == 1
    assert current[0].threshold == 6.5
    assert len(history) == 2


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

    batch_scraped_at = "2026-04-11T20:06:00.735723"
    await odds_store.upsert_odds(line, scraped_at=batch_scraped_at)
    await odds_store.upsert_odds(milestone, scraped_at=batch_scraped_at)

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


@pytest.mark.asyncio
async def test_system_status_counts_only_latest_scrape_batch():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )
    fresh_odds_a = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_points",
        player_name="Tamir Blatt",
        threshold=6.5,
        over_odds=2.09,
        under_odds=1.66,
    )
    fresh_odds_b = NormalizedOdds(
        match_id="fresh",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Maccabi Tel Aviv",
        away_team="Hapoel Tel-Aviv",
        market_type="player_assists",
        player_name="Tamir Blatt",
        threshold=5.5,
        over_odds=2.0,
        under_odds=1.73,
    )

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.upsert_odds(fresh_odds_a, scraped_at="2026-04-11T20:06:00.735723")
    await odds_store.upsert_odds(fresh_odds_b, scraped_at="2026-04-11T20:06:00.735723")

    status = await odds_store.get_system_status()

    assert status.total_matches == 1
    assert status.total_odds == 2
    assert status.last_scrape_at == "2026-04-11T20:06:00.735723"


@pytest.mark.asyncio
async def test_current_snapshot_can_hide_previous_rows_without_new_odds():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    stale_odds = NormalizedOdds(
        match_id="stale",
        bookmaker_id="meridian",
        league_id="euroleague",
        home_team="Bayern Munich",
        away_team="Maccabi Tel Aviv",
        market_type="player_points",
        player_name="Saben Lee",
        threshold=13.5,
        over_odds=1.8,
        under_odds=2.0,
    )

    await odds_store.upsert_odds(stale_odds, scraped_at="2026-04-10T13:39:04.516801")
    await odds_store.set_current_snapshot("2026-04-11T20:06:00.735723")

    matches = await odds_store.get_matches()
    status = await odds_store.get_system_status()

    assert matches == []
    assert status.total_matches == 0
    assert status.total_odds == 0
    assert status.last_scrape_at == "2026-04-11T20:06:00.735723"


@pytest.mark.asyncio
async def test_legacy_fallback_groups_recent_rows_before_snapshot_exists():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("old", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("recent-a", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")
    await odds_store.upsert_match("recent-b", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Bayern Munich",
            away_team="Maccabi Tel Aviv",
            market_type="player_points",
            player_name="Saben Lee",
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="2026-04-10T13:39:04.516801",
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="recent-a",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Maccabi Tel Aviv",
            away_team="Hapoel Tel-Aviv",
            market_type="player_points",
            player_name="Tamir Blatt",
            threshold=6.5,
            over_odds=2.09,
            under_odds=1.66,
        ),
        scraped_at="2026-04-11T20:00:00.000001",
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="recent-b",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-04-11T20:05:00.000001",
    )

    matches = await odds_store.get_matches(limit=10)
    status = await odds_store.get_system_status()

    assert {match.id for match in matches} == {"recent-a", "recent-b"}
    assert status.total_matches == 2
    assert status.total_odds == 2
    assert status.last_scrape_at == "2026-04-11T20:05:00.000001"
