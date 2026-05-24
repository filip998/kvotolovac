from __future__ import annotations

from app.models.schemas import NormalizedOdds, ResolvedEventMemberOut
from app.services.match_unification import player_identity
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
        "unsupported_player_market": 0,
        "empty_player_name": 1,
    }
    assert [skipped.reason for skipped in resolution.skipped] == [
        "missing_resolved_event_member",
        "missing_resolved_event_member",
        "non_player_market",
        "unsupported_sport",
        "empty_player_name",
    ]


def test_supported_sport_unsupported_player_market_reports_market_reason():
    odds = [
        _odds(
            match_id="tennis-player-points",
            bookmaker_id="book-a",
            player_name="Roger Federer",
            market_type="player_points",
            sport="tennis",
        )
    ]

    resolution = resolve_event_players(odds, ActiveEventMembership.from_members([]))

    assert resolution.scoped_odds == ()
    assert resolution.skipped_counts == {
        "missing_resolved_event_member": 0,
        "non_player_market": 0,
        "unsupported_sport": 0,
        "unsupported_player_market": 1,
        "empty_player_name": 0,
    }
    assert [skipped.reason for skipped in resolution.skipped] == [
        "unsupported_player_market"
    ]


def test_tennis_player_market_is_event_scoped():
    odds = [
        _odds(
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            player_name="Novak Djokovic",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-meridian",
            bookmaker_id="meridian",
            player_name="Novak Djokovic",
            market_type="player_games_won",
            sport="tennis",
        ),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                1,
                match_id="match-mozzart",
                bookmaker_id="mozzart",
                resolved_event_id="evt-djokovic-federer",
            ),
            _member(
                2,
                match_id="match-meridian",
                bookmaker_id="meridian",
                resolved_event_id="evt-djokovic-federer",
            ),
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert len(resolution.scoped_odds) == 2
    assert resolution.skipped == ()
    assert {item.event_player_display_name for item in resolution.scoped_odds} == {
        "Novak Djokovic"
    }
    assert len({item.event_scoped_player_key for item in resolution.scoped_odds}) == 1


def test_tennis_name_variants_merge_with_sufficient_event_context():
    odds = [
        _odds(
            match_id="match-full",
            bookmaker_id="book-a",
            player_name="Roger Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-initial",
            bookmaker_id="book-b",
            player_name="R. Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-comma",
            bookmaker_id="book-c",
            player_name="Federer, Roger",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-family",
            bookmaker_id="book-d",
            player_name="Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                index,
                match_id=odds_row.match_id,
                bookmaker_id=odds_row.bookmaker_id,
            )
            for index, odds_row in enumerate(odds, start=1)
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert len(resolution.scoped_odds) == 4
    assert len({item.event_scoped_player_key for item in resolution.scoped_odds}) == 1
    assert {item.event_player_display_name for item in resolution.scoped_odds} == {
        "Roger Federer"
    }
    assert len(resolution.identities) == 1
    assert resolution.identities[0].source_variants == (
        "Roger Federer",
        "Federer",
        "Federer, Roger",
        "R. Federer",
    )


def test_tennis_family_only_particle_surname_merges_with_context():
    odds = [
        _odds(
            match_id="match-full",
            bookmaker_id="book-a",
            player_name="Alex de Minaur",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-initial",
            bookmaker_id="book-b",
            player_name="A. de Minaur",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-family",
            bookmaker_id="book-c",
            player_name="De Minaur",
            market_type="player_games_won",
            sport="tennis",
        ),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                index,
                match_id=odds_row.match_id,
                bookmaker_id=odds_row.bookmaker_id,
            )
            for index, odds_row in enumerate(odds, start=1)
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert len(resolution.scoped_odds) == 3
    assert len({item.event_scoped_player_key for item in resolution.scoped_odds}) == 1
    assert {item.event_player_display_name for item in resolution.scoped_odds} == {
        "Alex de Minaur"
    }
    assert len(resolution.identities) == 1
    assert resolution.identities[0].source_variants == (
        "Alex de Minaur",
        "A. de Minaur",
        "De Minaur",
    )


def test_unpunctuated_reversed_tennis_name_does_not_change_existing_key():
    single = [
        _odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Alexander Zverev",
            market_type="player_games_won",
            sport="tennis",
        )
    ]
    single_membership = ActiveEventMembership.from_members(
        [_member(1, match_id="match-a", bookmaker_id="book-a")]
    )
    single_resolution = resolve_event_players(single, single_membership)

    odds_with_reversed_peer = [
        *single,
        _odds(
            match_id="match-b",
            bookmaker_id="book-b",
            player_name="Zverev Alexander",
            market_type="player_games_won",
            sport="tennis",
        ),
    ]
    membership_with_peer = ActiveEventMembership.from_members(
        [
            _member(1, match_id="match-a", bookmaker_id="book-a"),
            _member(2, match_id="match-b", bookmaker_id="book-b"),
        ]
    )

    resolution_with_peer = resolve_event_players(
        odds_with_reversed_peer,
        membership_with_peer,
    )

    assert {identity.display_name for identity in resolution_with_peer.identities} == {
        "Alexander Zverev",
        "Zverev Alexander",
    }
    assert (
        next(
            item.event_scoped_player_key
            for item in resolution_with_peer.scoped_odds
            if item.event_player_display_name == "Alexander Zverev"
        )
        == single_resolution.scoped_odds[0].event_scoped_player_key
    )


