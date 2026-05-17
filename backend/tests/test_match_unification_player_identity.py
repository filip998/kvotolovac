from __future__ import annotations

from app.models.schemas import NormalizedOdds, ResolvedEventMemberOut
from app.services.match_unification.player_identity import (
    ActiveEventMembership,
    resolve_event_players,
)


def _odds(
    *,
    match_id: str,
    bookmaker_id: str,
    player_name: str | None,
    market_type: str = "player_points",
    sport: str = "basketball",
    threshold: float = 10.5,
) -> NormalizedOdds:
    return NormalizedOdds(
        match_id=match_id,
        bookmaker_id=bookmaker_id,
        league_id="euroleague",
        sport=sport,
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


def test_event_player_resolution_merges_equivalent_labels_across_match_ids():
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
    membership = ActiveEventMembership.from_members(
        [
            _member(1, match_id="match-mozzart", bookmaker_id="mozzart"),
            _member(2, match_id="match-meridian", bookmaker_id="meridian"),
        ]
    )

    resolution = resolve_event_players(odds, membership)
    resolved = list(resolution.scoped_odds)

    assert len(resolved) == 2
    assert resolution.skipped == ()
    assert {id(item.odds) for item in resolved} == {id(item) for item in odds}
    assert {item.odds.match_id for item in resolved} == {
        "match-mozzart",
        "match-meridian",
    }
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
    identity_rows = [
        (identity.display_name, identity.source_variants)
        for identity in resolution.identities
    ]
    assert identity_rows == [
        ("Nikola Jokić", ("Nikola Jokić", "N. Jokic"))
    ]


def test_event_player_resolution_keeps_ambiguous_initials_separate():
    odds = [
        _odds(match_id="match-a", bookmaker_id="book-a", player_name="J. Smith"),
        _odds(match_id="match-b", bookmaker_id="book-b", player_name="Jordan Smith"),
        _odds(match_id="match-c", bookmaker_id="book-c", player_name="Jalen Smith"),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(1, match_id="match-a", bookmaker_id="book-a"),
            _member(2, match_id="match-b", bookmaker_id="book-b"),
            _member(3, match_id="match-c", bookmaker_id="book-c"),
        ]
    )

    resolution = resolve_event_players(odds, membership)
    resolved = resolution.scoped_odds

    assert len(resolved) == 3
    assert len({item.event_scoped_player_key for item in resolved}) == 3
    assert {item.event_player_display_name for item in resolved} == {
        "J. Smith",
        "Jordan Smith",
        "Jalen Smith",
    }


def test_event_player_resolution_reports_skipped_odds_reasons():
    odds = [
        _odds(match_id="missing", bookmaker_id="mozzart", player_name="Nikola Jokić"),
        _odds(match_id="inactive", bookmaker_id="maxbet", player_name="N. Jokic"),
        _odds(
            match_id="non-player",
            bookmaker_id="meridian",
            player_name=None,
            market_type="game_total",
        ),
        _odds(
            match_id="football-player",
            bookmaker_id="soccerbet",
            player_name="Player Name",
            sport="football",
        ),
        _odds(match_id="empty-player", bookmaker_id="book-a", player_name=" "),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                1,
                match_id="inactive",
                bookmaker_id="maxbet",
                status="inactive",
            )
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert resolution.scoped_odds == ()
    assert resolution.skipped_counts == {
        "missing_resolved_event_member": 2,
        "non_player_market": 1,
        "unsupported_sport": 1,
        "empty_player_name": 1,
    }
    assert [skipped.reason for skipped in resolution.skipped] == [
        "missing_resolved_event_member",
        "missing_resolved_event_member",
        "non_player_market",
        "unsupported_sport",
        "empty_player_name",
    ]


def test_active_event_membership_uses_first_sorted_active_member():
    membership = ActiveEventMembership.from_members(
        [
            _member(
                3,
                match_id="match-shared",
                bookmaker_id="mozzart",
                resolved_event_id="evt-late",
            ),
            _member(
                1,
                match_id="match-shared",
                bookmaker_id="mozzart",
                resolved_event_id="evt-early",
            ),
            _member(
                2,
                match_id="match-inactive",
                bookmaker_id="maxbet",
                resolved_event_id="evt-inactive",
                status="inactive",
            ),
        ]
    )

    assert membership.resolved_event_id_for(
        match_id="match-shared",
        bookmaker_id="mozzart",
    ) == "evt-early"
    assert (
        membership.resolved_event_id_for(
            match_id="match-inactive",
            bookmaker_id="maxbet",
        )
        is None
    )
