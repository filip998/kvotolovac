from __future__ import annotations

import pytest

from app.models.schemas import NormalizedOdds, NormalizedOutcomeOffer
from app.services.analyzer import find_threshold_gaps
from app.services.canonical_analyzer import analyze_canonical_offers
from app.services.canonical_offers import (
    canonical_offer_from_normalized_outcome_offer,
    canonical_offers_from_normalized_odds,
)
from app.services.opportunity_analyzer import analyze_outcome_offers


def _odds(
    bookmaker: str,
    player: str | None,
    threshold: float,
    *,
    over: float | None = 1.85,
    under: float | None = 1.95,
    match_id: str = "basketball-match-1",
    market_type: str = "player_points",
    event_id: str | None = None,
    subject_key: str | None = None,
    subject_name: str | None = None,
) -> list:
    odds = NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker,
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type=market_type,
        player_name=player,
        threshold=threshold,
        over_odds=over,
        under_odds=under,
    )
    return canonical_offers_from_normalized_odds(
        odds,
        event_id=event_id,
        subject_key_override=subject_key,
        subject_name_override=subject_name,
    )


def _legacy_odds(
    bookmaker: str,
    player: str | None,
    threshold: float,
    *,
    over: float | None = 1.85,
    under: float | None = 1.95,
    match_id: str = "basketball-match-1",
    market_type: str = "player_points",
) -> NormalizedOdds:
    return NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker,
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        market_type=market_type,
        player_name=player,
        threshold=threshold,
        over_odds=over,
        under_odds=under,
    )


def _outcome_offer(
    bookmaker_id: str,
    market_type: str,
    outcome_code: str,
    odds: float,
    *,
    line: float | None = None,
    match_id: str = "football-match-1",
    sport: str = "football",
    event_id: str | None = None,
):
    offer = NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id=f"{sport}_league",
        sport=sport,
        home_team_id=1,
        away_team_id=2,
        home_team="Team Alpha",
        away_team="Team Beta",
        market_type=market_type,
        outcome_code=outcome_code,
        odds=odds,
        line=line,
        raw_label=outcome_code,
        start_time="2030-01-01T20:00:00+00:00",
    )
    return canonical_offer_from_normalized_outcome_offer(offer, event_id=event_id)


def _legacy_outcome_offer(
    bookmaker_id: str,
    market_type: str,
    outcome_code: str,
    odds: float,
    *,
    line: float | None = None,
    match_id: str = "football-match-1",
) -> NormalizedOutcomeOffer:
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id="football_league",
        sport="football",
        home_team_id=1,
        away_team_id=2,
        home_team="Team Alpha",
        away_team="Team Beta",
        market_type=market_type,
        outcome_code=outcome_code,
        odds=odds,
        line=line,
        raw_label=outcome_code,
        start_time="2030-01-01T20:00:00+00:00",
    )


def test_canonical_line_middle_matches_basketball_threshold_gap():
    legacy_odds = [
        _legacy_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
        _legacy_odds("meridian", "Lundberg", 18.5, over=1.80, under=2.00),
    ]
    legacy = find_threshold_gaps(legacy_odds)
    opportunities = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
            *_odds("meridian", "Lundberg", 18.5, over=1.80, under=2.00),
        ]
    )

    assert len(legacy) == 1
    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.opportunity_type == "middle"
    assert opportunity.market_type == legacy[0].market_type
    assert opportunity.line == legacy[0].threshold_a
    assert opportunity.profit_margin == legacy[0].profit_margin
    assert opportunity.middle_profit_margin == legacy[0].middle_profit_margin
    assert {(leg.bookmaker_id, leg.outcome_code, leg.line) for leg in opportunity.legs} == {
        ("mozzart", "over", 16.5),
        ("meridian", "under", 18.5),
    }


def test_canonical_line_middle_honors_min_gap():
    offers = [
        *_odds("mozzart", "Lundberg", 16.5),
        *_odds("meridian", "Lundberg", 17.5),
    ]

    assert analyze_canonical_offers(offers, min_gap=2.0) == []
    assert len(analyze_canonical_offers(offers, min_gap=0.5)) == 1


