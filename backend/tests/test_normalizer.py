from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from app.services.normalizer import (
    _resolve_contextual_player_name_replacements,
    generate_match_id,
    normalize_league_id,
    normalize_market_type,
    normalize_odds,
    normalize_odds_with_diagnostics,
    normalize_odds_with_issues,
    normalize_player_name,
    normalize_team_name,
)
from app.services.team_registry import (
    CircularAliasError,
    clear_team_registry_cache,
    remember_team_alias,
)
from app.models.schemas import RawOddsData


def test_normalize_team_exact():
    assert normalize_team_name("Olympiacos") == "Olympiacos"
    assert normalize_team_name("olympiacos") == "Olympiacos"


def test_normalize_team_alias():
    assert normalize_team_name("Red Star") == "Crvena Zvezda"
    assert normalize_team_name("barcelona") == "FC Barcelona"
    assert normalize_team_name("Houston", "nba") == "Houston Rockets"
    assert normalize_team_name("Minnesota", "nba") == "Minnesota Timberwolves"
    assert normalize_team_name("Crv.Zvezda") == "Crvena Zvezda"
    assert normalize_team_name("Cluj Napoc") == "Universitatea Cluj"
    assert normalize_team_name("Budućnost") == "Buducnost"
    assert normalize_team_name("KK Crvena Zvezda") == "Crvena Zvezda"
    assert normalize_team_name("Ostrow") == "Ostrow Wielkopolski"
    assert normalize_team_name("Fenerbahce Istanbul", "euroleague") == "Fenerbahce"
    assert normalize_team_name("ASVEL Lyon-Villeurbanne", "euroleague") == "Asvel"
    assert normalize_team_name("Lyon-Villeurb.", "euroleague") == "Asvel"
    assert normalize_team_name("UU-Korihait", "korisliiga") == "Korihait Uusikaupunki"
    assert normalize_team_name("Salon Vilpas Vikings", "korisliiga") == "Salon Vilpas"


def test_normalize_team_nba_aliases_are_sport_scoped():
    assert normalize_team_name("Houston") == "Houston Rockets"
    assert normalize_team_name("Houston", "euroleague") == "Houston Rockets"
    assert normalize_team_name("Houston", "nba") == "Houston Rockets"


def test_normalize_team_fuzzy():
    assert normalize_team_name("Olympiakos") == "Olympiacos"


def test_normalize_team_fuzzy_does_not_merge_opponents_with_shared_city_tokens():
    assert normalize_team_name("Hapoel Tel-Aviv", "euroleague") == "Hapoel Tel-Aviv"


def test_normalize_team_alias_chains_collapse_to_final_target(team_registry_file):
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Uniao Corinthians",
        team_name="EC Uniao Corinthians",
        competition_id="brazil_nbb",
    )
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="U.Corinthians",
        team_name="Uniao Corinthians",
        competition_id="brazil_nbb",
    )

    assert normalize_team_name("Uniao Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"
    assert normalize_team_name("U.Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"


def test_remember_team_alias_preserves_reviewed_target_before_chain_resolution(team_registry_file):
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Uniao Corinthians",
        team_name="EC Uniao Corinthians",
        competition_id="brazil_nbb",
    )

    resolution = remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="U.Corinthians",
        team_name="Uniao Corinthians",
        competition_id="brazil_nbb",
    )
    assert resolution.team_name == "EC Uniao Corinthians"
    assert normalize_team_name("U.Corinthians", "brazil_nbb", "meridian") == "EC Uniao Corinthians"


def test_remember_team_alias_rejects_circular_alias(team_registry_file):
    remember_team_alias(
        bookmaker_id="meridian",
        raw_team_name="Baskonia Gatez",
        team_name="Baskonia",
        competition_id="euroleague",
    )

    with pytest.raises(CircularAliasError, match="Circular alias"):
        remember_team_alias(
            bookmaker_id="meridian",
            raw_team_name="Baskonia",
            team_name="Baskonia Gatez",
            competition_id="euroleague",
        )


