from __future__ import annotations

import pytest

from app.models.schemas import NormalizedOdds, NormalizedOutcomeOffer
from app.services.analyzer import find_threshold_gaps
from app.services import middle_ev
from app.services.canonical_analyzer import (
    analyze_canonical_offers,
    analyze_canonical_offers_with_benchmark,
)
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
    event_orientation: str | None = None,
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
    return canonical_offer_from_normalized_outcome_offer(
        offer,
        event_id=event_id,
        event_orientation=event_orientation,
    )


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
        _legacy_odds("meridian", "Lundberg", 19.5, over=1.80, under=2.00),
    ]
    legacy = find_threshold_gaps(legacy_odds)
    opportunities = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=1.85, under=1.95),
            *_odds("meridian", "Lundberg", 19.5, over=1.80, under=2.00),
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
        ("meridian", "under", 19.5),
    }


def test_canonical_line_middle_honors_min_gap():
    offers = [
        *_odds("mozzart", "Lundberg", 16.5),
        *_odds("meridian", "Lundberg", 19.5),
    ]

    assert analyze_canonical_offers(offers, min_gap=4.0) == []
    assert len(analyze_canonical_offers(offers, min_gap=0.5)) == 1


def test_canonical_line_middle_caps_candidates_per_event_player_market():
    offers = [
        *_odds("over-book", "Lundberg", 7.5, over=1.90, under=None),
    ]
    for index in range(12):
        offers.extend(
            _odds(
                f"under-book-{index}",
                "Lundberg",
                8.5 + index,
                over=None,
                under=1.90,
            )
        )

    opportunities = analyze_canonical_offers(
        offers,
        max_middle_opportunities_per_market=10,
    )

    assert len(opportunities) == 10
    assert {opportunity.opportunity_type for opportunity in opportunities} == {"middle"}
    assert all(opportunity.subject_name == "Lundberg" for opportunity in opportunities)


def test_canonical_line_middle_ranks_relative_width_before_raw_odds():
    opportunities = analyze_canonical_offers(
        [
            *_odds("over-book", "Lundberg", 7.5, over=1.90, under=None),
            *_odds("narrow-under", "Lundberg", 8.5, over=None, under=5.00),
            *_odds("wide-under", "Lundberg", 10.5, over=None, under=1.90),
        ],
        max_middle_opportunities_per_market=1,
    )

    assert len(opportunities) == 1
    assert {(leg.bookmaker_id, leg.line) for leg in opportunities[0].legs} == {
        ("over-book", 7.5),
        ("wide-under", 10.5),
    }


def test_canonical_line_middle_uses_odds_when_relative_width_ties():
    opportunities = analyze_canonical_offers(
        [
            *_odds("cheap-over", "Lundberg", 7.5, over=1.50, under=None),
            *_odds("cheap-under", "Lundberg", 10.5, over=None, under=1.50),
            *_odds("better-over", "Lundberg", 7.5, over=2.00, under=None),
            *_odds("better-under", "Lundberg", 10.5, over=None, under=2.00),
        ],
        max_middle_opportunities_per_market=1,
    )

    assert len(opportunities) == 1
    assert {(leg.bookmaker_id, leg.line) for leg in opportunities[0].legs} == {
        ("better-over", 7.5),
        ("better-under", 10.5),
    }


