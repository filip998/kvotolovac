import pytest

from app.services.market_allowlist import (
    DEFAULT_ANALYSIS_MARKETS,
    LEGACY_PLAYER_PROPS_ANALYSIS_MARKETS,
    MarketAllowlistError,
    analysis_market_allowlist,
    legacy_analysis_markets_for_scope,
    normalize_analysis_markets,
)


def test_normalize_analysis_markets_defaults_to_all():
    assert normalize_analysis_markets("") == DEFAULT_ANALYSIS_MARKETS
    assert normalize_analysis_markets(["all"]) == DEFAULT_ANALYSIS_MARKETS


def test_normalize_analysis_markets_accepts_exact_and_wildcard_filters():
    assert normalize_analysis_markets(
        "basketball:player_*;basketball:home_handicap_ot"
    ) == ("basketball:player_*", "basketball:home_handicap_ot")


def test_normalize_analysis_markets_rejects_all_with_specific_filters():
    with pytest.raises(MarketAllowlistError):
        normalize_analysis_markets(["all", "basketball:player_*"])


def test_normalize_analysis_markets_rejects_malformed_filters():
    with pytest.raises(MarketAllowlistError):
        normalize_analysis_markets(["basketball"])


def test_legacy_player_props_maps_to_wildcard_player_markets():
    assert (
        legacy_analysis_markets_for_scope("player_props")
        == LEGACY_PLAYER_PROPS_ANALYSIS_MARKETS
    )
    assert normalize_analysis_markets(
        None,
        legacy_scrape_market_scope="player_props",
    ) == LEGACY_PLAYER_PROPS_ANALYSIS_MARKETS


def test_market_allowlist_matches_canonical_market_names():
    allowlist = analysis_market_allowlist(
        ["basketball:player_*", "football:football_total_goals"]
    )

    assert allowlist.allows(sport="basketball", market_type="player_points")
    assert allowlist.allows(sport="football", market_type="football_total_goals")
    assert not allowlist.allows(sport="basketball", market_type="game_total")
    assert not allowlist.allows(sport="football", market_type="football_result")


def test_market_allowlist_outcome_lane_gating_is_conservative_for_unknown_sports():
    assert not analysis_market_allowlist(
        ["basketball:player_*"]
    ).may_include_outcome_offer_markets("football")
    assert analysis_market_allowlist(
        ["football:football_total_goals"]
    ).may_include_outcome_offer_markets("football")
    assert analysis_market_allowlist(
        ["tennis:*"]
    ).may_include_outcome_offer_markets("tennis")
