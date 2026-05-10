from app.services.tennis_name_matcher import (
    match_tennis_player_names,
    tennis_competitor_pair_matches,
)


def test_tennis_player_matcher_handles_comma_initial_full_and_diacritics():
    assert match_tennis_player_names("Cocciaretto, Elisabetta", "E. Cocciaretto")
    assert match_tennis_player_names("Swiatek, Iga", "Iga Swiatek")
    assert match_tennis_player_names("Swiatek, Iga", "I. Swiatek")
    assert match_tennis_player_names("Świątek, Iga", "Iga Swiatek")


def test_tennis_player_matcher_handles_prefix_abbreviations():
    assert match_tennis_player_names("Ka. Pliskova", "Karolina Pliskova")
    assert match_tennis_player_names("Ti.Pereira", "Tiago Pereira")


def test_tennis_competitor_pair_matcher_detects_reversed_orientation():
    matches = tennis_competitor_pair_matches(
        "Cocciaretto, Elisabetta",
        "Swiatek, Iga",
        "Iga Swiatek",
        "Elisabetta Cocciaretto",
    )

    assert matches
    assert matches[0].orientation == "reversed"


def test_tennis_player_matcher_rejects_doubles_like_surfaces():
    assert match_tennis_player_names("Cocciaretto / Errani", "E. Cocciaretto") is None
    assert match_tennis_player_names("Cocciaretto & Errani", "E. Cocciaretto") is None