def test_canonical_line_middle_uses_subject_key_before_display_name():
    opportunities = analyze_canonical_offers(
        [
            *_odds(
                "mozzart",
                "Nikola Jokić",
                16.5,
                over=1.90,
                under=1.80,
                subject_key="ply_lundberg",
                subject_name="Nikola Jokić",
            ),
            *_odds(
                "meridian",
                "N. Jokic",
                18.5,
                over=1.80,
                under=1.95,
                subject_key="ply_lundberg",
                subject_name="N. Jokic",
            ),
        ]
    )

    assert len(opportunities) == 1
    assert {(leg.bookmaker_id, leg.outcome_code, leg.line) for leg in opportunities[0].legs} == {
        ("mozzart", "over", 16.5),
        ("meridian", "under", 18.5),
    }


def test_canonical_line_middle_rejects_invalid_odds():
    opportunities = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=0.0, under=None),
            *_odds("meridian", "Lundberg", 18.5, over=None, under=2.00),
        ]
    )

    assert opportunities == []


def test_canonical_same_line_arbitrage_rejects_invalid_odds():
    opportunities = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=0.0, under=None),
            *_odds("meridian", "Lundberg", 16.5, over=None, under=2.00),
        ]
    )

    assert opportunities == []


def test_canonical_complementary_outcomes_reject_invalid_odds():
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("maxbet", "football_result", "home", 0.0),
            _outcome_offer("balkanbet", "football_double_chance", "draw_or_away", 2.0),
        ]
    )

    assert opportunities == []


def test_canonical_player_points_milestones_compare_as_player_points():
    opportunities = analyze_canonical_offers(
        [
            *_odds(
                "oktagonbet",
                "Lundberg",
                9.5,
                over=1.90,
                under=None,
                market_type="player_points_milestones",
            ),
            *_odds("mozzart", "Lundberg", 12.5, over=1.80, under=1.90),
        ]
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.market_type == "player_points"
    assert {(leg.market_type, leg.outcome_code, leg.line) for leg in opportunity.legs} == {
        ("player_points_milestones", "over", 9.5),
        ("player_points", "under", 12.5),
    }


def test_canonical_same_line_arbitrage_matches_basketball_positive_margin():
    legacy = find_threshold_gaps(
        [
            _legacy_odds("a", "Lundberg", -4.5, over=2.05, under=1.85, market_type="home_handicap_ot"),
            _legacy_odds("b", "Lundberg", -4.5, over=1.85, under=2.00, market_type="home_handicap_ot"),
        ]
    )
    opportunities = analyze_canonical_offers(
        [
            *_odds("a", "Lundberg", -4.5, over=2.05, under=1.85, market_type="home_handicap_ot"),
            *_odds("b", "Lundberg", -4.5, over=1.85, under=2.00, market_type="home_handicap_ot"),
        ]
    )

    assert len(legacy) == 1
    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.opportunity_type == "same_line_arbitrage"
    assert opportunity.market_type == "home_handicap_ot"
    assert opportunity.line == -4.5
    assert opportunity.profit_margin == legacy[0].profit_margin
    assert opportunity.middle_profit_margin == legacy[0].middle_profit_margin
    assert {(leg.bookmaker_id, leg.outcome_code, leg.odds) for leg in opportunity.legs} == {
        ("a", "over", 2.05),
        ("b", "under", 2.00),
    }


def test_canonical_same_bookmaker_alternate_lines_are_ignored():
    assert (
        analyze_canonical_offers(
            [
                *_odds("mozzart", None, 216.5, market_type="game_total"),
                *_odds("mozzart", None, 217.5, market_type="game_total"),
            ]
        )
        == []
    )


def test_canonical_total_goals_same_line_arbitrage_matches_legacy():
    legacy = analyze_outcome_offers(
        [
            _legacy_outcome_offer("maxbet", "football_total_goals", "under", 2.15, line=2.5),
            _legacy_outcome_offer("balkanbet", "football_total_goals", "over", 2.85, line=2.5),
        ]
    )
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("maxbet", "football_total_goals", "under", 2.15, line=2.5),
            _outcome_offer("balkanbet", "football_total_goals", "over", 2.85, line=2.5),
        ]
    )

    assert len(legacy) == 1
    assert len(opportunities) == 1
    assert opportunities[0].opportunity_type == legacy[0].opportunity_type
    assert opportunities[0].market_type == legacy[0].market_type
    assert opportunities[0].line == legacy[0].line
    assert opportunities[0].profit_margin == legacy[0].profit_margin