def test_canonical_line_middle_populates_market_implied_ev_fields():
    opportunities = analyze_canonical_offers(
        [
            *_odds("value-over", "Lundberg", 8.5, over=2.10, under=4.00),
            *_odds("fair-mid", "Lundberg", 12.5, over=2.00, under=2.00),
            *_odds("value-under", "Lundberg", 15.5, over=4.00, under=2.10),
        ],
        max_middle_opportunities_per_market=1,
    )

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.middle_hit_probability is not None
    assert opportunity.middle_ev is not None
    assert opportunity.middle_ev > 0
    assert opportunity.middle_model_confidence in {"low", "medium", "high"}
    assert opportunity.middle_model_diagnostics["mode"] == "fitted"
    assert opportunity.middle_ev_rank is not None


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
                19.5,
                over=1.80,
                under=1.95,
                subject_key="ply_lundberg",
                subject_name="N. Jokic",
            ),
        ]
    )

    assert len(opportunities) == 1
    assert opportunities[0].subject_type == "player"
    assert opportunities[0].subject_key == "ply_lundberg"
    assert opportunities[0].subject_name == "Nikola Jokić"
    assert {(leg.bookmaker_id, leg.outcome_code, leg.line) for leg in opportunities[0].legs} == {
        ("mozzart", "over", 16.5),
        ("meridian", "under", 19.5),
    }


def test_canonical_line_middle_does_not_cross_resolved_events_for_same_player():
    same_event = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=1.85, under=None, event_id="event-a"),
            *_odds("meridian", "Lundberg", 19.5, over=None, under=2.00, event_id="event-a"),
        ]
    )
    split_events = analyze_canonical_offers(
        [
            *_odds("mozzart", "Lundberg", 16.5, over=1.85, under=None, event_id="event-a"),
            *_odds("meridian", "Lundberg", 19.5, over=None, under=2.00, event_id="event-b"),
        ]
    )

    assert len(same_event) == 1
    assert split_events == []


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


def test_canonical_analysis_benchmark_counts_rule_paths_without_changing_output():
    offers = [
        *_odds(
            "same-line-a",
            "Lundberg",
            -4.5,
            over=2.05,
            under=1.85,
            market_type="home_handicap_ot",
        ),
        *_odds(
            "same-line-b",
            "Lundberg",
            -4.5,
            over=1.85,
            under=2.00,
            market_type="home_handicap_ot",
        ),
        *_odds(
            "middle-over",
            "Lundberg",
            16.5,
            over=1.90,
            under=None,
            market_type="player_points",
        ),
        *_odds(
            "middle-under",
            "Lundberg",
            19.5,
            over=None,
            under=1.95,
            market_type="player_points",
        ),
        _outcome_offer("result-book", "football_result", "home", 2.50),
        _outcome_offer(
            "double-chance-book",
            "football_double_chance",
            "draw_or_away",
            1.75,
        ),
    ]

    baseline = analyze_canonical_offers(offers)
    result = analyze_canonical_offers_with_benchmark(
        offers,
        canonical_offer_load_ms=12,
        primary_match_lookup_ms=3,
    )

    assert [
        (item.opportunity_type, item.market_type, item.line) for item in result.opportunities
    ] == [(item.opportunity_type, item.market_type, item.line) for item in baseline]

    benchmark = result.benchmark
    assert benchmark.canonical_offer_load_ms == 12
    assert benchmark.primary_match_lookup_ms == 3
    assert benchmark.loaded_offer_count == len(offers)
    assert benchmark.opportunity_count == len(result.opportunities)
    assert benchmark.same_market_group_count >= 3
    assert benchmark.line_market_group_count >= 2
    assert benchmark.event_market_family_group_count >= 2

    rules = {
        (row.rule, row.market_type): row
        for row in benchmark.rules
    }
    same_line = rules[("same_line_arbitrage", "home_handicap_ot")]
    assert same_line.group_count == 1
    assert same_line.offer_count == 4
    assert same_line.candidate_pair_count == 6
    assert same_line.publishable_candidate_count == 1
    assert same_line.opportunity_count == 1

    middle = rules[("line_middle", "player_points")]
    assert middle.group_count == 1
    assert middle.candidate_pair_count == 1
    assert middle.publishable_candidate_count == 1
    assert middle.opportunity_count == 1

    complementary = rules[
        ("complementary_outcomes", "football_result_double_chance")
    ]
    assert complementary.group_count == 1
    assert complementary.candidate_pair_count == 1
    assert complementary.publishable_candidate_count == 1
    assert complementary.opportunity_count == 1

    assert benchmark.candidate_pair_count == sum(
        row.candidate_pair_count for row in benchmark.rules
    )
    assert benchmark.publishable_candidate_count == sum(
        row.publishable_candidate_count for row in benchmark.rules
    )


