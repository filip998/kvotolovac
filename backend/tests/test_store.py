from __future__ import annotations

import asyncio
import sqlite3

import aiosqlite
import pytest

from app.config import settings
from app.database import close_db, get_db, init_db
from app.models.schemas import (
    EventReviewCaseIn,
    NormalizedOdds,
    NormalizedOutcomeOffer,
    ResolvedEventIn,
    ResolvedEventMemberIn,
    TeamReviewDiagnostic,
    UnresolvedOddsDiagnostic,
)
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
    assert [book.id for book in matches[0].available_bookmakers] == ["mozzart"]


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
        source_url="https://example.com/m1",
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
    assert current[0].source_url == "https://example.com/m1"

    history = await odds_store.get_odds_history_for_match("m1")
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_get_current_canonical_offers_for_matches_reads_odds_and_outcome_offers():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_match(
        "basketball-match",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "football-match",
        "premier_league",
        "Team Alpha",
        "Team Beta",
        sport="football",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    scraped_at = "2030-01-01T19:55:00+00:00"

    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="basketball-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            sport="basketball",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url="https://example.com/basketball",
            market_type="player_points",
            player_name="Nikola Jovic",
            threshold=16.5,
            over_odds=1.91,
            under_odds=1.87,
        ),
        scraped_at=scraped_at,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="football-match",
            bookmaker_id="maxbet",
            league_id="premier_league",
            sport="football",
            home_team="Team Alpha",
            away_team="Team Beta",
            source_url="https://example.com/football",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.85,
            line=2.5,
            raw_label="3+",
        ),
        scraped_at=scraped_at,
    )
    await odds_store.set_current_snapshot(scraped_at)

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["basketball-match", "football-match"]
    )

    assert [(offer.bookmaker_id, offer.outcome_code) for offer in offers] == [
        ("mozzart", "over"),
        ("mozzart", "under"),
        ("maxbet", "over"),
    ]
    basketball_market = offers[0].market
    football_market = offers[2].market
    assert basketball_market.market_type == "player_points"
    assert basketball_market.subject_type == "player"
    assert basketball_market.subject_name == "Nikola Jovic"
    assert offers[0].scraped_at == scraped_at
    assert football_market.market_type == "football_total_goals"
    assert football_market.subject_type == "event"
    assert football_market.line == 2.5
    assert offers[2].scraped_at == scraped_at


@pytest.mark.asyncio
async def test_current_canonical_offers_use_active_resolved_event_identity():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match(
        "bookmaker-match-a",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "bookmaker-match-b",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    scraped_at = "2030-01-01T19:55:00+00:00"
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="resolved-partizan-zvezda",
            sport="basketball",
            start_time="2030-01-01T20:00:00+00:00",
            primary_match_id="bookmaker-match-a",
            method="manual",
        )
    )
    for match_id, bookmaker_id, player_name in (
        ("bookmaker-match-a", "mozzart", "Nikola Jokić"),
        ("bookmaker-match-b", "maxbet", "N. Jokic"),
    ):
        await odds_store.link_resolved_event_member(
            ResolvedEventMemberIn(
                resolved_event_id="resolved-partizan-zvezda",
                match_id=match_id,
                bookmaker_id=bookmaker_id,
            )
        )
        await odds_store.upsert_odds(
            NormalizedOdds(
                match_id=match_id,
                bookmaker_id=bookmaker_id,
                league_id="euroleague",
                sport="basketball",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                market_type="player_points",
                player_name=player_name,
                threshold=16.5,
                over_odds=1.91,
                under_odds=None,
            ),
            scraped_at=scraped_at,
        )
    await odds_store.set_current_snapshot(scraped_at)

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["bookmaker-match-a", "bookmaker-match-b"]
    )

    assert len(offers) == 2
    assert {offer.market.event_id for offer in offers} == {"resolved-partizan-zvezda"}
    assert len({offer.market_key for offer in offers}) == 1
    assert {offer.market.subject_name for offer in offers} == {"Nikola Jokić"}
    assert {offer.market.subject_key for offer in offers} == {
        offers[0].market.subject_key
    }
    assert offers[0].market.subject_key
    assert offers[0].market.subject_key.startswith("ply_")


@pytest.mark.asyncio
async def test_current_canonical_offers_capture_snapshot_once(monkeypatch):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_match(
        "basketball-match",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
    )
    await odds_store.upsert_match(
        "football-match",
        "premier_league",
        "Team Alpha",
        "Team Beta",
        sport="football",
    )
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    stable_snapshot = "2030-01-01T19:55:00+00:00"
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="basketball-match",
            bookmaker_id="mozzart",
            league_id="euroleague",
            sport="basketball",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Nikola Jovic",
            threshold=16.5,
            over_odds=1.91,
            under_odds=None,
        ),
        scraped_at=stable_snapshot,
    )
    await odds_store.upsert_outcome_offer(
        NormalizedOutcomeOffer(
            match_id="football-match",
            bookmaker_id="maxbet",
            league_id="premier_league",
            sport="football",
            home_team="Team Alpha",
            away_team="Team Beta",
            market_type="football_total_goals",
            outcome_code="over",
            odds=1.85,
            line=2.5,
        ),
        scraped_at=stable_snapshot,
    )
    calls = 0

    async def fake_snapshot_filter(db, alias):
        nonlocal calls
        calls += 1
        return f"{alias}.scraped_at = ?", [stable_snapshot]

    monkeypatch.setattr(
        odds_store,
        "_current_or_legacy_snapshot_filter",
        fake_snapshot_filter,
    )

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["basketball-match", "football-match"]
    )

    assert calls == 1
    assert [(offer.bookmaker_id, offer.scraped_at) for offer in offers] == [
        ("mozzart", stable_snapshot),
        ("maxbet", stable_snapshot),
    ]


@pytest.mark.asyncio
async def test_upsert_odds_preserves_existing_source_url_when_new_snapshot_has_none():
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
            source_url="https://example.com/m1",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-04-11T20:06:00.735723",
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url=None,
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.9,
            under_odds=1.9,
        ),
        scraped_at="2026-04-11T20:11:00.735723",
    )
    await odds_store.set_current_snapshot("2026-04-11T20:11:00.735723")

    current = await odds_store.get_odds_for_match("m1")

    assert len(current) == 1
    assert current[0].source_url == "https://example.com/m1"


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
    assert [book.id for book in matches[0].available_bookmakers] == ["meridian"]


