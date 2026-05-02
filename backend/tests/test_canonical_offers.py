from __future__ import annotations

from app.models.schemas import NormalizedOdds, NormalizedOutcomeOffer
from app.services.canonical_offers import (
    canonical_offer_from_normalized_outcome_offer,
    canonical_offers_from_normalized_odds,
)


def _odds(
    bookmaker_id: str = "mozzart",
    *,
    match_id: str = "basketball-match-1",
    market_type: str = "player_points",
    player_name: str | None = "Nikola Jovic",
    threshold: float = 16.5,
    over_odds: float | None = 1.9,
    under_odds: float | None = 1.9,
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
        source_url=f"https://example.com/{bookmaker_id}/{match_id}",
        market_type=market_type,
        player_name=player_name,
        threshold=threshold,
        over_odds=over_odds,
        under_odds=under_odds,
        start_time="2030-01-01T20:00:00+00:00",
    )


def _outcome_offer(
    bookmaker_id: str = "maxbet",
    *,
    match_id: str = "football-match-1",
    market_type: str = "football_total_goals",
    outcome_code: str = "over",
    odds: float = 1.85,
    line: float | None = 2.5,
    sport: str = "football",
) -> NormalizedOutcomeOffer:
    return NormalizedOutcomeOffer(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id=f"{sport}_league",
        sport=sport,
        home_team_id=1,
        away_team_id=2,
        home_team="Team Alpha",
        away_team="Team Beta",
        source_url=f"https://example.com/{bookmaker_id}/{match_id}",
        market_type=market_type,
        outcome_code=outcome_code,
        odds=odds,
        line=line,
        raw_label=outcome_code,
        start_time="2030-01-01T20:00:00+00:00",
    )


def test_normalized_odds_player_prop_becomes_two_player_subject_offers():
    offers = canonical_offers_from_normalized_odds(
        _odds(over_odds=1.91, under_odds=1.87),
        scraped_at="2030-01-01T19:55:00+00:00",
    )

    assert [offer.outcome_code for offer in offers] == ["over", "under"]
    assert {offer.odds for offer in offers} == {1.91, 1.87}
    assert len({offer.market_key for offer in offers}) == 1
    market = offers[0].market
    assert market.market_type == "player_points"
    assert market.source_market_type == "player_points"
    assert market.subject_type == "player"
    assert market.subject_key == "player:nikola jovic"
    assert market.subject_name == "Nikola Jovic"
    assert market.line == 16.5
    assert offers[0].scraped_at == "2030-01-01T19:55:00+00:00"


def test_normalized_odds_market_key_excludes_bookmaker_and_odds():
    first = canonical_offers_from_normalized_odds(
        _odds("mozzart", over_odds=1.91, under_odds=None)
    )[0]
    second = canonical_offers_from_normalized_odds(
        _odds("maxbet", over_odds=2.05, under_odds=None)
    )[0]

    assert first.market_key == second.market_key
    assert first.bookmaker_id != second.bookmaker_id
    assert first.odds != second.odds


def test_normalized_odds_emits_only_available_sides():
    under_only = canonical_offers_from_normalized_odds(
        _odds(over_odds=None, under_odds=1.88)
    )
    empty = canonical_offers_from_normalized_odds(
        _odds(over_odds=None, under_odds=None)
    )

    assert [(offer.outcome_code, offer.odds) for offer in under_only] == [("under", 1.88)]
    assert empty == []


def test_basketball_game_total_remains_distinct_event_market():
    offers = canonical_offers_from_normalized_odds(
        _odds(
            market_type="game_total_ot",
            player_name=None,
            threshold=168.5,
            over_odds=1.95,
            under_odds=1.85,
        )
    )

    assert {offer.outcome_code for offer in offers} == {"over", "under"}
    assert offers[0].market.market_type == "game_total_ot"
    assert offers[0].market.subject_type == "event"
    assert offers[0].market.line == 168.5


def test_football_total_goals_outcome_offer_maps_directly():
    canonical = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(market_type="football_total_goals", outcome_code="over", line=2.5)
    )

    assert canonical.outcome_code == "over"
    assert canonical.odds == 1.85
    assert canonical.raw_label == "over"
    assert canonical.market.market_type == "football_total_goals"
    assert canonical.market.source_market_type == "football_total_goals"
    assert canonical.market.subject_type == "event"
    assert canonical.market.line == 2.5


def test_football_result_and_double_chance_use_conservative_market_names():
    result = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(market_type="football_result", outcome_code="home", line=None)
    )
    double_chance = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(
            market_type="football_double_chance",
            outcome_code="draw_or_away",
            line=None,
        )
    )

    assert result.market.market_type == "result"
    assert result.market.source_market_type == "football_result"
    assert double_chance.market.market_type == "double_chance"
    assert double_chance.market.source_market_type == "football_double_chance"
    assert result.market_key != double_chance.market_key


def test_synthetic_tennis_match_winner_maps_to_event_level_match_winner():
    canonical = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(
            market_type="tennis_match_winner",
            outcome_code="home",
            line=None,
            sport="tennis",
        )
    )

    assert canonical.market.market_type == "match_winner"
    assert canonical.market.source_market_type == "tennis_match_winner"
    assert canonical.market.subject_type == "event"
    assert canonical.market.line is None


def test_market_key_uses_event_identity_when_available():
    match_scoped = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(match_id="bookmaker-match-a")
    )
    event_scoped = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(match_id="bookmaker-match-b"),
        event_id="resolved-event-1",
    )
    same_event_scoped = canonical_offer_from_normalized_outcome_offer(
        _outcome_offer(match_id="bookmaker-match-c"),
        event_id="resolved-event-1",
    )

    assert match_scoped.market_key != event_scoped.market_key
    assert event_scoped.market_key == same_event_scoped.market_key
