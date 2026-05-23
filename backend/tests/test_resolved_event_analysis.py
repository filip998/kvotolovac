from __future__ import annotations

import pytest

from app.models.schemas import (
    NormalizedOdds,
    NormalizedOutcomeOffer,
    OpportunityLeg,
    ResolvedEventIn,
    ResolvedEventMemberIn,
    ResolvedEventMemberOut,
)
from app.services.analyzer import analyze
from app.services.canonical_analyzer import analyze_canonical_offers
from app.services.opportunity_analyzer import Opportunity, analyze_outcome_offers
from app.store import odds_store


START_TIME = "2030-01-01T20:00:00+00:00"


def _member(
    member_id: int,
    *,
    match_id: str,
    bookmaker_id: str,
    resolved_event_id: str = "evt-partizan-zvezda",
    status: str = "active",
    orientation: str = "as_listed",
) -> ResolvedEventMemberOut:
    return ResolvedEventMemberOut(
        id=member_id,
        resolved_event_id=resolved_event_id,
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        status=status,
        orientation=orientation,
    )


def _basketball_odds(
    *,
    match_id: str,
    bookmaker_id: str,
    player_name: str,
    threshold: float,
) -> NormalizedOdds:
    return NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id="euroleague",
        sport="basketball",
        home_team_id=1,
        away_team_id=2,
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type="player_points",
        player_name=player_name,
        threshold=threshold,
        over_odds=2.05,
        under_odds=2.05,
        start_time=START_TIME,
    )


def _football_offer(
    *,
    match_id: str,
    bookmaker_id: str,
    outcome_code: str,
    odds: float,
    market_type: str = "football_total_goals",
    line: float | None = 2.5,
    source_url: str | None = None,
) -> NormalizedOutcomeOffer:
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id="premier_league",
        sport="football",
        home_team_id=10,
        away_team_id=11,
        home_team="Arsenal",
        away_team="Chelsea",
        source_url=source_url,
        market_type=market_type,
        outcome_code=outcome_code,
        odds=odds,
        line=line,
        raw_label=outcome_code,
        start_time=START_TIME,
    )


def test_basketball_player_props_compare_by_resolved_event_and_player_key():
    odds = [
        _basketball_odds(
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            player_name="Nikola Jokić",
            threshold=12.5,
        ),
        _basketball_odds(
            match_id="match-meridian",
            bookmaker_id="meridian",
            player_name="N. Jokic",
            threshold=15.5,
        ),
    ]
    members = [
        _member(1, match_id="match-mozzart", bookmaker_id="mozzart"),
        _member(2, match_id="match-meridian", bookmaker_id="meridian"),
    ]

    discrepancies = analyze(
        odds,
        event_members=members,
        event_primary_match_ids={"evt-partizan-zvezda": "match-mozzart"},
    )

    assert len(discrepancies) == 1
    discrepancy = discrepancies[0]
    assert discrepancy.resolved_event_id == "evt-partizan-zvezda"
    assert discrepancy.match_id == "match-mozzart"
    assert discrepancy.player_name == "Nikola Jokić"
    assert discrepancy.bookmaker_a_id == "mozzart"
    assert discrepancy.bookmaker_b_id == "meridian"
    assert discrepancy.bookmaker_a_match_id == "match-mozzart"
    assert discrepancy.bookmaker_b_match_id == "match-meridian"
    assert discrepancy.gap == 3.0


def test_basketball_non_player_markets_use_resolved_event_identity():
    odds = [
        _basketball_odds(
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            player_name="",
            threshold=156.5,
        ).model_copy(update={"market_type": "game_total", "player_name": None}),
        _basketball_odds(
            match_id="match-meridian",
            bookmaker_id="meridian",
            player_name="",
            threshold=159.5,
        ).model_copy(update={"market_type": "game_total", "player_name": None}),
    ]
    members = [
        _member(1, match_id="match-mozzart", bookmaker_id="mozzart"),
        _member(2, match_id="match-meridian", bookmaker_id="meridian"),
    ]

    discrepancies = analyze(
        odds,
        event_members=members,
        event_primary_match_ids={"evt-partizan-zvezda": "match-mozzart"},
    )

    assert len(discrepancies) == 1
    assert discrepancies[0].resolved_event_id == "evt-partizan-zvezda"
    assert discrepancies[0].match_id == "match-mozzart"
    assert discrepancies[0].market_type == "game_total"
    assert discrepancies[0].player_name is None


def test_basketball_non_player_markets_ignore_inactive_event_members():
    odds = [
        _basketball_odds(
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            player_name="",
            threshold=156.5,
        ).model_copy(update={"market_type": "game_total", "player_name": None}),
        _basketball_odds(
            match_id="match-meridian",
            bookmaker_id="meridian",
            player_name="",
            threshold=159.5,
        ).model_copy(update={"market_type": "game_total", "player_name": None}),
    ]
    members = [
        _member(1, match_id="match-mozzart", bookmaker_id="mozzart"),
        _member(
            2,
            match_id="match-meridian",
            bookmaker_id="meridian",
            status="inactive",
        ),
    ]

    assert analyze(odds, event_members=members) == []


