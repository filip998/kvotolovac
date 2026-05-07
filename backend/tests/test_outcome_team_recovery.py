from __future__ import annotations

from app.scrapers.outcome_team_recovery import (
    recover_matchup_from_payload,
    split_matchup_text,
)


def test_recover_matchup_from_payload_preserves_primary_teams():
    matchup = recover_matchup_from_payload(
        {
            "home": "Liverpool",
            "away": "Chelsea",
            "name": "Liverpool - Arsenal",
        }
    )

    assert matchup.home_team == "Liverpool"
    assert matchup.away_team == "Chelsea"
    assert not matchup.recovered


def test_recover_matchup_from_payload_fills_missing_away_from_same_payload_name():
    matchup = recover_matchup_from_payload(
        {
            "home": "Liverpool",
            "away": "",
            "name": "Liverpool - Chelsea",
        }
    )

    assert matchup.home_team == "Liverpool"
    assert matchup.away_team == "Chelsea"
    assert matchup.source == "name"


def test_recover_matchup_from_payload_refuses_conflicting_known_side():
    matchup = recover_matchup_from_payload(
        {
            "home": "Liverpool",
            "away": "",
            "name": "Arsenal - Chelsea",
        }
    )

    assert matchup.home_team == "Liverpool"
    assert matchup.away_team == ""
    assert not matchup.recovered


def test_recover_matchup_from_payload_uses_two_ordered_participants():
    matchup = recover_matchup_from_payload(
        {
            "participants": [
                {"name": "Aston Villa"},
                {"name": "Nottingham Forest"},
            ]
        }
    )

    assert matchup.home_team == "Aston Villa"
    assert matchup.away_team == "Nottingham Forest"
    assert matchup.source == "participants"


def test_recover_matchup_from_payload_refuses_multi_runner_payload():
    matchup = recover_matchup_from_payload(
        {
            "teams": [
                {"name": "Team A"},
                {"name": "Team B"},
                {"name": "Team C"},
            ]
        }
    )

    assert matchup.home_team == ""
    assert matchup.away_team == ""
    assert not matchup.recovered


def test_split_matchup_text_supports_only_two_sided_match_labels():
    assert split_matchup_text("Home vs Away") == ("Home", "Away")
    assert split_matchup_text("Outright Winner") is None
