from __future__ import annotations

import json
from pathlib import Path

from app.scrapers.maxbet_scraper import _parse_game_total_match
from app.scrapers.mozzart_scraper import _parse_game_total_items
from app.models.schemas import RawOddsData
from app.services.normalizer import (
    generate_match_id,
    normalize_league_id,
    normalize_odds,
    normalize_odds_with_diagnostics,
    normalize_odds_with_issues,
    normalize_team_name,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MAXBET_TOTALS_FIXTURE_PATH = FIXTURES_DIR / "maxbet_basketball_totals.json"
MOZZART_MATCHES_FIXTURE_PATH = FIXTURES_DIR / "mozzart_matches.json"


def _load_fixture(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def test_normalize_league_id_keeps_competition_ids_stable():
    assert normalize_league_id("argentina_1") == "argentina_1"
    assert normalize_league_id("portoriko_1") == "portoriko_1"


def test_normalize_team_name_scopes_issue31_aliases():
    assert normalize_team_name("Obras", "argentina_1") == "Obras Sanitarias"
    assert normalize_team_name("Instituto", "argentina_1") == "Instituto de Cordoba"
    assert normalize_team_name("Capitanes de A.", "portoriko_1") == "Capitanes de Arecibo"

    assert normalize_team_name("Obras") == "Obras"
    assert normalize_team_name("Instituto") == "Instituto"
    assert normalize_team_name("Capitanes de A.") == "Capitanes de A."


def test_normalize_odds_collapses_obras_instituto_fixture_variants():
    mozzart_fixture = _load_fixture(MOZZART_MATCHES_FIXTURE_PATH)
    maxbet_fixture = _load_fixture(MAXBET_TOTALS_FIXTURE_PATH)

    mozzart_offer = next(
        offer
        for offer in _parse_game_total_items(mozzart_fixture["items"])
        if offer.home_team == "Obras" and offer.away_team == "Instituto"
    )
    maxbet_offer = next(
        offer
        for offer in _parse_game_total_match(maxbet_fixture["esMatches"][0])
        if offer.home_team == "Obras"
        and offer.away_team == "Inst.de Cordoba"
        and offer.threshold == 156.5
    )

    normalized = normalize_odds([mozzart_offer, maxbet_offer])

    assert len(normalized) == 2
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.league_id for offer in normalized} == {"argentina_1"}
    assert {offer.home_team for offer in normalized} == {"Obras Sanitarias"}
    assert {offer.away_team for offer in normalized} == {"Instituto de Cordoba"}


def test_generate_match_id_distinguishes_exact_start_times():
    first_tip = generate_match_id(
        "Rilski Sportist",
        "Levski Sofia",
        "2026-04-16T17:00:00+00:00",
    )
    rematch_tip = generate_match_id(
        "Rilski Sportist",
        "Levski Sofia",
        "2026-04-18T17:00:00+00:00",
    )

    assert first_tip != rematch_tip


def test_normalize_odds_keeps_missing_start_time_events_scoped_by_league():
    normalized = normalize_odds(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="nba",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                market_type="game_total",
                threshold=161.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time=None,
            ),
            RawOddsData(
                bookmaker_id="maxbet",
                league_id="aba_liga",
                home_team="Partizan",
                away_team="Crvena Zvezda",
                market_type="game_total",
                threshold=162.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time=None,
            ),
        ]
    )

    assert len(normalized) == 2
    assert len({offer.match_id for offer in normalized}) == 2
    assert {offer.league_id for offer in normalized} == {"nba", "aba_liga"}