def test_basketball_separate_or_unresolved_events_do_not_cross_compare():
    odds = [
        _basketball_odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokić",
            threshold=12.5,
        ),
        _basketball_odds(
            match_id="match-b",
            bookmaker_id="book-b",
            player_name="N. Jokic",
            threshold=14.5,
        ),
    ]

    assert analyze(odds, event_members=[]) == []
    assert (
        analyze(
            odds,
            event_members=[
                _member(1, match_id="match-a", bookmaker_id="book-a"),
                _member(2, match_id="match-b", bookmaker_id="book-b", status="inactive"),
            ],
        )
        == []
    )
    assert (
        analyze(
            odds,
            event_members=[
                _member(
                    1,
                    match_id="match-a",
                    bookmaker_id="book-a",
                    resolved_event_id="evt-a",
                ),
                _member(
                    2,
                    match_id="match-b",
                    bookmaker_id="book-b",
                    resolved_event_id="evt-b",
                ),
            ],
        )
        == []
    )


def test_football_opportunities_group_by_resolved_event_id():
    offers = [
        _football_offer(
            match_id="match-maxbet",
            bookmaker_id="maxbet",
            outcome_code="under",
            odds=2.15,
        ),
        _football_offer(
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            outcome_code="over",
            odds=2.85,
        ),
    ]
    members = [
        _member(
            1,
            match_id="match-maxbet",
            bookmaker_id="maxbet",
            resolved_event_id="evt-arsenal-chelsea",
        ),
        _member(
            2,
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            resolved_event_id="evt-arsenal-chelsea",
        ),
    ]

    opportunities = analyze_outcome_offers(
        offers,
        event_members=members,
        event_primary_match_ids={"evt-arsenal-chelsea": "match-maxbet"},
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.resolved_event_id == "evt-arsenal-chelsea"
    assert opportunity.match_id == "match-maxbet"
    assert {leg.match_id for leg in opportunity.legs} == {
        "match-maxbet",
        "match-balkanbet",
    }


def test_football_result_complements_use_resolved_event_orientation():
    offers = [
        _football_offer(
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            market_type="football_result",
            outcome_code="away",
            odds=13.0,
            line=None,
        ),
        _football_offer(
            match_id="match-superbet",
            bookmaker_id="superbet",
            market_type="football_double_chance",
            outcome_code="home_or_draw",
            odds=4.40,
            line=None,
        ),
    ]
    members = [
        _member(
            1,
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            resolved_event_id="evt-al-kholood-al-hilal",
            orientation="reversed",
        ),
        _member(
            2,
            match_id="match-superbet",
            bookmaker_id="superbet",
            resolved_event_id="evt-al-kholood-al-hilal",
        ),
    ]

    opportunities = analyze_outcome_offers(
        offers,
        event_members=members,
        event_primary_match_ids={"evt-al-kholood-al-hilal": "match-superbet"},
    )

    assert opportunities == []


@pytest.mark.asyncio
async def test_store_helpers_exclude_non_eligible_event_methods_from_analysis():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("book-a", "Book A")
    await odds_store.upsert_bookmaker("book-b", "Book B")
    await odds_store.upsert_match(
        "match-a",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        "match-b",
        "euroleague",
        "KK Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time=START_TIME,
    )
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-medium-candidate",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-a",
            method="auto_candidate",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-medium-candidate",
            match_id="match-a",
            bookmaker_id="book-a",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-medium-candidate",
            match_id="match-b",
            bookmaker_id="book-b",
        )
    )
    odds = [
        _basketball_odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokić",
            threshold=12.5,
        ),
        _basketball_odds(
            match_id="match-b",
            bookmaker_id="book-b",
            player_name="N. Jokic",
            threshold=14.5,
        ),
    ]

    members = await odds_store.get_eligible_resolved_event_members_for_odds(odds)

    assert members == []
    assert analyze(odds, event_members=members) == []


@pytest.mark.asyncio
async def test_opportunity_storage_uses_leg_match_ids_for_source_urls():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match(
        "match-mozzart",
        "euroleague",
        "Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        "match-meridian",
        "euroleague",
        "KK Partizan",
        "Crvena Zvezda",
        sport="basketball",
        start_time=START_TIME,
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-mozzart",
        bookmaker_id="mozzart",
        source_url="https://mozzart.example/event",
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-meridian",
        bookmaker_id="meridian",
        source_url="https://meridian.example/event",
    )

    await odds_store.insert_opportunity(
        Opportunity(
            sport="basketball",
            match_id="match-mozzart",
            resolved_event_id=None,
            opportunity_type="middle",
            market_type="player_points",
            subject_type="player",
            subject_name="Nikola Jokić",
            line=None,
            profit_margin=0.025,
            middle_profit_margin=0.5,
            legs=[
                OpportunityLeg(
                    match_id="match-mozzart",
                    bookmaker_id="mozzart",
                    market_type="player_points",
                    outcome_code="over",
                    line=12.5,
                    odds=2.05,
                ),
                OpportunityLeg(
                    match_id="match-meridian",
                    bookmaker_id="meridian",
                    market_type="player_points",
                    outcome_code="under",
                    line=14.5,
                    odds=2.05,
                ),
            ],
        ),
        detected_at=START_TIME,
    )

    stored = await odds_store.get_opportunities()

    assert len(stored) == 1
    assert stored[0].match_id == "match-mozzart"
    assert stored[0].legs[0].match_id == "match-mozzart"
    assert stored[0].legs[1].match_id == "match-meridian"
    assert stored[0].legs[0].source_url == "https://mozzart.example/event"
    assert stored[0].legs[1].source_url == "https://meridian.example/event"


