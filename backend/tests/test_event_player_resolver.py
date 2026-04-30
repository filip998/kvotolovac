from __future__ import annotations

from app.models.schemas import NormalizedOdds, ResolvedEventMemberOut
from app.services.event_player_resolver import build_event_scoped_player_odds


def _odds(
    *,
    match_id: str,
    bookmaker_id: str,
    player_name: str,
    market_type: str = "player_points",
    threshold: float = 10.5,
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
        market_type=market_type,
        player_name=player_name,
        threshold=threshold,
        over_odds=1.85,
        under_odds=1.95,
        start_time="2030-01-01T20:00:00+00:00",
    )


def _member(
    member_id: int,
    *,
    match_id: str,
    bookmaker_id: str,
    resolved_event_id: str = "evt-partizan-zvezda",
    status: str = "active",
) -> ResolvedEventMemberOut:
    return ResolvedEventMemberOut(
        id=member_id,
        resolved_event_id=resolved_event_id,
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        status=status,
    )


def test_event_scoped_player_resolution_merges_equivalent_labels_across_match_ids():
    odds = [
        _odds(
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            player_name="Nikola Jokić",
            threshold=12.5,
        ),
        _odds(
            match_id="match-meridian",
            bookmaker_id="meridian",
            player_name="N. Jokic",
            threshold=13.5,
        ),
    ]
    members = [
        _member(1, match_id="match-mozzart", bookmaker_id="mozzart"),
        _member(2, match_id="match-meridian", bookmaker_id="meridian"),
    ]

    resolved = build_event_scoped_player_odds(odds, members)

    assert len(resolved) == 2
    assert {item.odds.match_id for item in resolved} == {"match-mozzart", "match-meridian"}
    assert len({item.event_scoped_player_key for item in resolved}) == 1
    assert {item.event_player_display_name for item in resolved} == {"Nikola Jokić"}
    assert {item.source_player_name_variants for item in resolved} == {
        ("Nikola Jokić", "N. Jokic")
    }
    assert {
        item.comparison_group_key
        for item in resolved
    } == {
        (
            "evt-partizan-zvezda",
            "player_points",
            resolved[0].event_scoped_player_key,
        )
    }


def test_event_scoped_player_resolution_keeps_ambiguous_initials_separate():
    odds = [
        _odds(match_id="match-a", bookmaker_id="book-a", player_name="J. Smith"),
        _odds(match_id="match-b", bookmaker_id="book-b", player_name="Jordan Smith"),
        _odds(match_id="match-c", bookmaker_id="book-c", player_name="Jalen Smith"),
    ]
    members = [
        _member(1, match_id="match-a", bookmaker_id="book-a"),
        _member(2, match_id="match-b", bookmaker_id="book-b"),
        _member(3, match_id="match-c", bookmaker_id="book-c"),
    ]

    resolved = build_event_scoped_player_odds(odds, members)

    assert len(resolved) == 3
    assert len({item.event_scoped_player_key for item in resolved}) == 3
    assert {item.event_player_display_name for item in resolved} == {
        "J. Smith",
        "Jordan Smith",
        "Jalen Smith",
    }
