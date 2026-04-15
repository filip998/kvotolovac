from __future__ import annotations

import json
from pathlib import Path

from app.scrapers.maxbet_scraper import _parse_game_total_match
from app.scrapers.mozzart_scraper import _parse_game_total_items
from app.services.normalizer import normalize_league_id, normalize_odds, normalize_team_name

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