def test_canonical_result_double_chance_complement_matches_legacy():
    legacy = analyze_outcome_offers(
        [
            _legacy_outcome_offer("maxbet", "football_result", "home", 2.5),
            _legacy_outcome_offer("balkanbet", "football_double_chance", "draw_or_away", 1.75),
        ]
    )
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("maxbet", "football_result", "home", 2.5),
            _outcome_offer("balkanbet", "football_double_chance", "draw_or_away", 1.75),
        ]
    )

    assert len(legacy) == 1
    assert len(opportunities) == 1
    assert opportunities[0].opportunity_type == legacy[0].opportunity_type
    assert opportunities[0].market_type == legacy[0].market_type
    assert opportunities[0].profit_margin == legacy[0].profit_margin
    assert {leg.market_type for leg in opportunities[0].legs} == {
        "football_result",
        "football_double_chance",
    }


def test_canonical_total_goals_middle_matches_legacy_floor():
    legacy = analyze_outcome_offers(
        [
            _legacy_outcome_offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _legacy_outcome_offer("maxbet", "football_total_goals", "under", 2.0, line=2.5),
        ]
    )
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _outcome_offer("maxbet", "football_total_goals", "under", 2.0, line=2.5),
        ]
    )

    assert len(legacy) == 1
    assert len(opportunities) == 1
    assert opportunities[0].opportunity_type == "middle"
    assert opportunities[0].profit_margin == legacy[0].profit_margin
    assert opportunities[0].middle_profit_margin == legacy[0].middle_profit_margin


def test_canonical_total_goals_middle_filters_large_outside_loss():
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("balkanbet", "football_total_goals", "over", 1.65, line=1.5),
            _outcome_offer("maxbet", "football_total_goals", "under", 1.65, line=2.5),
        ]
    )

    assert opportunities == []


def test_canonical_tennis_match_winner_arbitrage_needs_no_tennis_pipeline():
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer(
                "book-a",
                "tennis_match_winner",
                "home",
                2.10,
                sport="tennis",
            ),
            _outcome_offer(
                "book-b",
                "tennis_match_winner",
                "away",
                2.10,
                sport="tennis",
            ),
        ]
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.sport == "tennis"
    assert opportunity.market_type == "match_winner"
    assert opportunity.opportunity_type == "same_line_arbitrage"
    assert opportunity.profit_margin == pytest.approx(0.05, abs=1e-4)
    assert {leg.market_type for leg in opportunity.legs} == {"tennis_match_winner"}


def test_canonical_resolved_event_uses_primary_match_and_source_leg_matches():
    opportunities = analyze_canonical_offers(
        [
            *_odds(
                "mozzart",
                "Lundberg",
                16.5,
                match_id="match-mozzart",
                event_id="evt-partizan-zvezda",
            ),
            *_odds(
                "meridian",
                "Lundberg",
                18.5,
                match_id="match-meridian",
                event_id="evt-partizan-zvezda",
            ),
        ],
        event_primary_match_ids={"evt-partizan-zvezda": "match-mozzart"},
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.resolved_event_id == "evt-partizan-zvezda"
    assert opportunity.match_id == "match-mozzart"
    assert {leg.match_id for leg in opportunity.legs} == {
        "match-mozzart",
        "match-meridian",
    }