def test_tennis_display_selection_is_independent_of_input_order():
    def _resolve_ordered(player_names: list[str]):
        odds = [
            _odds(
                match_id=f"match-{index}",
                bookmaker_id=f"book-{index}",
                player_name=player_name,
                market_type="player_games_won",
                sport="tennis",
            )
            for index, player_name in enumerate(player_names, start=1)
        ]
        membership = ActiveEventMembership.from_members(
            [
                _member(
                    index,
                    match_id=odds_row.match_id,
                    bookmaker_id=odds_row.bookmaker_id,
                )
                for index, odds_row in enumerate(odds, start=1)
            ]
        )
        return resolve_event_players(odds, membership)

    first = _resolve_ordered(["Roger Federer", "roger federer"])
    second = _resolve_ordered(["roger federer", "Roger Federer"])

    assert {identity.display_name for identity in first.identities} == {
        "Roger Federer"
    }
    assert {identity.display_name for identity in second.identities} == {
        "Roger Federer"
    }
    assert (
        first.identities[0].event_scoped_player_key
        == second.identities[0].event_scoped_player_key
    )


def test_tennis_ambiguous_initials_stay_separate_without_context():
    odds = [
        _odds(
            match_id="match-roger",
            bookmaker_id="book-a",
            player_name="Roger Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-rafael",
            bookmaker_id="book-b",
            player_name="Rafael Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
        _odds(
            match_id="match-initial",
            bookmaker_id="book-c",
            player_name="R. Federer",
            market_type="player_games_won",
            sport="tennis",
        ),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                index,
                match_id=odds_row.match_id,
                bookmaker_id=odds_row.bookmaker_id,
            )
            for index, odds_row in enumerate(odds, start=1)
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert len(resolution.scoped_odds) == 3
    assert len({item.event_scoped_player_key for item in resolution.scoped_odds}) == 3
    assert {item.event_player_display_name for item in resolution.scoped_odds} == {
        "Roger Federer",
        "Rafael Federer",
        "R. Federer",
    }


def test_event_scoped_player_key_is_stable_for_same_event_and_display_name():
    odds = [
        _odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokić",
        )
    ]
    membership = ActiveEventMembership.from_members(
        [_member(1, match_id="match-a", bookmaker_id="book-a")]
    )

    first = resolve_event_players(odds, membership)
    second = resolve_event_players(odds, membership)

    assert first.scoped_odds[0].event_player_display_name == "Nikola Jokić"
    assert (
        first.scoped_odds[0].event_scoped_player_key
        == second.scoped_odds[0].event_scoped_player_key
    )


def test_event_scoped_player_key_is_stable_for_normalized_display_variants():
    plain = [
        _odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokic",
        )
    ]
    accented = [
        _odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokić",
        )
    ]
    membership = ActiveEventMembership.from_members(
        [_member(1, match_id="match-a", bookmaker_id="book-a")]
    )

    plain_resolution = resolve_event_players(plain, membership)
    accented_resolution = resolve_event_players(accented, membership)

    assert (
        plain_resolution.scoped_odds[0].event_scoped_player_key
        == accented_resolution.scoped_odds[0].event_scoped_player_key
    )


def test_event_scoped_player_key_differs_across_resolved_events():
    odds = [
        _odds(
            match_id="match-a",
            bookmaker_id="book-a",
            player_name="Nikola Jokić",
        ),
        _odds(
            match_id="match-b",
            bookmaker_id="book-b",
            player_name="Nikola Jokić",
        ),
    ]
    membership = ActiveEventMembership.from_members(
        [
            _member(
                1,
                match_id="match-a",
                bookmaker_id="book-a",
                resolved_event_id="evt-a",
            ),
            _member(
                2,
                match_id="match-b",
                bookmaker_id="book-b",
                resolved_event_id="evt-b",
            ),
        ]
    )

    resolution = resolve_event_players(odds, membership)

    assert {item.event_player_display_name for item in resolution.scoped_odds} == {
        "Nikola Jokić"
    }
    assert len({item.event_scoped_player_key for item in resolution.scoped_odds}) == 2


def test_compact_name_collision_is_disambiguated_before_key_generation():
    policies = dict(player_identity._PLAYER_IDENTITY_POLICIES)
    policies["collisionball"] = player_identity._PlayerIdentityPolicy(
        market_supported=lambda market_type: market_type == "player_collision",
        resolve_display_names=lambda names: {
            name.strip(): name.strip() for name in names if name.strip()
        },
    )
    original_policies = player_identity._PLAYER_IDENTITY_POLICIES
    player_identity._PLAYER_IDENTITY_POLICIES = policies
    try:
        single_odds = [
            _odds(
                match_id="match-a",
                bookmaker_id="book-a",
                player_name="AB",
                market_type="player_collision",
                sport="collisionball",
            )
        ]
        single_membership = ActiveEventMembership.from_members(
            [_member(1, match_id="match-a", bookmaker_id="book-a")]
        )
        single_resolution = resolve_event_players(single_odds, single_membership)

        colliding_odds = [
            _odds(
                match_id="match-a",
                bookmaker_id="book-a",
                player_name="AB",
                market_type="player_collision",
                sport="collisionball",
            ),
            _odds(
                match_id="match-b",
                bookmaker_id="book-b",
                player_name="A B",
                market_type="player_collision",
                sport="collisionball",
            ),
        ]
        membership = ActiveEventMembership.from_members(
            [
                _member(1, match_id="match-a", bookmaker_id="book-a"),
                _member(2, match_id="match-b", bookmaker_id="book-b"),
            ]
        )

        resolution = resolve_event_players(colliding_odds, membership)
    finally:
        player_identity._PLAYER_IDENTITY_POLICIES = original_policies

    assert {identity.display_name for identity in resolution.identities} == {"AB", "A B"}
    assert (
        len({identity.event_scoped_player_key for identity in resolution.identities})
        == 2
    )
    assert (
        next(
            item.event_scoped_player_key
            for item in resolution.scoped_odds
            if item.event_player_display_name == "AB"
        )
        == single_resolution.scoped_odds[0].event_scoped_player_key
    )


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