def test_legacy_competition_aliases_are_not_imported_globally(team_registry_file):
    Path(team_registry_file).write_text(
        json.dumps(
            {
                "aliases": {},
                "bookmaker_aliases": {},
                "competition_aliases": {
                    "argentina_1": {
                        "Legacy Scoped Alias": "Legacy Scoped Canonical",
                    }
                },
                "bookmaker_competition_aliases": {
                    "meridian": {
                        "argentina_1": {
                            "Legacy Scoped Bookmaker Alias": "Legacy Scoped Bookmaker Canonical",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    clear_team_registry_cache()

    assert normalize_team_name("Legacy Scoped Alias") == "Legacy Scoped Alias"
    assert (
        normalize_team_name("Legacy Scoped Bookmaker Alias", None, "meridian")
        == "Legacy Scoped Bookmaker Alias"
    )


def test_normalize_player_full_name():
    assert normalize_player_name("Sasha Vezenkov") == "Sasha Vezenkov"


def test_normalize_player_abbreviated():
    assert normalize_player_name("S. Vezenkov") == "Sasha Vezenkov"
    assert normalize_player_name("Vezenkov S.") == "Sasha Vezenkov"


def test_normalize_player_initial_format():
    assert normalize_player_name("F. Campazzo") == "Facundo Campazzo"
    assert normalize_player_name("Campazzo F.") == "Facundo Campazzo"
    assert normalize_player_name("K.Durant") == "Kevin Durant"


def test_normalize_odds_does_not_overresolve_double_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="Cameron McCollum",
            threshold=14.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=21.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Cameron McCollum",
        "C.J. McCollum",
    ]


def test_normalize_odds_resolves_unambiguous_single_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City",
            away_team="J. Williams",
            market_type="player_points",
            player_name="J. Williams",
            threshold=17.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    # Only one Williams in the match → unambiguous, should resolve
    assert [offer.player_name for offer in normalized] == [
        "Jalen Williams",
        "Jalen Williams",
    ]


def test_normalize_odds_resolves_supported_single_initial_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Phoenix Suns",
            away_team="Denver Nuggets",
            market_type="player_points",
            player_name="Colin Gillespie",
            threshold=7.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Phoenix",
            away_team="Denver",
            market_type="player_assists",
            player_name="Colin Gillespie",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Phoenix Suns",
            away_team="Denver Nuggets",
            market_type="player_points",
            player_name="C. Gillespie",
            threshold=7.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Colin Gillespie",
        "Colin Gillespie",
        "Colin Gillespie",
    ]


def test_normalize_odds_merges_single_initial_with_multi_initial_candidate():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=21.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_assists",
            player_name="C.J. McCollum",
            threshold=4.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=20.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    # Within the same event, "C. McCollum" is treated as an abbreviation of
    # "C.J. McCollum" — the single initial is prefix-compatible with the multi
    # initial sequence and there is only one canonical given-name candidate, so
    # all rows collapse to the more complete name.
    assert [offer.player_name for offer in normalized] == [
        "C.J. McCollum",
        "C.J. McCollum",
        "C.J. McCollum",
    ]


def test_normalize_odds_keeps_single_initial_separate_when_multi_initial_candidates_disagree():
    """Within one event, a single-initial label must NOT be merged when there are
    multiple multi-initial candidates that share its first initial but disagree
    on later positions (e.g. "C.J." and "C.K." are different players, so "C." is
    ambiguous between them and should stay unresolved)."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=18.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=18.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.K. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="merkurxtip",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.K. McCollum",
            threshold=18.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "C. McCollum",
        "C.J. McCollum",
        "C.J. McCollum",
        "C.K. McCollum",
        "C.K. McCollum",
    ]


def test_normalize_odds_collapses_single_initial_with_competing_extensions_when_clear_majority():
    """Within one event, prefix-compatible abbreviation depths describe the
    same player. When a single initial appears alongside two abbreviation
    extensions of different length (``C.``/``C.J.``/``C.J.K.``) and one
    extension carries the bookmaker majority, all surfaces merge into that
    majority. The structural rule subsumes the older "multi-initial all-
    single-letter" guard: same-length mismatched-position cases (``C.J.`` vs
    ``C.K.``) are still rejected by `_letter_seq_collapse_compatible`'s same-
    length branch, which is what guards against genuinely-different players
    here. See ``test_normalize_odds_keeps_single_initial_separate_when_multi_initial_candidates_disagree``
    for the strict counterpart."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="merkurxtip",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "C.J.K. McCollum",
        "C.J.K. McCollum",
        "C.J.K. McCollum",
        "C.J.K. McCollum",
        "C.J.K. McCollum",
    ]


def test_normalize_odds_keeps_single_initial_separate_when_candidates_only_fuzzy_match():
    """Diversity-collapse must not use fuzzy matching across distinct candidates.
    ``Jalen`` and ``Jaden`` differ on position 0 (no exact-prefix relation);
    rapidfuzz reports ratio >= 80 between them, but treating that as "same
    player" would silently resolve ``J. Williams`` to whichever candidate
    happens to win the rank, which is just guesswork."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="J. Williams",
            threshold=15.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jaden Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="merkurxtip",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jaden Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "J. Williams",
        "Jalen Williams",
        "Jalen Williams",
        "Jaden Williams",
        "Jaden Williams",
    ]


def test_normalize_odds_does_not_collapse_short_full_name_into_longer_extension():
    """A multi-character given name like ``Jo`` must not be rewritten into a
    longer prefix-compatible name like ``John`` just because an abbreviation
    ``J.`` is also present in the same event. Jo and John are distinct names
    (the prefix relation is coincidental); only true initials should be
    expanded by the contextual resolver."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="J. Williams",
            threshold=15.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jo Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="John Williams",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "J. Williams",
        "Jo Williams",
        "John Williams",
    ]


def test_normalize_odds_resolves_dotted_abbreviation_with_diacritic_surname():
    """A dotted multi-character abbreviation like ``Stef.`` must merge with the
    full ``Stefan`` form even when the surname carries diacritics
    (``Miljenović``). The contextual resolver folds diacritics on both the
    surface and the parsed surname when locating the first-name region, so the
    explicit dot signal is detected and ``Stef. Miljenović`` is rewritten."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="aba_liga",
            home_team="Mega",
            away_team="Partizan",
            market_type="player_points",
            player_name="Stef. Miljenović",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="aba_liga",
            home_team="Mega",
            away_team="Partizan",
            market_type="player_points",
            player_name="Stefan Miljenović",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Mega",
            away_team="Partizan",
            market_type="player_points",
            player_name="Stefan Miljenović",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Stefan Miljenović",
        "Stefan Miljenović",
        "Stefan Miljenović",
    ]


def test_normalize_odds_keeps_surname_particle_player_separate_from_lookalike_first_name():
    """A surface like ``St.Brown`` is parsed as ``first=st last=brown`` by the
    simple two-token parser, but ``St.`` is a surname particle (Saint), not a
    first-name abbreviation. It must NOT be rewritten into a same-event
    ``Stefan Brown`` (a different player who genuinely has ``Stefan`` as a
    given name and ``Brown`` as surname). The contextual resolver excludes the
    common surname-particle tokens from the dotted-abbreviation signal."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nfl",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="St.Brown",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nfl",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Stefan Brown",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == ["St.Brown", "Stefan Brown"]


def test_normalize_odds_extends_multi_initial_into_longer_initial_majority():
    """Within one event, a 2-initial label like ``C.J.`` and a 3-initial label
    like ``C.J.K.`` for the same surname describe the same player at different
    abbreviation depths. The chance that two players whose first-name
    abbreviations form a prefix chain co-occur in a single match is
    effectively zero, so we let the bookmaker majority decide. This is the
    inverse of `test_normalize_odds_keeps_single_initial_separate_when_multi_initial_candidates_disagree`,
    where same-length mismatched-position cases (``C.J.`` vs ``C.K.``) stay
    distinct via the structural per-position prefix check."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "C.J.K. McCollum",
        "C.J.K. McCollum",
        "C.J.K. McCollum",
    ]


def test_normalize_odds_contracts_longer_initial_sequence_into_shorter_majority():
    """Mirror of the previous test: when the shorter abbreviation has the
    bookmaker majority, the longer one merges down. Within one event, prefix-
    compatible abbreviations across length describe the same player; the
    count-based tie-break is what selects the canonical surface."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "C.J. McCollum",
        "C.J. McCollum",
        "C.J. McCollum",
    ]