@pytest.mark.asyncio
async def test_get_matches_filters_by_bookmakers_and_keeps_full_coverage():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    batch_scraped_at = "2026-04-11T20:06:00.735723"
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
        scraped_at=batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=17.5,
            over_odds=1.9,
            under_odds=1.85,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m2",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Monaco",
            away_team="Barcelona",
            market_type="player_points",
            player_name="Mike James",
            threshold=19.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    matches = await odds_store.get_matches(bookmaker_ids=["meridian"])

    assert [match.id for match in matches] == ["m1"]
    assert [book.id for book in matches[0].available_bookmakers] == ["meridian", "mozzart"]


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
async def test_insert_and_get_unresolved_odds():
    batch_scraped_at = "2026-04-13T16:36:09.440629"
    await odds_store.upsert_bookmaker("admiralbet", "AdmiralBet")
    await odds_store.upsert_league("aba_liga", "ABA Liga", "basketball")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="admiralbet",
            raw_league_id="AdmiralBet ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="P. Nikolic",
            raw_team_name="Borac Cacak",
            normalized_team_name="Borac Cacak",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
            candidate_matchups=[],
            available_matchups_same_slot=["Dubai vs Buducnost"],
        ),
        scraped_at=batch_scraped_at,
    )
    await odds_store.set_current_snapshot(batch_scraped_at)

    unresolved = await odds_store.get_unresolved_odds()

    assert len(unresolved) == 1
    assert unresolved[0].bookmaker_name == "AdmiralBet"
    assert unresolved[0].league_name == "ABA Liga"
    assert unresolved[0].available_matchups_same_slot == ["Dubai vs Buducnost"]


@pytest.mark.asyncio
async def test_get_unresolved_odds_filters_by_multiple_bookmakers():
    batch_scraped_at = "2026-04-13T18:00:00+00:00"
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    for bookmaker_id in ("maxbet", "meridian"):
        await odds_store.insert_unresolved_odds(
            UnresolvedOddsDiagnostic(
                bookmaker_id=bookmaker_id,
                raw_league_id="ABA Liga",
                league_id="aba_liga",
                market_type="player_points",
                player_name="S. Ilic",
                raw_team_name="Borac",
                normalized_team_name="Borac",
                start_time="2026-04-13T18:00:00+00:00",
                threshold=10.5,
                over_odds=1.8,
                under_odds=2.0,
                reason_code="no_canonical_matchup_for_team_at_slot",
                candidate_count=0,
            ),
            scraped_at=batch_scraped_at,
        )
    await odds_store.set_current_snapshot(batch_scraped_at)

    unresolved = await odds_store.get_unresolved_odds(bookmaker_ids=["meridian"])

    assert len(unresolved) == 1
    assert unresolved[0].bookmaker_id == "meridian"


@pytest.mark.asyncio
async def test_get_unresolved_odds_respects_current_snapshot():
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="S. Ilic",
            raw_team_name="Borac",
            normalized_team_name="Borac",
            start_time="2026-04-13T16:00:00+00:00",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.9,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
        ),
        scraped_at="2026-04-13T16:00:00+00:00",
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="maxbet",
            raw_league_id="ABA Liga",
            league_id="aba_liga",
            market_type="player_points",
            player_name="S. Ilic",
            raw_team_name="Borac",
            normalized_team_name="Borac",
            start_time="2026-04-13T18:00:00+00:00",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_canonical_matchup_for_team_at_slot",
            candidate_count=0,
        ),
        scraped_at="2026-04-13T18:00:00+00:00",
    )
    await odds_store.set_current_snapshot("2026-04-13T18:00:00+00:00")

    unresolved = await odds_store.get_unresolved_odds()

    assert len(unresolved) == 1
    assert unresolved[0].threshold == 10.5


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
        middle_profit_margin=0.96,
    )
    assert disc_id > 0

    discs = await odds_store.get_discrepancies()
    assert len(discs) == 1
    assert discs[0].gap == 2.0
    assert discs[0].middle_profit_margin == 0.96


@pytest.mark.asyncio
async def test_get_discrepancies_filters_by_search_before_pagination():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")

    await odds_store.insert_discrepancy(
        match_id="m2",
        market_type="player_points",
        player_name="Mike James",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=16.5,
        threshold_b=18.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.20,
    )
    await odds_store.insert_discrepancy(
        match_id="m1",
        market_type="player_points",
        player_name="Kendrick Nunn",
        bookmaker_a_id="mozzart",
        bookmaker_b_id="meridian",
        threshold_a=14.5,
        threshold_b=16.5,
        odds_a=1.85,
        odds_b=2.00,
        gap=2.0,
        profit_margin=0.01,
    )

    discs = await odds_store.get_discrepancies(search="nunn", limit=1)

    assert len(discs) == 1
    assert discs[0].player_name == "Kendrick Nunn"