def test_canonical_line_middle_counts_only_feasible_pairs_and_reuses_model(monkeypatch):
    original_consensus_points = middle_ev._consensus_points
    original_fit_normal = middle_ev._fit_normal
    calls = {"consensus": 0, "fit": 0}

    def counting_consensus_points(quotes):
        calls["consensus"] += 1
        return original_consensus_points(quotes)

    def counting_fit_normal(points):
        calls["fit"] += 1
        return original_fit_normal(points)

    monkeypatch.setattr(middle_ev, "_consensus_points", counting_consensus_points)
    monkeypatch.setattr(middle_ev, "_fit_normal", counting_fit_normal)

    offers = []
    for index, line in enumerate([8.5, 10.5, 12.5, 14.5]):
        offers.extend(
            _odds(
                f"book-{index}",
                "Lundberg",
                line,
                over=2.00,
                under=2.00,
            )
        )

    result = analyze_canonical_offers_with_benchmark(
        offers,
        max_middle_opportunities_per_market=100,
    )
    rules = {
        (row.rule, row.market_type): row
        for row in result.benchmark.rules
    }
    middle = rules[("line_middle", "player_points")]

    assert middle.candidate_pair_count == 6
    assert calls == {"consensus": 1, "fit": 1}


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


def test_canonical_result_double_chance_uses_event_orientation():
    overlapping = analyze_canonical_offers(
        [
            _outcome_offer(
                "balkanbet",
                "football_result",
                "away",
                13.0,
                event_id="evt-al-kholood-al-hilal",
                event_orientation="reversed",
            ),
            _outcome_offer(
                "superbet",
                "football_double_chance",
                "home_or_draw",
                4.40,
                event_id="evt-al-kholood-al-hilal",
            ),
        ]
    )
    complementary = analyze_canonical_offers(
        [
            _outcome_offer(
                "balkanbet",
                "football_result",
                "away",
                2.50,
                event_id="evt-al-kholood-al-hilal",
                event_orientation="reversed",
            ),
            _outcome_offer(
                "superbet",
                "football_double_chance",
                "draw_or_away",
                1.75,
                event_id="evt-al-kholood-al-hilal",
            ),
        ]
    )
    reversed_double_chance = analyze_canonical_offers(
        [
            _outcome_offer(
                "superbet",
                "football_result",
                "home",
                2.50,
                event_id="evt-al-kholood-al-hilal",
            ),
            _outcome_offer(
                "balkanbet",
                "football_double_chance",
                "home_or_draw",
                1.75,
                event_id="evt-al-kholood-al-hilal",
                event_orientation="reversed",
            ),
        ]
    )

    assert overlapping == []
    assert len(complementary) == 1
    assert {leg.outcome_code for leg in complementary[0].legs} == {
        "home",
        "draw_or_away",
    }
    assert len(reversed_double_chance) == 1
    assert {leg.outcome_code for leg in reversed_double_chance[0].legs} == {
        "home",
        "draw_or_away",
    }


def test_canonical_total_goals_middle_matches_legacy_floor():
    legacy = analyze_outcome_offers(
        [
            _legacy_outcome_offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _legacy_outcome_offer("maxbet", "football_total_goals", "under", 2.0, line=4.5),
        ]
    )
    opportunities = analyze_canonical_offers(
        [
            _outcome_offer("balkanbet", "football_total_goals", "over", 2.0, line=1.5),
            _outcome_offer("maxbet", "football_total_goals", "under", 2.0, line=4.5),
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
            _outcome_offer("maxbet", "football_total_goals", "under", 1.65, line=4.5),
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
                19.5,
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