def test_normalize_odds_keeps_ambiguous_short_prefix_players():
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jalen Williams",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_assists",
            player_name="Jalen Williams",
            threshold=5.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagon",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Jaylin Williams",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Dallas Mavericks",
            market_type="player_points",
            player_name="Ja. Williams",
            threshold=17.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Jalen Williams",
        "Jalen Williams",
        "Jaylin Williams",
        "Ja. Williams",
    ]


def test_normalize_odds_with_issues_reports_unresolved_shared_platform_rows():
    normalized, unresolved = normalize_odds_with_issues(
        [
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id="aba_liga",
                home_team="Borac Cacak",
                away_team="P. Nikolic",
                market_type="player_points",
                player_name="P. Nikolic",
                threshold=10.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-13T16:00:00+00:00",
            )
        ]
    )

    assert normalized == []
    assert len(unresolved) == 1
    assert unresolved[0].reason_code == "no_canonical_matchup_for_team_at_slot"
    assert unresolved[0].raw_team_name == "Borac Cacak"


def test_normalize_odds_with_diagnostics_can_suppress_shared_platform_logs(caplog):
    normalized, unresolved, _team_reviews = normalize_odds_with_diagnostics(
        [
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id="aba_liga",
                home_team="Borac Cacak",
                away_team="P. Nikolic",
                market_type="player_points",
                player_name="P. Nikolic",
                threshold=10.5,
                over_odds=1.8,
                under_odds=2.0,
                start_time="2026-04-13T16:00:00+00:00",
            )
        ],
        log_unresolved_shared_platform=False,
    )

    assert normalized == []
    assert len(unresolved) == 1
    assert "Dropping" not in caplog.text


def test_normalize_odds_logs_grouped_shared_platform_warnings(caplog):
    raw = [
        RawOddsData(
            bookmaker_id="admiralbet",
            league_id="aba_liga",
            home_team="Borac Cacak",
            away_team=player_name,
            market_type="player_points",
            player_name=player_name,
            threshold=threshold,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-13T16:00:00+00:00",
        )
        for player_name, threshold in (("P. Nikolic", 10.5), ("P. Nikolic", 12.5))
    ]

    normalize_odds_with_diagnostics(raw)

    warning_messages = [
        record.message
        for record in caplog.records
        if "unresolved shared-platform props" in record.message
    ]
    assert len(warning_messages) == 1
    assert "Dropping 2 unresolved shared-platform props for Borac Cacak" in warning_messages[0]