@pytest.mark.asyncio
async def test_opportunity_storage_keeps_representative_match_and_leg_sources():
    await odds_store.upsert_league("premier_league", "Premier League", "football")
    await odds_store.upsert_bookmaker("maxbet", "MaxBet")
    await odds_store.upsert_bookmaker("balkanbet", "BalkanBet")
    await odds_store.upsert_match(
        "match-maxbet",
        "premier_league",
        "Arsenal",
        "Chelsea",
        sport="football",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        "match-balkanbet",
        "premier_league",
        "Arsenal FC",
        "Chelsea",
        sport="football",
        start_time=START_TIME,
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-maxbet",
        bookmaker_id="maxbet",
        source_url="https://maxbet.example/event",
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-balkanbet",
        bookmaker_id="balkanbet",
        source_url="https://balkanbet.example/event",
    )
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-arsenal-chelsea",
            sport="football",
            start_time=START_TIME,
            primary_match_id="match-maxbet",
            method="manual_review",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-arsenal-chelsea",
            match_id="match-maxbet",
            bookmaker_id="maxbet",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-arsenal-chelsea",
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
        )
    )
    offers = [
        _football_offer(
            match_id="match-maxbet",
            bookmaker_id="maxbet",
            outcome_code="under",
            odds=2.15,
        ),
        _football_offer(
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            outcome_code="over",
            odds=2.85,
        ),
    ]
    members = await odds_store.get_eligible_resolved_event_members_for_outcome_offers(
        offers
    )
    primary_match_ids = await odds_store.get_resolved_event_primary_match_ids(
        [member.resolved_event_id for member in members]
    )
    opportunities = analyze_outcome_offers(
        offers,
        event_members=members,
        event_primary_match_ids=primary_match_ids,
    )

    await odds_store.insert_opportunity(
        opportunities[0],
        detected_at="2030-01-01T20:05:00+00:00",
    )

    stored = await odds_store.get_opportunities(sport="football")

    assert len(stored) == 1
    opportunity = stored[0]
    assert opportunity.match_id == "match-maxbet"
    assert opportunity.resolved_event_id == "evt-arsenal-chelsea"
    assert opportunity.home_team == "Arsenal"
    source_by_bookmaker = {leg.bookmaker_id: leg.source_url for leg in opportunity.legs}
    assert source_by_bookmaker == {
        "maxbet": "https://maxbet.example/event",
        "balkanbet": "https://balkanbet.example/event",
    }


@pytest.mark.asyncio
async def test_current_canonical_offers_map_reversed_outcome_members():
    await odds_store.upsert_league("saudi_cup", "Saudi Cup", "football")
    await odds_store.upsert_bookmaker("balkanbet", "BalkanBet")
    await odds_store.upsert_bookmaker("superbet", "Superbet")
    await odds_store.upsert_match(
        "match-superbet",
        "saudi_cup",
        "Al-Kholood",
        "Al Hilal",
        sport="football",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        "match-balkanbet",
        "saudi_cup",
        "Al Hilal SFC",
        "Al-Kholood",
        sport="football",
        start_time=START_TIME,
    )
    await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-al-kholood-al-hilal",
            sport="football",
            start_time=START_TIME,
            primary_match_id="match-superbet",
            method="auto_fuzzy_high",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-al-kholood-al-hilal",
            match_id="match-superbet",
            bookmaker_id="superbet",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id="evt-al-kholood-al-hilal",
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            orientation="reversed",
        )
    )
    await odds_store.upsert_outcome_offer(
        _football_offer(
            match_id="match-balkanbet",
            bookmaker_id="balkanbet",
            market_type="football_result",
            outcome_code="away",
            odds=13.0,
            line=None,
        ),
        scraped_at=START_TIME,
    )
    await odds_store.upsert_outcome_offer(
        _football_offer(
            match_id="match-superbet",
            bookmaker_id="superbet",
            market_type="football_double_chance",
            outcome_code="home_or_draw",
            odds=4.40,
            line=None,
        ),
        scraped_at=START_TIME,
    )

    offers = await odds_store.get_current_canonical_offers_for_matches(
        ["match-superbet", "match-balkanbet"],
        snapshot_id=START_TIME,
    )

    result_offer = next(
        offer for offer in offers if offer.market.source_market_type == "football_result"
    )
    assert result_offer.bookmaker_id == "balkanbet"
    assert result_offer.outcome_code == "home"
    assert analyze_canonical_offers(
        offers,
        event_primary_match_ids={"evt-al-kholood-al-hilal": "match-superbet"},
    ) == []