@pytest.mark.asyncio
async def test_discrepancy_leg_match_id_migration_preserves_foreign_keys(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy.db"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.execute(
            """CREATE TABLE discrepancies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT REFERENCES matches(id),
                   market_type TEXT NOT NULL,
                   player_name TEXT,
                   bookmaker_a_id TEXT,
                   bookmaker_b_id TEXT,
                   threshold_a REAL,
                   threshold_b REAL,
                   odds_a REAL,
                   odds_b REAL,
                   gap REAL,
                   profit_margin REAL,
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               )"""
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    rows = await db.execute_fetchall("PRAGMA foreign_key_list(discrepancies)")
    foreign_keys = {(row[3], row[2]) for row in rows}

    assert ("bookmaker_a_match_id", "matches") in foreign_keys
    assert ("bookmaker_b_match_id", "matches") in foreign_keys


@pytest.mark.asyncio
async def test_resolved_event_id_migration_preserves_foreign_keys(
    tmp_path,
    monkeypatch,
):
    await close_db()
    legacy_db_path = tmp_path / "legacy_resolved_event_fk.db"
    with sqlite3.connect(legacy_db_path) as conn:
        conn.executescript(
            """CREATE TABLE discrepancies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   match_id TEXT REFERENCES matches(id),
                   resolved_event_id TEXT,
                   market_type TEXT NOT NULL,
                   player_name TEXT,
                   bookmaker_a_id TEXT,
                   bookmaker_a_match_id TEXT,
                   bookmaker_b_id TEXT,
                   bookmaker_b_match_id TEXT,
                   threshold_a REAL,
                   threshold_b REAL,
                   odds_a REAL,
                   odds_b REAL,
                   gap REAL,
                   profit_margin REAL,
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               );
               INSERT INTO discrepancies (resolved_event_id, market_type)
               VALUES ('missing-event', 'player_points');
               CREATE TABLE opportunities (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   sport TEXT NOT NULL,
                   match_id TEXT REFERENCES matches(id),
                   resolved_event_id TEXT,
                   opportunity_type TEXT NOT NULL,
                   market_type TEXT NOT NULL,
                   line REAL,
                   profit_margin REAL,
                   legs TEXT NOT NULL DEFAULT '[]',
                   detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   is_active BOOLEAN DEFAULT TRUE
               );
               INSERT INTO opportunities (
                   sport,
                   resolved_event_id,
                   opportunity_type,
                   market_type,
                   legs
               )
               VALUES (
                   'basketball',
                   'missing-event',
                   'cross_bookmaker_arbitrage',
                   'player_points',
                   '[]'
               );"""
        )
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{legacy_db_path}")

    await init_db(str(legacy_db_path))
    db = await get_db()
    discrepancy_fks = await db.execute_fetchall("PRAGMA foreign_key_list(discrepancies)")
    opportunity_fks = await db.execute_fetchall("PRAGMA foreign_key_list(opportunities)")

    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in discrepancy_fks
    }
    assert ("resolved_event_id", "resolved_events") in {
        (row[3], row[2]) for row in opportunity_fks
    }
    discrepancy_stale_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM discrepancies WHERE resolved_event_id IS NULL"
    )
    opportunity_stale_rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM opportunities WHERE resolved_event_id IS NULL"
    )
    assert discrepancy_stale_rows[0]["count"] == 1
    assert opportunity_stale_rows[0]["count"] == 1

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO discrepancies (resolved_event_id, market_type) VALUES (?, ?)",
            ("still-missing-event", "player_points"),
        )
        await db.commit()
    await db.rollback()

    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """INSERT INTO opportunities (
                   sport,
                   resolved_event_id,
                   opportunity_type,
                   market_type,
                   legs
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                "basketball",
                "still-missing-event",
                "cross_bookmaker_arbitrage",
                "player_points",
                "[]",
            ),
        )
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_event_review_case_fk_failure_rolls_back_connection():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match(
        "match-1",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time="2030-01-01T20:00:00+00:00",
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await odds_store.upsert_event_review_case(
            EventReviewCaseIn(
                fingerprint="bad-candidate-event",
                sport="basketball",
                start_time="2030-01-01T20:00:00+00:00",
                primary_match_id="match-1",
                candidate_resolved_event_id="missing-event",
                candidate_match_ids=["match-1"],
                reason_code="candidate_event_equivalence",
                source_bookmaker_ids=[],
            )
        )

    case_id = await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint="good-candidate-event",
            sport="basketball",
            start_time="2030-01-01T20:00:00+00:00",
            primary_match_id="match-1",
            candidate_match_ids=["match-1"],
            reason_code="candidate_event_equivalence",
            source_bookmaker_ids=[],
        )
    )

    assert case_id > 0


@pytest.mark.asyncio
async def test_get_discrepancies_filters_by_involved_bookmakers():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Monaco", "Barcelona")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")

    await odds_store.insert_discrepancy(
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
    await odds_store.insert_discrepancy(
        match_id="m2",
        market_type="player_points",
        player_name="Mike James",
        bookmaker_a_id="maxbet",
        bookmaker_b_id="mozzart",
        threshold_a=19.5,
        threshold_b=21.5,
        odds_a=1.9,
        odds_b=1.85,
        gap=2.0,
        profit_margin=0.03,
    )

    discs = await odds_store.get_discrepancies(bookmaker_ids=["meridian"])

    assert len(discs) == 1
    assert discs[0].bookmaker_b_id == "meridian"


@pytest.mark.asyncio
async def test_get_discrepancy_detail():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match_bookmaker_source(
        match_id="m1",
        bookmaker_id="mozzart",
        source_url="https://example.com/mozzart/m1",
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="m1",
        bookmaker_id="meridian",
        source_url="https://example.com/meridian/m1",
    )

    disc_id = await odds_store.insert_discrepancy(
        match_id="m1", market_type="player_points", player_name="Lundberg",
        bookmaker_a_id="mozzart", bookmaker_b_id="meridian",
        threshold_a=16.5, threshold_b=18.5,
        odds_a=1.85, odds_b=2.0, gap=2.0, profit_margin=0.04, middle_profit_margin=0.96,
    )
    detail = await odds_store.get_discrepancy(disc_id)
    assert detail is not None
    assert detail.bookmaker_a_name == "Mozzart"
    assert detail.bookmaker_a_source_url == "https://example.com/mozzart/m1"
    assert detail.bookmaker_b_source_url == "https://example.com/meridian/m1"
    assert detail.home_team == "Partizan"
    assert detail.middle_profit_margin == 0.96


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
async def test_cleanup_retained_data_prunes_stale_snapshot_rows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "persist_inapp_notifications", False)
    monkeypatch.setattr(settings, "odds_history_retention_days", 7)
    monkeypatch.setattr(settings, "team_review_retention_days", 30)

    current_snapshot_at = "2026-04-20T12:00:00"
    stale_snapshot_at = "2026-04-10T12:00:00"
    old_history_at = "2026-03-01T12:00:00"
    recent_history_at = "2026-04-18T12:00:00"

    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match("stale", "euroleague", "Bayern Munich", "Maccabi Tel Aviv")
    await odds_store.upsert_match("fresh", "euroleague", "Maccabi Tel Aviv", "Hapoel Tel-Aviv")

    await odds_store.upsert_odds(
        NormalizedOdds(
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
        ),
        scraped_at=old_history_at,
    )
    await odds_store.upsert_odds(
        NormalizedOdds(
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
        ),
        scraped_at=current_snapshot_at,
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            league_id="euroleague",
            market_type="player_points",
            player_name="Saben Lee",
            raw_team_name="Bayern Munich",
            normalized_team_name="bayern munich",
            start_time=stale_snapshot_at,
            threshold=13.5,
            over_odds=1.8,
            under_odds=2.0,
            reason_code="no_match_found",
        ),
        scraped_at=stale_snapshot_at,
    )
    await odds_store.insert_unresolved_odds(
        UnresolvedOddsDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            league_id="euroleague",
            market_type="player_points",
            player_name="Tamir Blatt",
            raw_team_name="Maccabi Tel Aviv",
            normalized_team_name="maccabi tel aviv",
            start_time=current_snapshot_at,
            threshold=6.5,
            over_odds=2.09,
            under_odds=1.66,
            reason_code="no_match_found",
        ),
        scraped_at=current_snapshot_at,
    )
    await odds_store.insert_discrepancy(
        "stale",
        "player_points",
        "Saben Lee",
        "meridian",
        "mozzart",
        13.5,
        15.5,
        1.8,
        1.95,
        2.0,
        0.03,
    )
    await odds_store.deactivate_all_discrepancies()
    active_discrepancy_id = await odds_store.insert_discrepancy(
        "fresh",
        "player_points",
        "Tamir Blatt",
        "meridian",
        "mozzart",
        6.5,
        8.5,
        2.09,
        1.95,
        2.0,
        0.04,
    )

    old_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            normalized_raw_league_id="euroleague",
            scope_league_id="euroleague",
            raw_team_name="Old Alias",
            normalized_raw_team_name="old alias",
            start_time=old_history_at,
            reason_code="candidate_team_match_same_start_time",
            matched_counterpart_team="Maccabi Tel Aviv",
            canonical_home_team="Old Team",
            canonical_away_team="Maccabi Tel Aviv",
        ),
        scraped_at=old_history_at,
    )
    recent_case_id = await odds_store.insert_team_review_case(
        TeamReviewDiagnostic(
            bookmaker_id="meridian",
            raw_league_id="euroleague",
            normalized_raw_league_id="euroleague",
            scope_league_id="euroleague",
            raw_team_name="Recent Alias",
            normalized_raw_team_name="recent alias",
            start_time=recent_history_at,
            reason_code="candidate_team_match_same_start_time",
            matched_counterpart_team="Hapoel Tel-Aviv",
            canonical_home_team="Recent Team",
            canonical_away_team="Hapoel Tel-Aviv",
        ),
        scraped_at=recent_history_at,
    )

    old_notification_id = await odds_store.insert_notification(
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "discrepancy", "Recent Alert", "body", {"gap": 2.0}
    )
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-01 12:00:00", old_notification_id),
    )
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-19 12:00:00", recent_notification_id),
    )
    await db.commit()

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)

    odds_rows = await db.execute_fetchall(
        "SELECT match_id, scraped_at FROM odds ORDER BY match_id"
    )
    unresolved_rows = await db.execute_fetchall(
        "SELECT raw_team_name, scraped_at FROM unresolved_odds ORDER BY id"
    )
    discrepancy_rows = await db.execute_fetchall(
        "SELECT id, is_active FROM discrepancies ORDER BY id"
    )
    history_rows = await db.execute_fetchall(
        "SELECT match_id, scraped_at FROM odds_history ORDER BY id"
    )
    review_rows = await db.execute_fetchall(
        "SELECT id, raw_team_name FROM team_review_cases ORDER BY id"
    )
    notification_rows = await db.execute_fetchall(
        "SELECT id FROM notifications ORDER BY id"
    )

    assert [(row["match_id"], row["scraped_at"]) for row in odds_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["raw_team_name"], row["scraped_at"]) for row in unresolved_rows] == [
        ("Maccabi Tel Aviv", current_snapshot_at)
    ]
    assert [(row["id"], row["is_active"]) for row in discrepancy_rows] == [
        (active_discrepancy_id, 1)
    ]
    assert [(row["match_id"], row["scraped_at"]) for row in history_rows] == [
        ("fresh", current_snapshot_at)
    ]
    assert [(row["id"], row["raw_team_name"]) for row in review_rows] == [
        (recent_case_id, "Recent Alias")
    ]
    assert notification_rows == []
    assert counts == {
        "deleted_stale_odds": 1,
        "deleted_stale_unresolved_odds": 1,
        "deleted_inactive_discrepancies": 1,
        "deleted_odds_history": 1,
        "deleted_team_review_cases": 1,
        "deleted_notifications": 2,
    }
    assert old_case_id != recent_case_id


@pytest.mark.asyncio
async def test_cleanup_retained_data_keeps_recent_notifications_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "persist_inapp_notifications", True)
    monkeypatch.setattr(settings, "notification_retention_days", 3)

    current_snapshot_at = "2026-04-20T12:00:00"
    old_notification_id = await odds_store.insert_notification(
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
    )
    recent_notification_id = await odds_store.insert_notification(
        "discrepancy", "Recent Alert", "body", {"gap": 2.0}
    )

    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-10 12:00:00", old_notification_id),
    )
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-19 12:00:00", recent_notification_id),
    )
    await db.commit()

    counts = await odds_store.cleanup_retained_data(current_snapshot_at)
    notification_rows = await db.execute_fetchall(
        "SELECT id FROM notifications ORDER BY id"
    )

    assert [row["id"] for row in notification_rows] == [recent_notification_id]
    assert counts["deleted_notifications"] == 1


@pytest.mark.asyncio
async def test_cleanup_retained_data_uses_isolated_connection_for_transaction(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "persist_inapp_notifications", False)

    pause_delete = asyncio.Event()
    allow_delete = asyncio.Event()
    original_connect = odds_store.aiosqlite.connect

    class PausingConnection:
        def __init__(self, conn):
            self._conn = conn

        async def execute(self, sql, parameters=()):
            if sql.strip() == "DELETE FROM notifications":
                pause_delete.set()
                await allow_delete.wait()
            return await self._conn.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    async def fake_connect(*args, **kwargs):
        conn = await original_connect(*args, **kwargs)
        return PausingConnection(conn)

    monkeypatch.setattr(odds_store.aiosqlite, "connect", fake_connect)

    old_notification_id = await odds_store.insert_notification(
        "discrepancy", "Old Alert", "body", {"gap": 2.0}
    )
    db = await get_db()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE id = ?",
        ("2026-04-01 12:00:00", old_notification_id),
    )
    await db.commit()

    cleanup_task = asyncio.create_task(
        odds_store.cleanup_retained_data("2026-04-20T12:00:00")
    )
    await pause_delete.wait()

    insert_task = asyncio.create_task(
        odds_store.insert_notification("discrepancy", "Concurrent Alert", "body", {"gap": 2.5})
    )
    await asyncio.sleep(0)
    allow_delete.set()

    counts = await cleanup_task
    inserted_id = await insert_task
    notification_rows = await db.execute_fetchall(
        "SELECT id, title FROM notifications ORDER BY id"
    )

    assert inserted_id > 0
    assert [(row["id"], row["title"]) for row in notification_rows] == [
        (inserted_id, "Concurrent Alert")
    ]
    assert counts["deleted_notifications"] == 1


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


@pytest.mark.asyncio
async def test_insert_discrepancies_bulk_matches_per_row_path():
    """Bulk insert path produces the same DB rows as the per-row path.

    Regression for the analyzer-phase slowness that surfaced after the
    basketball handicap rollout: per-row commits made a single cycle hold
    the SQLite write transaction for 5–7 minutes (≈ ``insert_discrepancy``
    × 100k+).  ``insert_discrepancies_bulk`` collapses that to a single
    transaction.  This test verifies semantic equivalence.
    """
    from dataclasses import dataclass

    @dataclass
    class _Row:
        match_id: str
        market_type: str
        player_name: str | None
        bookmaker_a_id: str
        bookmaker_b_id: str
        threshold_a: float
        threshold_b: float
        odds_a: float | None
        odds_b: float | None
        gap: float
        profit_margin: float | None
        middle_profit_margin: float | None = None
        resolved_event_id: str | None = None
        bookmaker_a_match_id: str | None = None
        bookmaker_b_match_id: str | None = None

    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("mPB", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("mAB", "euroleague", "Atlas", "Bayern")
    # Per-bookmaker match-id columns FK to matches.id, so the alternate
    # IDs the bulk test uses below must exist.
    await odds_store.upsert_match("mPB-mozz", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("mPB-maxbet", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")

    rows = [
        _Row(
            match_id="mPB",
            market_type="player_points",
            player_name="Mike James",
            bookmaker_a_id="mozzart",
            bookmaker_b_id="meridian",
            threshold_a=18.5,
            threshold_b=20.5,
            odds_a=1.85,
            odds_b=1.90,
            gap=2.0,
            profit_margin=0.025,
            middle_profit_margin=0.85,
        ),
        _Row(
            match_id="mPB",
            market_type="home_handicap_ot",
            player_name=None,
            bookmaker_a_id="mozzart",
            bookmaker_b_id="maxbet",
            threshold_a=-3.5,
            threshold_b=-2.5,
            odds_a=1.95,
            odds_b=1.85,
            gap=1.0,
            profit_margin=0.012,
            middle_profit_margin=None,  # exercise the NULL path
            bookmaker_a_match_id="mPB-mozz",
            bookmaker_b_match_id="mPB-maxbet",
        ),
        _Row(
            match_id="mAB",
            market_type="game_total_ot",
            player_name=None,
            bookmaker_a_id="meridian",
            bookmaker_b_id="maxbet",
            threshold_a=205.5,
            threshold_b=205.5,
            odds_a=1.92,
            odds_b=1.92,
            gap=0.0,
            profit_margin=None,  # exercise the NULL profit path
            middle_profit_margin=0.40,
        ),
    ]

    inserted = await odds_store.insert_discrepancies_bulk(rows)
    assert inserted == 3

    discs = await odds_store.get_discrepancies()
    by_market = {(d.match_id, d.market_type, d.player_name): d for d in discs}
    assert len(discs) == 3

    pb_player = by_market[("mPB", "player_points", "Mike James")]
    assert pb_player.threshold_a == 18.5
    assert pb_player.threshold_b == 20.5
    assert pb_player.odds_a == 1.85
    assert pb_player.middle_profit_margin == 0.85
    # Default fallback: bookmaker_*_match_id should equal match_id when
    # the dataclass leaves them as None.
    assert pb_player.bookmaker_a_match_id == "mPB"
    assert pb_player.bookmaker_b_match_id == "mPB"

    pb_handicap = by_market[("mPB", "home_handicap_ot", None)]
    # Signed threshold preserved through the bulk path.
    assert pb_handicap.threshold_a == -3.5
    assert pb_handicap.threshold_b == -2.5
    # NULL middle_profit_margin should round-trip as None, not 0.0.
    assert pb_handicap.middle_profit_margin is None
    # Explicit per-bookmaker match-ids are preserved.
    assert pb_handicap.bookmaker_a_match_id == "mPB-mozz"
    assert pb_handicap.bookmaker_b_match_id == "mPB-maxbet"

    ab = by_market[("mAB", "game_total_ot", None)]
    assert ab.profit_margin is None
    assert ab.middle_profit_margin == 0.40
    assert ab.gap == 0.0


@pytest.mark.asyncio
async def test_insert_discrepancies_bulk_handles_empty_input():
    """Empty input is a no-op and must not start a transaction."""
    inserted = await odds_store.insert_discrepancies_bulk([])
    assert inserted == 0
    discs = await odds_store.get_discrepancies()
    assert discs == []


@pytest.mark.asyncio
async def test_insert_opportunities_bulk_matches_per_row_path():
    """Bulk-insert opportunities produce the same DB rows as per-row inserts."""
    from app.models.schemas import OpportunityLeg
    from app.services.opportunity_analyzer import Opportunity

    await odds_store.upsert_league("uefa", "UEFA", "football")
    await odds_store.upsert_match("f-m1", "uefa", "Bayern", "Real Madrid")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")

    opportunities = [
        Opportunity(
            sport="football",
            match_id="f-m1",
            opportunity_type="football_result_double_chance",
            market_type="football_double_chance",
            line=None,
            profit_margin=0.012,
            middle_profit_margin=None,
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    bookmaker_name="Mozzart",
                    market_type="football_result",
                    outcome_code="home",
                    odds=2.5,
                    line=None,
                    raw_label="1",
                ),
                OpportunityLeg(
                    bookmaker_id="maxbet",
                    bookmaker_name="MaxBet",
                    market_type="football_double_chance",
                    outcome_code="draw_or_away",
                    odds=1.42,
                    line=None,
                    raw_label="X2",
                ),
            ],
        ),
    ]

    inserted = await odds_store.insert_opportunities_bulk(
        opportunities, detected_at="2026-05-02T16:00:00+00:00"
    )
    assert inserted == 1

    opps = await odds_store.get_opportunities()
    assert len(opps) == 1
    assert opps[0].profit_margin == 0.012
    assert opps[0].middle_profit_margin is None
    assert opps[0].sport == "football"
    assert len(opps[0].legs) == 2
    assert opps[0].legs[0].outcome_code == "home"
    assert opps[0].legs[1].outcome_code == "draw_or_away"


@pytest.mark.asyncio
async def test_insert_opportunities_bulk_handles_empty_input():
    """Empty input is a no-op."""
    inserted = await odds_store.insert_opportunities_bulk(
        [], detected_at="2026-05-02T16:00:00+00:00"
    )
    assert inserted == 0


@pytest.mark.asyncio
async def test_replace_cycle_outputs_and_snapshot_rolls_back_atomically_on_failure():
    from dataclasses import dataclass

    from app.models.schemas import OpportunityLeg
    from app.services.opportunity_analyzer import Opportunity

    @dataclass
    class _DiscrepancyRow:
        match_id: str
        resolved_event_id: str | None
        market_type: str
        player_name: str | None
        bookmaker_a_id: str
        bookmaker_a_match_id: str | None
        bookmaker_b_id: str
        bookmaker_b_match_id: str | None
        threshold_a: float
        threshold_b: float
        odds_a: float | None
        odds_b: float | None
        gap: float
        profit_margin: float | None
        middle_profit_margin: float | None

    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("old", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("new", "euroleague", "Bayern", "Real Madrid")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Old Player",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="old-snapshot",
    )
    await odds_store.set_current_snapshot("old-snapshot")
    await odds_store.insert_discrepancy(
        "old",
        "player_points",
        "Old Player",
        "mozzart",
        "maxbet",
        10.5,
        12.5,
        1.8,
        1.9,
        2.0,
        0.02,
    )
    await odds_store.insert_opportunity(
        Opportunity(
            sport="basketball",
            match_id="old",
            opportunity_type="test",
            market_type="player_points",
            line=10.5,
            profit_margin=0.02,
            middle_profit_margin=None,
            legs=[
                OpportunityLeg(
                    bookmaker_id="mozzart",
                    market_type="player_points",
                    outcome_code="over",
                    odds=1.8,
                    line=10.5,
                )
            ],
        ),
        detected_at="old-snapshot",
    )

    with pytest.raises(Exception):
        await odds_store.replace_cycle_outputs_and_activate_snapshot(
            resolved_events=[
                ResolvedEventIn(
                    id="evt-new",
                    sport="basketball",
                    start_time="2030-01-01T20:00:00+00:00",
                    primary_match_id="new",
                    confidence=0.99,
                    method="exact",
                )
            ],
            resolved_event_members=[
                ResolvedEventMemberIn(
                    resolved_event_id="evt-new",
                    match_id="new",
                    bookmaker_id="maxbet",
                    confidence=0.99,
                )
            ],
            event_review_cases=[
                EventReviewCaseIn(
                    fingerprint="event-review-new",
                    sport="basketball",
                    start_time="2030-01-01T20:00:00+00:00",
                    primary_match_id="new",
                    candidate_resolved_event_id="evt-new",
                    candidate_match_ids=["new"],
                    reason_code="candidate_event_equivalence",
                    source_bookmaker_ids=["maxbet"],
                )
            ],
            odds=[
                NormalizedOdds(
                    match_id="old",
                    bookmaker_id="mozzart",
                    league_id="euroleague",
                    home_team="Partizan",
                    away_team="Crvena Zvezda",
                    market_type="player_points",
                    player_name="Old Player",
                    threshold=10.5,
                    over_odds=1.7,
                    under_odds=2.1,
                )
            ],
            outcome_offers=[],
            unresolved_odds=[],
            team_review_cases=[],
            auto_approved_team_reviews=[],
            opportunities=[
                Opportunity(
                    sport="basketball",
                    match_id="new",
                    opportunity_type="test",
                    market_type="player_points",
                    line=12.5,
                    profit_margin=0.03,
                    middle_profit_margin=None,
                    legs=[
                        OpportunityLeg(
                            bookmaker_id="maxbet",
                            market_type="player_points",
                            outcome_code="under",
                            odds=1.9,
                            line=12.5,
                        )
                    ],
                )
            ],
            discrepancies=[
                _DiscrepancyRow(
                    match_id="missing-match",
                    resolved_event_id=None,
                    market_type="player_points",
                    player_name="Broken",
                    bookmaker_a_id="mozzart",
                    bookmaker_a_match_id=None,
                    bookmaker_b_id="maxbet",
                    bookmaker_b_match_id=None,
                    threshold_a=10.5,
                    threshold_b=12.5,
                    odds_a=1.8,
                    odds_b=1.9,
                    gap=2.0,
                    profit_margin=0.03,
                    middle_profit_margin=None,
                )
            ],
            detected_at="new-snapshot",
            snapshot_at="new-snapshot",
        )

    status = await odds_store.get_system_status()
    discrepancies = await odds_store.get_discrepancies()
    opportunities = await odds_store.get_opportunities()

    assert status.last_scrape_at == "old-snapshot"
    assert [d.match_id for d in discrepancies] == ["old"]
    assert [o.match_id for o in opportunities] == ["old"]
    current = await odds_store.get_odds_for_match("old")
    assert len(current) == 1
    assert current[0].over_odds == 1.8
    assert await odds_store.get_resolved_event("evt-new") is None
    assert await odds_store.get_event_review_case_by_fingerprint("event-review-new") is None


@pytest.mark.asyncio
async def test_replace_cycle_outputs_hides_uncommitted_writer_changes_from_shared_reads(
    monkeypatch: pytest.MonkeyPatch,
):
    from dataclasses import dataclass

    @dataclass
    class _DiscrepancyRow:
        match_id: str
        resolved_event_id: str | None
        market_type: str
        player_name: str | None
        bookmaker_a_id: str
        bookmaker_a_match_id: str | None
        bookmaker_b_id: str
        bookmaker_b_match_id: str | None
        threshold_a: float
        threshold_b: float
        odds_a: float | None
        odds_b: float | None
        gap: float
        profit_margin: float | None
        middle_profit_margin: float | None

    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("old", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("new", "euroleague", "Bayern", "Real Madrid")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="old",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Old Player",
            threshold=10.5,
            over_odds=1.8,
            under_odds=2.0,
        ),
        scraped_at="old-snapshot",
    )
    await odds_store.set_current_snapshot("old-snapshot")
    await odds_store.insert_discrepancy(
        "old",
        "player_points",
        "Old Player",
        "mozzart",
        "maxbet",
        10.5,
        12.5,
        1.8,
        1.9,
        2.0,
        0.02,
    )

    original_set_current_snapshot_tx = odds_store._set_current_snapshot_tx
    writer_paused = asyncio.Event()
    allow_commit = asyncio.Event()

    async def pausing_set_current_snapshot_tx(db, snapshot_at):
        writer_paused.set()
        await allow_commit.wait()
        await original_set_current_snapshot_tx(db, snapshot_at)

    monkeypatch.setattr(
        odds_store,
        "_set_current_snapshot_tx",
        pausing_set_current_snapshot_tx,
    )

    writer_task = asyncio.create_task(
        odds_store.replace_cycle_outputs_and_activate_snapshot(
            resolved_events=[
                ResolvedEventIn(
                    id="evt-new",
                    sport="basketball",
                    start_time="2030-01-01T20:00:00+00:00",
                    primary_match_id="new",
                    confidence=0.99,
                    method="exact",
                )
            ],
            resolved_event_members=[
                ResolvedEventMemberIn(
                    resolved_event_id="evt-new",
                    match_id="new",
                    bookmaker_id="maxbet",
                    confidence=0.99,
                )
            ],
            event_review_cases=[],
            odds=[],
            outcome_offers=[],
            unresolved_odds=[],
            team_review_cases=[],
            auto_approved_team_reviews=[],
            opportunities=[],
            discrepancies=[
                _DiscrepancyRow(
                    match_id="new",
                    resolved_event_id="evt-new",
                    market_type="player_points",
                    player_name="New Player",
                    bookmaker_a_id="mozzart",
                    bookmaker_a_match_id=None,
                    bookmaker_b_id="maxbet",
                    bookmaker_b_match_id=None,
                    threshold_a=10.5,
                    threshold_b=12.5,
                    odds_a=1.8,
                    odds_b=1.9,
                    gap=2.0,
                    profit_margin=0.03,
                    middle_profit_margin=None,
                )
            ],
            detected_at="new-snapshot",
            snapshot_at="new-snapshot",
        )
    )
    await asyncio.wait_for(writer_paused.wait(), timeout=1)

    try:
        status_during = await odds_store.get_system_status()
        discrepancies_during = await odds_store.get_discrepancies()
        assert status_during.last_scrape_at == "old-snapshot"
        assert [d.match_id for d in discrepancies_during] == ["old"]
        assert await odds_store.get_resolved_event("evt-new") is None
    finally:
        allow_commit.set()

    await writer_task
    status_after = await odds_store.get_system_status()
    discrepancies_after = await odds_store.get_discrepancies()
    assert status_after.last_scrape_at == "new-snapshot"
    assert [d.match_id for d in discrepancies_after] == ["new"]
    assert await odds_store.get_resolved_event("evt-new") is not None


@pytest.mark.asyncio
async def test_replace_cycle_outputs_supports_default_memory_database():
    await close_db()
    await init_db()
    try:
        result = await odds_store.replace_cycle_outputs_and_activate_snapshot(
            resolved_events=[],
            resolved_event_members=[],
            event_review_cases=[],
            odds=[],
            outcome_offers=[],
            unresolved_odds=[],
            team_review_cases=[],
            auto_approved_team_reviews=[],
            opportunities=[],
            discrepancies=[],
            detected_at="memory-snapshot",
            snapshot_at="memory-snapshot",
        )
        status = await odds_store.get_system_status()

        assert result["discrepancies"] == 0
        assert status.last_scrape_at == "memory-snapshot"
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_default_memory_database_hides_uncommitted_writer_changes(
    monkeypatch: pytest.MonkeyPatch,
):
    await close_db()
    await init_db()
    try:
        await odds_store.upsert_bookmaker("mozzart", "Mozzart")
        await odds_store.upsert_bookmaker("maxbet", "MaxBet")
        await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
        await odds_store.upsert_match(
            "old",
            "euroleague",
            "Partizan",
            "Crvena Zvezda",
        )
        await odds_store.upsert_match("new", "euroleague", "Bayern", "Real Madrid")
        await odds_store.set_current_snapshot("old-snapshot")
        await odds_store.insert_discrepancy(
            "old",
            "player_points",
            "Old Player",
            "mozzart",
            "maxbet",
            10.5,
            12.5,
            1.8,
            1.9,
            2.0,
            0.02,
        )

        original_set_current_snapshot_tx = odds_store._set_current_snapshot_tx
        writer_paused = asyncio.Event()
        allow_commit = asyncio.Event()

        async def pausing_set_current_snapshot_tx(db, snapshot_at):
            writer_paused.set()
            await allow_commit.wait()
            await original_set_current_snapshot_tx(db, snapshot_at)

        monkeypatch.setattr(
            odds_store,
            "_set_current_snapshot_tx",
            pausing_set_current_snapshot_tx,
        )

        writer_task = asyncio.create_task(
            odds_store.replace_cycle_outputs_and_activate_snapshot(
                resolved_events=[],
                resolved_event_members=[],
                event_review_cases=[],
                odds=[],
                outcome_offers=[],
                unresolved_odds=[],
                team_review_cases=[],
                auto_approved_team_reviews=[],
                opportunities=[],
                discrepancies=[],
                detected_at="new-snapshot",
                snapshot_at="new-snapshot",
            )
        )
        await asyncio.wait_for(writer_paused.wait(), timeout=1)

        try:
            status_during = await odds_store.get_system_status()
            discrepancies_during = await odds_store.get_discrepancies()
            assert status_during.last_scrape_at == "old-snapshot"
            assert [d.match_id for d in discrepancies_during] == ["old"]
        finally:
            allow_commit.set()

        await writer_task
        status_after = await odds_store.get_system_status()
        discrepancies_after = await odds_store.get_discrepancies()
        assert status_after.last_scrape_at == "new-snapshot"
        assert discrepancies_after == []
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_upsert_odds_bulk_writes_current_history_and_sources():
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_odds(
        NormalizedOdds(
            match_id="m1",
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            source_url="https://example.com/original",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
        ),
        scraped_at="2026-05-02T15:00:00+00:00",
    )

    inserted = await odds_store.upsert_odds_bulk(
        [
            NormalizedOdds(
                match_id="m1",
                bookmaker_id="mozzart",
                league_id="euroleague",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                source_url=None,
                market_type="player_points",
                player_name="Iffe Lundberg",
                threshold=16.5,
                over_odds=1.90,
                under_odds=1.90,
            ),
            NormalizedOdds(
                match_id="m1",
                bookmaker_id="mozzart",
                league_id="euroleague",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                source_url="https://example.com/handicap",
                market_type="home_handicap_ot",
                threshold=-4.5,
                over_odds=1.85,
                under_odds=1.95,
            ),
        ],
        scraped_at="2026-05-02T16:00:00+00:00",
    )
    await odds_store.set_current_snapshot("2026-05-02T16:00:00+00:00")

    assert inserted == 2
    current = await odds_store.get_odds_for_match("m1")
    assert {(row.market_type, row.player_name, row.threshold) for row in current} == {
        ("player_points", "Iffe Lundberg", 16.5),
        ("home_handicap_ot", None, -4.5),
    }
    player_row = next(row for row in current if row.market_type == "player_points")
    assert player_row.over_odds == 1.90
    assert player_row.source_url == "https://example.com/handicap"

    db = await get_db()
    history_count = await db.execute_fetchall(
        "SELECT COUNT(*) AS count FROM odds_history WHERE match_id = ?",
        ("m1",),
    )
    assert history_count[0]["count"] == 3


@pytest.mark.asyncio
async def test_upsert_outcome_offers_bulk_preserves_nullable_line_uniqueness():
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_match("f1", "premier_league", "Arsenal", "Chelsea", sport="football")

    inserted = await odds_store.upsert_outcome_offers_bulk(
        [
            NormalizedOutcomeOffer(
                match_id="f1",
                bookmaker_id="mozzart",
                league_id="premier_league",
                sport="football",
                home_team="Arsenal",
                away_team="Chelsea",
                source_url="https://example.com/f1",
                market_type="football_result",
                outcome_code="home",
                odds=2.05,
                line=None,
                raw_label="1",
            ),
            NormalizedOutcomeOffer(
                match_id="f1",
                bookmaker_id="mozzart",
                league_id="premier_league",
                sport="football",
                home_team="Arsenal",
                away_team="Chelsea",
                source_url="https://example.com/f1",
                market_type="football_total",
                outcome_code="over",
                odds=1.95,
                line=2.5,
                raw_label="2.5+",
            ),
        ],
        scraped_at="2026-05-02T16:00:00+00:00",
    )
    await odds_store.set_current_snapshot("2026-05-02T16:00:00+00:00")

    assert inserted == 2
    offers = await odds_store.get_outcome_offers(match_id="f1")
    assert {(offer.market_type, offer.outcome_code, offer.line) for offer in offers} == {
        ("football_result", "home", None),
        ("football_total", "over", 2.5),
    }
    assert {offer.source_url for offer in offers} == {"https://example.com/f1"}


@pytest.mark.asyncio
async def test_diagnostic_bulk_inserts_match_per_row_visibility():
    await odds_store.upsert_bookmaker("admiralbet", "AdmiralBet")
    await odds_store.upsert_league("aba_liga", "ABA Liga", "basketball")
    scraped_at = "2026-05-02T16:00:00+00:00"

    unresolved_inserted = await odds_store.insert_unresolved_odds_bulk(
        [
            UnresolvedOddsDiagnostic(
                bookmaker_id="admiralbet",
                raw_league_id="AdmiralBet ABA Liga",
                league_id="aba_liga",
                market_type="player_points",
                player_name="P. Nikolic",
                raw_team_name="Borac Cacak",
                normalized_team_name="Borac Cacak",
                start_time="2026-05-02T16:00:00+00:00",
                threshold=10.5,
                over_odds=1.8,
                under_odds=2.0,
                reason_code="no_canonical_matchup_for_team_at_slot",
                candidate_count=0,
                available_matchups_same_slot=["Dubai vs Buducnost"],
            )
        ],
        scraped_at=scraped_at,
    )
    approved_ids = await odds_store.insert_team_review_cases_bulk(
        [
            TeamReviewDiagnostic(
                bookmaker_id="admiralbet",
                raw_league_id="aba_liga",
                normalized_raw_league_id="aba_liga",
                scope_league_id="aba_liga",
                raw_team_name="Borac",
                normalized_raw_team_name="borac",
                start_time="2026-05-02T16:00:00+00:00",
                reason_code="candidate_team_match_same_start_time",
                matched_counterpart_team="Dubai",
                canonical_home_team="Borac",
                canonical_away_team="Dubai",
            )
        ],
        scraped_at=scraped_at,
        mark_approved=True,
    )
    await odds_store.set_current_snapshot(scraped_at)

    assert unresolved_inserted == 1
    assert len(approved_ids) == 1
    unresolved = await odds_store.get_unresolved_odds()
    assert len(unresolved) == 1
    assert unresolved[0].available_matchups_same_slot == ["Dubai vs Buducnost"]
    reviews = await odds_store.get_team_review_cases(status="approved")
    assert len(reviews) == 1
    assert reviews[0].raw_team_name == "Borac"


@pytest.mark.asyncio
async def test_link_resolved_event_members_bulk_preserves_manual_member():
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_match("m1", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_match("m2", "euroleague", "Partizan", "Crvena Zvezda")
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt_manual",
            sport="basketball",
            start_time="2026-05-02T16:00:00+00:00",
            primary_match_id="m1",
            method="manual",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt_manual",
            match_id="m1",
            bookmaker_id="mozzart",
            source_url="https://example.com/manual",
        )
    )

    await odds_store.upsert_resolved_events_bulk(
        [
            ResolvedEventIn(
                id="evt_auto",
                sport="basketball",
                start_time="2026-05-02T16:00:00+00:00",
                primary_match_id="m2",
                method="exact",
            )
        ]
    )
    linked = await odds_store.link_resolved_event_members_bulk(
        [
            ResolvedEventMemberIn(
                resolved_event_id="evt_auto",
                match_id="m1",
                bookmaker_id="mozzart",
                source_url="https://example.com/auto",
            )
        ]
    )

    assert linked == 1
    member = await odds_store.get_resolved_event_member(match_id="m1", bookmaker_id="mozzart")
    assert member is not None
    assert member.resolved_event_id == "evt_manual"
    assert member.source_url == "https://example.com/manual"