def test_normalize_odds_with_issues_infers_two_team_shared_platform_slot():
    normalized, unresolved = normalize_odds_with_issues(
        [
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
                start_time="2026-04-13T16:15:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="admiralbet",
                league_id="poljska 1",
                home_team="Zielona Gora",
                away_team="Mareks Mejeris",
                market_type="player_points",
                player_name="Mareks Mejeris",
                threshold=10.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-13T16:15:00+00:00",
            ),
            RawOddsData(
                bookmaker_id="maxbet",
                league_id="poland",
                home_team="Ostrow",
                away_team="Chris Smith",
                market_type="player_points",
                player_name="Chris Smith",
                threshold=12.5,
                over_odds=1.85,
                under_odds=1.95,
                start_time="2026-04-13T16:15:00+00:00",
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
                start_time="2026-04-13T16:15:00+00:00",
            ),
        ]
    )

    assert unresolved == []
    assert len(normalized) == 4
    assert {
        (offer.home_team, offer.away_team)
        for offer in normalized
    } == {("Ostrow Wielkopolski", "Zielona Gora")}


def test_normalize_odds_resolves_unique_match_local_player_variants():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aaron Wiggins",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aar.Wiggins",
            threshold=2.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Jalen Green",
            threshold=4.5,
            over_odds=1.95,
            under_odds=1.75,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Jal.Green",
            threshold=4.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Mark Williams",
            threshold=7.5,
            over_odds=1.88,
            under_odds=1.92,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Mar.Williams",
            threshold=7.5,
            over_odds=1.82,
            under_odds=1.98,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Aaron Wiggins",
        "Aaron Wiggins",
        "Jalen Green",
        "Jalen Green",
        "Mark Williams",
        "Mark Williams",
    ]


def test_normalize_odds_prefers_more_supported_full_name_variant():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Aaron Wiggins",
            threshold=2.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_points",
            player_name="Aaron Wiggins",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Phoenix Suns",
            market_type="player_assists",
            player_name="Arron Wiggins",
            threshold=2.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Aaron Wiggins",
        "Aaron Wiggins",
        "Aaron Wiggins",
    ]


def test_normalize_player_none():
    assert normalize_player_name(None) is None
    assert normalize_player_name("") is None


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("player_points", "player_points"),
        ("Player Points", "player_points"),
        ("points", "player_points"),
        ("player_rebounds", "player_rebounds"),
        ("Player Rebounds", "player_rebounds"),
        ("player_assists", "player_assists"),
        ("Player Assists", "player_assists"),
        ("player_3points", "player_3points"),
        ("Player 3 Points", "player_3points"),
        ("player_steals", "player_steals"),
        ("Player Steals", "player_steals"),
        ("player_blocks", "player_blocks"),
        ("Player Blocks", "player_blocks"),
        ("player_points_rebounds", "player_points_rebounds"),
        ("Player Points + Rebounds", "player_points_rebounds"),
        ("Points+Rebounds", "player_points_rebounds"),
        ("player_points_assists", "player_points_assists"),
        ("Player Points & Assists", "player_points_assists"),
        ("player_rebounds_assists", "player_rebounds_assists"),
        ("Player Rebounds + Assists", "player_rebounds_assists"),
        ("player_points_rebounds_assists", "player_points_rebounds_assists"),
        ("Player Points + Rebounds + Assists", "player_points_rebounds_assists"),
        ("PRA", "player_points_rebounds_assists"),
        ("player_points_milestones", "player_points_milestones"),
        ("Player Points Milestones", "player_points_milestones"),
        ("player_points_ladder", "player_points_milestones"),
        ("game_total", "game_total"),
        ("Game Total", "game_total"),
        ("total", "game_total"),
        ("game_total_ot", "game_total_ot"),
        ("Game Total OT", "game_total_ot"),
    ],
)
def test_normalize_market_type(raw_type, expected):
    assert normalize_market_type(raw_type) == expected


def test_generate_match_id_deterministic():
    start_time = "2026-04-16T20:00:00+00:00"
    id1 = generate_match_id("Partizan", "Crvena Zvezda", start_time)
    id2 = generate_match_id("Partizan", "Crvena Zvezda", start_time)
    assert id1 == id2


def test_generate_match_id_unique():
    start_time = "2026-04-16T20:00:00+00:00"
    id1 = generate_match_id("Partizan", "Crvena Zvezda", start_time)
    id2 = generate_match_id("Partizan", "Real Madrid", start_time)
    assert id1 != id2