def test_normalize_odds_infers_multiple_shared_platform_games_at_same_tipoff():
    normalized, unresolved = normalize_odds_with_issues(
        [
            RawOddsData(
                bookmaker_id="maxbet",
                league_id="nba",
                home_team="Houston",
                away_team="Kevin Durant",
                market_type="player_points",
                player_name="Kevin Durant",
                threshold=23.5,
                over_odds=1.58,
                under_odds=2.2,
                start_time="2026-04-11T01:30:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="oktagonbet",
                league_id="nba",
                home_team="Minnesota",
                away_team="Anthony Edwards",
                market_type="player_points",
                player_name="Anthony Edwards",
                threshold=26.5,
                over_odds=1.7,
                under_odds=2.0,
                start_time="2026-04-11T01:30:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id="poljska 1",
                home_team="Ostrow Wielkopolski",
                away_team="Daniel Laster",
                market_type="player_points",
                player_name="Daniel Laster",
                threshold=11.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T01:30:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="maxbet",
                league_id="poland",
                home_team="Zielona Gora",
                away_team="Ty Nichols",
                market_type="player_points",
                player_name="Ty Nichols",
                threshold=14.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T01:30:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len(normalized) == 4
    assert len({offer.match_id for offer in normalized}) == 2
    assert {
        (offer.home_team, offer.away_team)
        for offer in normalized
    } == {
        ("Houston Rockets", "Minnesota Timberwolves"),
        ("Ostrow Wielkopolski", "Zielona Gora"),
    }


def test_normalize_odds_infers_shared_platform_game_beside_other_tipoff_match():
    normalized, unresolved = normalize_odds_with_issues(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="nba",
                home_team="Houston Rockets",
                away_team="Minnesota Timberwolves",
                market_type="game_total",
                threshold=224.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T01:30:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id="poljska 1",
                home_team="Ostrow Wielkopolski",
                away_team="Daniel Laster",
                market_type="player_points",
                player_name="Daniel Laster",
                threshold=11.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-11T01:30:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="maxbet",
                league_id="poland",
                home_team="Zielona Gora",
                away_team="Ty Nichols",
                market_type="player_points",
                player_name="Ty Nichols",
                threshold=14.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-11T01:30:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len(normalized) == 3
    assert {
        (offer.home_team, offer.away_team)
        for offer in normalized
    } == {
        ("Houston Rockets", "Minnesota Timberwolves"),
        ("Ostrow Wielkopolski", "Zielona Gora"),
    }


def test_normalize_odds_infers_league_from_event_context(league_registry_file):
    normalized, unresolved, reviews, team_reviews = normalize_odds_with_diagnostics(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="Bulgarian NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=161.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time="2026-04-16T17:00:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="meridian",
                league_id="NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=162.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-16T17:00:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.league_id for offer in normalized} == {"bulgaria_nbl"}
    assert len(reviews) == 1
    assert reviews[0].bookmaker_id == "meridian"
    assert reviews[0].raw_league_id == "NBL"
    assert reviews[0].suggested_league_id == "bulgaria_nbl"
    assert reviews[0].reason_code == "league_inferred_from_event_context"
    assert team_reviews == []


def test_normalize_odds_creates_team_review_candidates_for_same_tipoff(team_registry_file):
    normalized, unresolved, reviews, team_reviews = normalize_odds_with_diagnostics(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="Bulgarian NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=161.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time="2026-04-16T17:00:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="meridian",
                league_id="NBL",
                home_team="Rilski Sport.",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=162.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-16T17:00:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len(normalized) == 2
    assert reviews == []
    assert len(team_reviews) == 1
    assert team_reviews[0].raw_team_name == "Rilski Sport."
    assert team_reviews[0].suggested_team_name == "Rilski Sportist"
    assert team_reviews[0].scope_league_id == "bulgaria_nbl"
    assert team_reviews[0].reason_code == "candidate_team_match_same_start_time"


def test_team_review_candidates_require_same_event_context(team_registry_file):
    normalized, unresolved, reviews, team_reviews = normalize_odds_with_diagnostics(
        [
            RawOddsData(
                bookmaker_id="mozzart",
                league_id="Bulgarian NBL",
                home_team="Rilski Sportist",
                away_team="Levski Sofia",
                market_type="game_total",
                threshold=161.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time="2026-04-16T17:00:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="meridian",
                league_id="Italy Lega A",
                home_team="Rilski Sport.",
                away_team="Virtus Bologna",
                market_type="game_total",
                threshold=162.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-16T17:00:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len(normalized) == 2
    assert reviews == []
    assert team_reviews == []