def test_generate_match_id_includes_sport():
    start_time = "2026-04-16T20:00:00+00:00"

    basketball_id = generate_match_id(
        "Partizan",
        "Crvena Zvezda",
        start_time,
        sport="basketball",
    )
    football_id = generate_match_id(
        "Partizan",
        "Crvena Zvezda",
        start_time,
        sport="football",
    )

    assert basketball_id != football_id


def test_normalize_league_id_alias():
    assert normalize_league_id("usa-nba") == "nba"
    assert normalize_league_id("USA NBA") == "nba"
    assert normalize_league_id("usa_nba") == "nba"
    assert normalize_league_id("nba_play_offs") == "nba"
    assert normalize_league_id("NBA - Promotion - Play Offs") == "nba"
    assert normalize_league_id("aba liga - winners stage") == "aba_liga"
    assert normalize_league_id("AdmiralBet ABA liga - plej of") == "aba_liga"
    assert normalize_league_id("italija_1") == "italy"
    assert normalize_league_id("Germany BBL") == "germany"
    assert normalize_league_id("Finska 1") == "korisliiga"
    assert normalize_league_id("Finska 1 plej of") == "korisliiga"
    assert normalize_league_id("Finnish League") == "korisliiga"
    assert normalize_league_id("Finland Play Offs") == "korisliiga"
    assert normalize_league_id("Finland Korisliiga") == "korisliiga"
    assert normalize_league_id("balkanbet_tournament_486") == "korisliiga"
    assert normalize_league_id("euroleague") == "euroleague"


def test_normalize_odds_full_pipeline():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
            start_time="2026-04-16T20:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Red Star",  # alias
            market_type="player_points",
            player_name="I. Lundberg",  # abbreviated
            threshold=18.5,
            over_odds=1.80,
            under_odds=2.00,
            start_time="2026-04-16T20:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert len(normalized) == 2
    # Both should map to the same match_id
    assert normalized[0].match_id == normalized[1].match_id
    # Both should resolve to canonical player name
    assert normalized[0].player_name == "Iffe Lundberg"
    assert normalized[1].player_name == "Iffe Lundberg"
    # Away team should be normalized
    assert normalized[1].away_team == "Crvena Zvezda"


def test_normalize_odds_resolves_shared_platform_matchups_and_aliases():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Houston",
            away_team="Minnesota",
            market_type="player_points",
            player_name="K.Durant",
            threshold=24.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="usa-nba",
            home_team="Houston Rockets",
            away_team="Minnesota Timberwolves",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=25.5,
            over_odds=1.88,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
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
            home_team="Houston Rockets",
            away_team="Kevin Durant",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=23.5,
            over_odds=1.6,
            under_odds=2.1,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 4
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.league_id for offer in normalized} == {"nba"}
    assert {offer.home_team for offer in normalized} == {"Houston Rockets"}
    assert {offer.away_team for offer in normalized} == {"Minnesota Timberwolves"}
    assert {offer.player_name for offer in normalized} == {"Kevin Durant"}


def test_normalize_odds_merges_korihait_vilpas_bookmaker_variants():
    raw = [
        RawOddsData(
            bookmaker_id="admiralbet",
            league_id="finska 1",
            home_team="UU Korihait Uusikaupunki",
            away_team="Salon Vilpas",
            market_type="game_total_ot",
            player_name=None,
            threshold=163.5,
            over_odds=1.8,
            under_odds=1.9,
            start_time="2026-04-15T15:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="finnish league",
            home_team="Korihait Uusikaupunki",
            away_team="Salon Vilpas Vikings",
            market_type="game_total_ot",
            player_name=None,
            threshold=164.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-15T15:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="finland play offs",
            home_team="Korihait U.",
            away_team="Salon Vilpas",
            market_type="game_total_ot",
            player_name=None,
            threshold=165.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-04-15T15:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="balkanbet_tournament_486",
            home_team="Korihait Uusikaupunki",
            away_team="Salon Vilpas Vikings",
            market_type="game_total_ot",
            player_name=None,
            threshold=162.5,
            over_odds=1.87,
            under_odds=1.87,
            start_time="2026-04-15T15:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="merkurxtip",
            league_id="korisliiga",
            home_team="UU-Korihait",
            away_team="Salon Vilpas",
            market_type="game_total_ot",
            player_name=None,
            threshold=164.0,
            over_odds=1.86,
            under_odds=1.86,
            start_time="2026-04-15T15:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 5
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.league_id for offer in normalized} == {"korisliiga"}
    assert {offer.home_team for offer in normalized} == {"Korihait Uusikaupunki"}
    assert {offer.away_team for offer in normalized} == {"Salon Vilpas"}


def test_normalize_odds_drops_unresolved_shared_platform_rows():
    raw = [
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
    ]

    assert normalize_odds(raw) == []


def test_normalize_odds_uses_deterministic_match_orientation():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Minnesota",
            away_team="Houston",
            market_type="player_points",
            player_name="K.Durant",
            threshold=24.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="usa-nba",
            home_team="Houston Rockets",
            away_team="Minnesota Timberwolves",
            market_type="player_points",
            player_name="Kevin Durant",
            threshold=25.5,
            over_odds=1.88,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
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
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 3
    assert len({offer.match_id for offer in normalized}) == 1
    assert {offer.home_team for offer in normalized} == {"Houston Rockets"}
    assert {offer.away_team for offer in normalized} == {"Minnesota Timberwolves"}


def test_normalize_odds_normalizes_new_market_types():
    raw = [
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="euroleague",
            home_team="Olympiakos",
            away_team="Barcelona",
            market_type="Player Points + Rebounds",
            player_name="S. Vezenkov",
            threshold=26.5,
            over_odds=1.83,
            under_odds=1.97,
            start_time="2026-04-16T19:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert normalized[0].market_type == "player_points_rebounds"
    assert normalized[0].home_team == "Olympiacos"
    assert normalized[0].away_team == "FC Barcelona"
    assert normalized[0].player_name == "Sasha Vezenkov"


def test_normalize_odds_resolves_prefix_player_variants_with_shared_matchup():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Crv.Zvezda",
            away_team="Cluj Napoc",
            market_type="player_points",
            player_name="Ja.Butler",
            threshold=11.5,
            over_odds=1.45,
            under_odds=2.5,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Jar.Butler",
            threshold=13.5,
            over_odds=1.82,
            under_odds=1.97,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Jared Butler",
            threshold=15.5,
            over_odds=2.30,
            under_odds=1.53,
            start_time="2026-04-13T16:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Jared Butler",
        "Jared Butler",
        "Jared Butler",
    ]
    assert {offer.home_team for offer in normalized} == {"Crvena Zvezda"}
    assert {offer.away_team for offer in normalized} == {"Universitatea Cluj"}


def test_normalize_odds_merges_hyphen_and_space_surnames():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Dubai",
            away_team="Buducnost",
            market_type="player_points",
            player_name="Codi Miller-McIntyre",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T18:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Dubai",
            away_team="Budućnost",
            market_type="player_points",
            player_name="Codi Miller McIntyre",
            threshold=12.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-13T18:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Codi Miller-McIntyre",
        "Codi Miller-McIntyre",
    ]


def test_normalize_odds_prefers_full_name_over_initial_and_diacritic_variants():
    raw = [
        RawOddsData(
            bookmaker_id="pinnbet",
            league_id="aba_liga",
            home_team="Crv.Zvezda",
            away_team="Cluj Napoc",
            market_type="player_points",
            player_name="S.Miljenovic",
            threshold=9.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="S. Miljenović",
            threshold=11.5,
            over_odds=1.8,
            under_odds=2.0,
            start_time="2026-04-13T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="oktagonbet",
            league_id="aba_liga",
            home_team="Crvena Zvezda",
            away_team="Universitatea Cluj",
            market_type="player_points",
            player_name="Stefan Miljenović",
            threshold=13.5,
            over_odds=1.7,
            under_odds=2.1,
            start_time="2026-04-13T16:00:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Stefan Miljenović",
        "Stefan Miljenović",
        "Stefan Miljenović",
    ]


def test_normalize_preserves_thresholds():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="euroleague",
            home_team="Partizan",
            away_team="Crvena Zvezda",
            market_type="player_points",
            player_name="Iffe Lundberg",
            threshold=16.5,
            over_odds=1.85,
            under_odds=1.95,
            start_time="2026-04-16T20:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert normalized[0].threshold == 16.5
    assert normalized[0].over_odds == 1.85


def test_normalize_reorients_home_handicap_when_source_home_is_canonical_away(team_registry_file):
    raw = [
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="paraguay",
            home_team="CA Ciudad Nueva",
            away_team="Felix Perez Cardozo",
            market_type="home_handicap_ot",
            threshold=-16.5,
            over_odds=1.87,
            under_odds=1.90,
            start_time="2026-05-04T23:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="volcanobet",
            league_id="paragvaj_1",
            home_team="CA Ciudad Nueva",
            away_team="Felix Perez Cardozo",
            market_type="home_handicap_ot",
            threshold=-15.5,
            over_odds=1.97,
            under_odds=1.75,
            start_time="2026-05-04T23:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="admiralbet",
            league_id="lnb",
            home_team="Felix Perez Cardozo",
            away_team="CA Ciudad Nueva",
            market_type="home_handicap_ot",
            threshold=16.5,
            over_odds=1.72,
            under_odds=2.05,
            start_time="2026-05-04T23:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    by_bookmaker = {row.bookmaker_id: row for row in normalized}
    assert len({row.match_id for row in normalized}) == 1
    assert all(row.home_team == "CA Ciudad Nueva" for row in normalized)
    assert all(row.away_team == "Felix Perez Cardozo" for row in normalized)
    assert by_bookmaker["admiralbet"].threshold == -16.5
    assert by_bookmaker["admiralbet"].over_odds == 2.05
    assert by_bookmaker["admiralbet"].under_odds == 1.72


def test_normalize_odds_resolves_suffix_jr():
    """W.Carter and Wendell Carter Jr should match."""
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Wendell Carter Jr",
            threshold=12.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="W.Carter",
            threshold=12.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert [offer.player_name for offer in normalized] == [
        "Wendell Carter Jr",
        "Wendell Carter Jr",
    ]


def test_normalize_odds_resolves_suffix_kelly_oubre_jr():
    """K.Oubre and Kelly Oubre Jr should match."""
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Kelly Oubre Jr",
            threshold=15.5,
            over_odds=1.80,
            under_odds=1.90,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="K.Oubre",
            threshold=15.5,
            over_odds=1.75,
            under_odds=1.95,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert [offer.player_name for offer in normalized] == [
        "Kelly Oubre Jr",
        "Kelly Oubre Jr",
    ]


def test_normalize_odds_resolves_full_name_with_and_without_suffix():
    """Wendell Carter (no suffix) and Wendell Carter Jr (with suffix) must merge.

    Identical first names ARE the strongest signal that two surface forms
    refer to the same player — the contextual resolver must not bail out
    just because ``"wendell".startswith("wendell")`` is technically true.
    """
    raw = [
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Wendell Carter",
            threshold=12.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Wendell Carter Jr",
            threshold=12.5,
            over_odds=1.80,
            under_odds=1.90,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Wendell Carter Jr",
            threshold=12.5,
            over_odds=1.78,
            under_odds=1.92,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert [offer.player_name for offer in normalized] == [
        "Wendell Carter Jr",
        "Wendell Carter Jr",
        "Wendell Carter Jr",
    ]


def test_normalize_odds_resolves_full_name_with_and_without_suffix_iii():
    """Marvin Bagley (no suffix) and Marvin Bagley III (with suffix) must merge."""
    raw = [
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Marvin Bagley",
            threshold=10.5,
            over_odds=1.90,
            under_odds=1.80,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Marvin Bagley III",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Marvin Bagley III",
            threshold=10.5,
            over_odds=1.83,
            under_odds=1.87,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    assert [offer.player_name for offer in normalized] == [
        "Marvin Bagley III",
        "Marvin Bagley III",
        "Marvin Bagley III",
    ]


def test_resolve_contextual_player_name_variants_keeps_ambiguous_prefix_distinct():
    """Different first-name tokens that happen to be in a true prefix
    relation (Jo/John, Steve/Steven) must still NOT merge — that's the
    ambiguity the directional gate was designed to protect."""
    counts_jo = Counter(["Jo Smith", "John Smith", "John Smith"])
    counts_steve = Counter(["Steve Curry", "Steven Curry", "Steven Curry"])
    assert _resolve_contextual_player_name_replacements(counts_jo) == {}
    assert _resolve_contextual_player_name_replacements(counts_steve) == {}


def test_resolve_contextual_player_name_variants_hyphenated_first_with_suffix():
    """Hyphenated first name with vs. without a Jr suffix must still merge.

    The directional gate compares letter-sequence parts (split on hyphens)
    to keep the comparison symmetric: ``"karl-anthony"`` raw token would
    spuriously trip the prefix bail against ``"karl"`` (the first split
    component), so both sides must be derived from the letter sequence.
    """
    counts = Counter(
        ["Mary-Anne Smith", "Mary-Anne Smith Jr", "Mary-Anne Smith Jr"]
    )
    assert _resolve_contextual_player_name_replacements(counts) == {
        "Mary-Anne Smith": "Mary-Anne Smith Jr",
    }


def test_normalize_odds_resolves_reversed_name_order():
    """Edgecombe VJ and VJ Edgecombe should match."""
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="VJ Edgecombe",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Edgecombe VJ",
            threshold=10.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = [offer.player_name for offer in normalized]
    # Both should resolve to the same name
    assert len(set(names)) == 1
    assert names[0] in ("VJ Edgecombe", "Edgecombe VJ")


def test_normalize_odds_resolves_three_variant_reversed_names():
    """Production regression: Edgecombe VJ + V.Edgecombe + VJ Edgecombe must
    converge. Previously the three-way mix tripped the literal first-token
    diversity guard (``edgecombe`` vs ``vj`` vs ``v``) and bailed without
    merging anything."""
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="V.Edgecombe",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="merkurxtip",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Edgecombe VJ",
            threshold=10.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="VJ Edgecombe",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"VJ Edgecombe"}


def test_normalize_odds_resolves_multi_initial_against_hyphenated_name():
    """Production regression: ``K.A.Towns`` and ``Karl-Anthony Towns`` describe
    the same NBA player. The multi-initial form must align position-by-position
    against the hyphenated full name (K↔Karl, A↔Anthony) and merge into the
    fuller variant."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="K.A.Towns",
            threshold=22.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Karl-Anthony Towns",
            threshold=22.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Karl-Anthony Towns"}


def test_normalize_odds_resolves_three_variant_partial_initial_alongside_full_name():
    """Production regression: in the actual Knicks vs 76ers scrape the bucket
    contained THREE Towns surfaces — ``K.Towns`` (mozzart, single initial),
    ``K.A.Towns`` (balkanbet/volcanobet, multi-initial), and
    ``Karl-Anthony Towns`` (everyone else). Before the diversity-collapse fix
    the multi-initial form stayed split because ``("k",)`` and
    ``("karl","anthony")`` looked like two distinct identities to the
    candidate-diversity guard. All three surfaces must collapse into the fuller
    variant."""
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="K.Towns",
            threshold=22.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="K.A.Towns",
            threshold=22.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="volcanobet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="K.A.Towns",
            threshold=22.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Karl-Anthony Towns",
            threshold=22.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Karl-Anthony Towns",
            threshold=22.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Karl-Anthony Towns",
            threshold=22.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Karl-Anthony Towns"}


def test_normalize_odds_keeps_three_variant_bucket_distinct_when_no_count_majority():
    """Conservative tie-break regression: with three structurally-related
    variants (``C.J.``, ``C.J.K.``, ``Christopher James``) at equal count, no
    merge happens. The diversity collapse stops short because ``("c","j","k")``
    has no length-mismatch collapse path with ``("christopher","james")``: the
    length-mismatch rule only fires when the SHORTER side is all single-letter
    (the abbreviation), and here the shorter sequence
    ``("christopher","james")`` is full-name. Two distinct fingerprints
    survive, the diversity guard bails for raw=``C.J.``, and the ranking
    tie-break holds the other two raws steady at equal count."""
    raw = [
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=18.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="C.J.K. McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Christopher James McCollum",
            threshold=18.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {
        "C.J. McCollum",
        "C.J.K. McCollum",
        "Christopher James McCollum",
    }


def test_normalize_odds_keeps_full_first_name_versus_different_full_name_distinct():
    """Regression for the diversity-collapse fix: when the bucket contains a
    short abbreviation alongside *two* different full-name candidates whose
    first-name tokens are themselves distinct (e.g., ``Joey Adam`` vs
    ``Jerry Allen``), the resolver must not pick a winner. The two full names
    don't collapse into each other (per-position prefix fails) and the
    abbreviation can't unilaterally choose between them."""
    raw = [
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="J. Doe",
            threshold=12.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Joey Adam Doe",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nba",
            home_team="New York Knicks",
            away_team="Philadelphia 76ers",
            market_type="player_points",
            player_name="Jerry Allen Doe",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-05-05T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"J. Doe", "Joey Adam Doe", "Jerry Allen Doe"}


def test_normalize_odds_refuses_contraction_when_bucket_carries_rival_extension():
    """Round-2 review regression: under the relaxed abbreviation rules, a
    bucket of ``{C.(5), C.J.(1), C.K.(1)}`` could silently merge BOTH
    ``C.J.`` and ``C.K.`` into ``C.`` because the diversity guard only sees
    each raw's own candidates — ``C.J.`` and ``C.K.`` never appear as
    candidates for each other (their position-1 first-name initials diverge),
    and each independently picks ``C.`` as best with no rival in sight. The
    rival-extension guard inside ``_resolve_contextual_player_name_replacements``
    catches that case: when ``best`` is a strict-shorter all-single-letter
    contraction of ``raw``, the resolver scans the bucket for any other
    surface that is a structural extension of ``best`` but incompatible with
    ``raw``. Here ``C.K.`` is a rival extension of ``C.`` from ``C.J.``'s
    perspective (and vice-versa), so neither contraction fires and all three
    surfaces stay distinct."""
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="admiralbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.J. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Chicago Bulls",
            away_team="New York Knicks",
            market_type="player_points",
            player_name="C.K. McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = sorted({offer.player_name for offer in normalized})
    assert names == ["C. McCollum", "C.J. McCollum", "C.K. McCollum"]


def test_normalize_odds_does_not_merge_different_players_with_swapped_tokens():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="James Jordan",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Jordan James",
            threshold=10.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "James Jordan",
        "Jordan James",
    ]


def test_normalize_odds_does_not_merge_three_letter_swapped_names():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Leo Grant",
            threshold=8.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="Grant Leo",
            threshold=8.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "Leo Grant",
        "Grant Leo",
    ]


def test_normalize_odds_does_not_merge_uppercase_three_letter_swapped_names():
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="LEO GRANT",
            threshold=7.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T00:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="Philadelphia 76ers",
            away_team="Orlando Magic",
            market_type="player_points",
            player_name="GRANT LEO",
            threshold=7.5,
            over_odds=1.7,
            under_odds=2.0,
            start_time="2026-04-13T00:30:00+00:00",
        ),
    ]

    normalized = normalize_odds(raw)

    assert [offer.player_name for offer in normalized] == [
        "LEO GRANT",
        "GRANT LEO",
    ]
