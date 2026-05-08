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
    create_canonical_team,
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
    assert normalize_team_name("Hapoel TA", "euroleague") == "Hapoel Tel-Aviv"
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
    assert "Dropping 2 unresolved shared-platform props for Borac" in warning_messages[0]


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


def test_normalize_odds_prefers_same_bookmaker_anchor_over_two_team_inference(
    team_registry_file,
):
    create_canonical_team(display_name="Hapoel TA", sport="basketball")
    create_canonical_team(display_name="Borac", sport="basketball")
    start_time = "2026-05-07T18:00:00+00:00"

    normalized, unresolved = normalize_odds_with_issues(
        [
            RawOddsData(
                bookmaker_id="balkanbet",
                league_id="euroleague",
                home_team="Hapoel Tel-Aviv",
                away_team="Real Madrid",
                market_type="game_total_ot",
                threshold=170.5,
                over_odds=1.9,
                under_odds=1.8,
                start_time=start_time,
            ),
            RawOddsData(
                bookmaker_id="balkanbet",
                league_id="balkanbet_tournament_29227",
                home_team="Hapoel TA",
                away_team="Elijah Bryant",
                market_type="player_points",
                player_name="Elijah Bryant",
                threshold=14.5,
                over_odds=1.78,
                under_odds=1.93,
                start_time=start_time,
            ),
            RawOddsData(
                bookmaker_id="balkanbet",
                league_id="balkanbet_tournament_29227",
                home_team="Borac",
                away_team="P. Nikolic",
                market_type="player_points",
                player_name="P. Nikolic",
                threshold=15.5,
                over_odds=1.8,
                under_odds=1.9,
                start_time=start_time,
            ),
        ]
    )

    offers_by_player = {offer.player_name: offer for offer in normalized if offer.player_name}
    hapoel_offer = offers_by_player["Elijah Bryant"]
    assert hapoel_offer.home_team == "Hapoel Tel-Aviv"
    assert hapoel_offer.away_team == "Real Madrid"
    assert hapoel_offer.league_id == "euroleague"
    assert "P. Nikolic" not in offers_by_player
    assert {
        (offer.home_team, offer.away_team)
        for offer in normalized
    } == {("Hapoel Tel-Aviv", "Real Madrid")}
    assert any(
        row.raw_team_name == "Borac"
        and row.reason_code == "no_canonical_matchup_for_team_at_slot"
        for row in unresolved
    )


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


def test_normalize_odds_resolves_reviewed_basketball_split_aliases_to_same_match():
    start_time = "2026-05-08T07:30:00+00:00"
    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nbl",
            home_team="Franklin",
            away_team="Nelson",
            market_type="moneyline",
            threshold=0.0,
            home_odds=1.75,
            away_odds=2.05,
            start_time=start_time,
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nbl",
            home_team="Franklin Bulls",
            away_team="Nelson Giants",
            market_type="moneyline",
            threshold=0.0,
            home_odds=1.80,
            away_odds=2.00,
            start_time=start_time,
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 2
    assert {offer.home_team for offer in normalized} == {"Franklin Bulls"}
    assert {offer.away_team for offer in normalized} == {"Nelson Giants"}
    assert len({offer.match_id for offer in normalized}) == 1


def test_normalize_odds_keeps_basketball_conflicting_opponent_split_separate(
    team_registry_file,
):
    start_time = "2026-05-08T16:00:00+00:00"
    for team in ("Bnei Herzliya", "Elitzur Yavne", "Ironi Kiryat Ata"):
        create_canonical_team(display_name=team, sport="basketball")

    raw = [
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="israel",
            home_team="Bnei Herzliya",
            away_team="Elitzur Yavne",
            market_type="moneyline",
            threshold=0.0,
            home_odds=1.75,
            away_odds=2.05,
            start_time=start_time,
        ),
        RawOddsData(
            bookmaker_id="superbet",
            league_id="israel",
            home_team="Bnei Herzliya",
            away_team="Ironi Kiryat Ata",
            market_type="moneyline",
            threshold=0.0,
            home_odds=1.80,
            away_odds=2.00,
            start_time=start_time,
        ),
    ]

    normalized = normalize_odds(raw)

    assert len(normalized) == 2
    assert len({offer.match_id for offer in normalized}) == 2


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


def test_normalize_odds_resolves_shared_platform_props_after_seeded_club_aliases(
    team_registry_file,
):
    raw = [
        RawOddsData(
            bookmaker_id="meridian",
            league_id="champions_league",
            home_team="Rytas Vilnius",
            away_team="CB 1939 Canarias",
            market_type="game_total_ot",
            threshold=164.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="champions_league",
            home_team="Lietuvos Rytas",
            away_team="Tenerife",
            market_type="game_total_ot",
            threshold=165.5,
            over_odds=1.9,
            under_odds=1.86,
            start_time="2026-05-07T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="balkanbet_tournament_123",
            home_team="Tenerife",
            away_team="Marcelinho Huertas",
            market_type="player_points",
            player_name="Marcelinho Huertas",
            threshold=9.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-07T16:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="meridian",
            league_id="champions_league",
            home_team="AEK Athens",
            away_team="Unicaja Malaga",
            market_type="game_total_ot",
            threshold=159.5,
            over_odds=1.9,
            under_odds=1.8,
            start_time="2026-05-07T17:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="champions_league",
            home_team="BC AEK Athens",
            away_team="CB Malaga",
            market_type="game_total_ot",
            threshold=160.5,
            over_odds=1.87,
            under_odds=1.87,
            start_time="2026-05-07T17:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="balkanbet_tournament_456",
            home_team="Unicaja",
            away_team="Kendrick Perry",
            market_type="player_points",
            player_name="Kendrick Perry",
            threshold=11.5,
            over_odds=1.86,
            under_odds=1.86,
            start_time="2026-05-07T17:00:00+00:00",
        ),
    ]

    normalized, unresolved = normalize_odds_with_issues(raw)

    assert unresolved == []
    assert len(normalized) == 6
    offers_by_player = {offer.player_name: offer for offer in normalized if offer.player_name}
    assert offers_by_player["Marcelinho Huertas"].home_team == "Rytas"
    assert offers_by_player["Marcelinho Huertas"].away_team == "Tenerife"
    assert offers_by_player["Kendrick Perry"].home_team == "AEK Athens"
    assert offers_by_player["Kendrick Perry"].away_team == "Unicaja"
    assert {
        offer.match_id
        for offer in normalized
        if offer.start_time == "2026-05-07T16:00:00+00:00"
    } == {offers_by_player["Marcelinho Huertas"].match_id}
    assert {
        offer.match_id
        for offer in normalized
        if offer.start_time == "2026-05-07T17:00:00+00:00"
    } == {offers_by_player["Kendrick Perry"].match_id}


def test_normalize_odds_repairs_balkanbet_split_feed_prop_start_time(
    team_registry_file,
):
    create_canonical_team(display_name="KK TFT Skopje", sport="basketball")
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="KK Borac Cacak",
            away_team="KK TFT Skopje",
            market_type="game_total_ot",
            threshold=164.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T18:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="Borac",
            away_team="P.Nikolic",
            market_type="player_points",
            player_name="P.Nikolic",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-07T18:00:00+00:00",
        ),
    ]

    normalized, unresolved = normalize_odds_with_issues(raw)

    assert unresolved == []
    assert len(normalized) == 2
    prop_offer = next(offer for offer in normalized if offer.player_name == "P.Nikolic")
    assert prop_offer.home_team == "Borac"
    assert prop_offer.away_team == "KK TFT Skopje"
    assert prop_offer.start_time == "2026-05-07T18:30:00+00:00"
    assert {offer.match_id for offer in normalized} == {prop_offer.match_id}


def test_normalize_odds_keeps_balkanbet_split_feed_prop_unresolved_with_multiple_nearby_games(
    team_registry_file,
):
    create_canonical_team(display_name="KK TFT Skopje", sport="basketball")
    create_canonical_team(display_name="Split Feed Rival", sport="basketball")
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="KK Borac Cacak",
            away_team="KK TFT Skopje",
            market_type="game_total_ot",
            threshold=164.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T18:15:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="KK Borac Cacak",
            away_team="Split Feed Rival",
            market_type="game_total_ot",
            threshold=150.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T18:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="Borac",
            away_team="P.Nikolic",
            market_type="player_points",
            player_name="P.Nikolic",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-07T18:00:00+00:00",
        ),
    ]

    normalized, unresolved = normalize_odds_with_issues(raw)

    assert len(normalized) == 2
    assert all(offer.player_name is None for offer in normalized)
    assert len(unresolved) == 1
    assert unresolved[0].raw_team_name == "Borac"
    assert unresolved[0].reason_code == "no_canonical_matchup_for_team_at_slot"


def test_normalize_odds_does_not_repair_balkanbet_prop_to_earlier_game_start(
    team_registry_file,
):
    create_canonical_team(display_name="KK TFT Skopje", sport="basketball")
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="KK Borac Cacak",
            away_team="KK TFT Skopje",
            market_type="game_total_ot",
            threshold=164.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T17:45:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="Borac",
            away_team="P.Nikolic",
            market_type="player_points",
            player_name="P.Nikolic",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-07T18:00:00+00:00",
        ),
    ]

    normalized, unresolved = normalize_odds_with_issues(raw)

    assert len(normalized) == 1
    assert normalized[0].player_name is None
    assert len(unresolved) == 1
    assert unresolved[0].raw_team_name == "Borac"
    assert unresolved[0].reason_code == "no_canonical_matchup_for_team_at_slot"


def test_normalize_odds_does_not_repair_balkanbet_prop_without_same_bookmaker_game_market(
    team_registry_file,
):
    create_canonical_team(display_name="KK TFT Skopje", sport="basketball")
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="KK Borac Cacak",
            away_team="KK TFT Skopje",
            market_type="player_points",
            player_name="TFT Player",
            threshold=12.5,
            over_odds=1.88,
            under_odds=1.88,
            start_time="2026-05-07T18:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="aba_liga",
            home_team="Borac",
            away_team="P.Nikolic",
            market_type="player_points",
            player_name="P.Nikolic",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-05-07T18:00:00+00:00",
        ),
    ]

    normalized, unresolved = normalize_odds_with_issues(raw)

    assert len(normalized) == 1
    assert normalized[0].player_name == "TFT Player"
    assert len(unresolved) == 1
    assert unresolved[0].raw_team_name == "Borac"
    assert unresolved[0].reason_code == "no_canonical_matchup_for_team_at_slot"


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


def test_resolve_contextual_player_name_variants_merges_terminal_s_typo_majority():
    counts = Counter(["Andre Feliz", "Andres Feliz", "Andres Feliz", "Andres Feliz"])

    assert _resolve_contextual_player_name_replacements(counts) == {
        "Andre Feliz": "Andres Feliz",
    }


def test_resolve_contextual_player_name_variants_expands_initial_when_terminal_s_variants_compete():
    counts = Counter(
        [
            "A.Feliz",
            "A.Feliz",
            "Andre Feliz",
            "Andres Feliz",
            "Andres Feliz",
            "Andres Feliz",
        ]
    )

    assert _resolve_contextual_player_name_replacements(counts) == {
        "A.Feliz": "Andres Feliz",
        "Andre Feliz": "Andres Feliz",
    }


def test_resolve_contextual_player_name_variants_keeps_initial_terminal_s_candidates_ambiguous_without_majority():
    counts = Counter(["A.Feliz", "Andre Feliz", "Andres Feliz"])

    assert _resolve_contextual_player_name_replacements(counts) == {}


def test_resolve_contextual_player_name_variants_uses_terminal_s_sequence_majority_for_split_surfaces():
    counts = Counter(
        {
            "A.Feliz": 1,
            "Andre Feliz": 5,
            "Andres Feliz": 3,
            "Andres Feliz Jr": 3,
        }
    )

    replacements = _resolve_contextual_player_name_replacements(counts)

    assert replacements["A.Feliz"].startswith("Andres Feliz")
    assert replacements["Andre Feliz"].startswith("Andres Feliz")


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


def test_normalize_odds_falls_through_to_unambiguous_full_name_when_short_best_has_rival():
    """Round-3 review regression (gpt-5.5): when the rival-extension guard
    refuses ``best=C.`` for raw ``C.J.`` because ``C.K.`` is a sibling
    extension, the resolver must fall through to the next-best candidate
    rather than abandon raw entirely. In a mixed bucket
    ``{C.(5), C.J.(1), Cameron John(1), C.K.(1)}`` the unambiguous full-name
    candidate ``Cameron John McCollum`` is in raw=``C.J.``'s candidate list
    too — `_letter_seq_compatible(('c','j'), ('cameron','john'))` passes via
    same-length per-position prefix — and ``C.K.`` is NOT a candidate of
    ``C.J.`` (mismatched at position 1), so ``Cameron John`` is unambiguous
    from raw=``C.J.``'s perspective. Falling through, ``C.J.`` merges into
    ``Cameron John McCollum``. ``C.`` (the original count majority) stays
    distinct because, from raw=``C.``'s perspective, both ``C.J.`` and
    ``C.K.`` are competing candidates so the diversity guard upstream
    refuses it. ``C.K.`` stays distinct because its only candidate (``C.``)
    has the same rival problem and its only fallback (``Cameron John``)
    fails the same-length per-position prefix check at position 1
    (``'k'`` vs ``'john'``)."""
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
            player_name="Cameron John McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="volcanobet",
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
    assert names == ["C. McCollum", "C.K. McCollum", "Cameron John McCollum"]


def test_normalize_odds_treats_same_length_full_name_as_rival_extension():
    """Round-3 review regression (opus-4.7-xhigh and opus-4.7-1m): the
    rival-extension guard previously skipped any candidate with a same-length
    letter-sequence as ``best`` (filter was ``len(other_seq) <= len(best_seq)``).
    A single-token full name like ``Carl`` (``letter-sequence = ('carl',)``)
    was therefore never tested as a rival of ``best=C.``
    (``letter-sequence = ('c',)``), even though ``Carl`` is just as plausible
    an expansion of ``C.`` as ``C.J.`` is. A bucket of
    ``{C.(5), C.J.(1), Carl(1)}`` then silently merged ``C.J. → C.``. The fix
    admits same-length-but-different-content rivals (skipping only sequences
    strictly shorter than ``best_seq`` and exact duplicates of it). ``Carl``
    is now flagged as a rival of ``C.`` from ``C.J.``'s perspective via
    ``_letter_seq_collapse_compatible(('carl',), False, ('c','j'), True)``,
    which returns False (length mismatch with shorter side ``('carl',)`` not
    all-single-letter), so the merge ``C.J. → C.`` is refused. ``C.J.`` has
    no other compatible candidate to fall through to; ``Carl`` itself is
    rejected by ``_letter_seq_compatible`` as a candidate of ``C.J.`` for the
    same structural reason. ``C.`` bails upstream because the diversity guard
    sees ``('c','j')`` and ``('carl',)`` as non-collapsing fingerprints.
    ``Carl`` bails because the directional gate catches the ``Carl → C.``
    prefix relation between non-abbreviated raws."""
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
            player_name="Carl McCollum",
            threshold=15.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-11T01:30:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = sorted({offer.player_name for offer in normalized})
    assert names == ["C. McCollum", "C.J. McCollum", "Carl McCollum"]


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


def test_normalize_odds_merges_compound_surname_with_hyphen_space_variant():
    """Production regression: ``Shai Gilgeous-Alexander`` (hyphen) and
    ``S. Gilgeous Alexander`` (space) describe the same NBA player. One
    bookmaker emits the hyphenated surname; another drops the hyphen and
    emits ``Gilgeous`` as if it were a middle name. ``_player_name_parts``
    parses the two surfaces with different surname tokens
    (``gilgeous-alexander`` vs ``alexander``), so without compound-surname
    awareness in the resolver they never even reach the first-name match
    step."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="S. Gilgeous Alexander",
            threshold=29.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Shai Gilgeous-Alexander"}


def test_normalize_odds_merges_surname_particle_variant():
    """Production regression: ``A.St.Brown`` (one bookmaker) and
    ``Amon-Ra St. Brown`` (another) describe the same player. ``St`` is a
    surname particle that the simple parser leaves in the first-name token
    list (``first=['a','st'], last='brown'`` and
    ``first=['amon-ra','st'], last='brown'``). Without compound-surname
    awareness the resolver tries to align ``('a','st')`` with
    ``('amon','ra','st')``, fails, and the merge is missed."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Indiana Pacers",
            market_type="player_points",
            player_name="A.St.Brown",
            threshold=12.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Indiana Pacers",
            market_type="player_points",
            player_name="Amon-Ra St. Brown",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Detroit Pistons",
            away_team="Indiana Pacers",
            market_type="player_points",
            player_name="Amon-Ra St. Brown",
            threshold=12.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Amon-Ra St. Brown"}


def test_normalize_odds_keeps_legitimate_particle_first_name_intact():
    """Round-1 review regression (gpt-5.5): the particle-pull pass in
    ``_resolver_player_parts`` originally fired even when the particle was
    the player's actual first name. ``Van Jefferson`` (Lakers WR-turned-NBA
    fan-favorite, but the example structurally applies to any
    ``Van X`` / ``Mac X`` / ``Mc X`` name) was being parsed as
    ``first=[], last='van jefferson'`` and lost its merge with the
    abbreviated ``V. Jefferson`` surface. The guard now requires at least
    one given-name token to remain after the pull, so ``Van Jefferson``
    stays parsed as ``first=['van'], last='jefferson'`` and merges
    normally."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Los Angeles Lakers",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="V. Jefferson",
            threshold=8.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Los Angeles Lakers",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Van Jefferson",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Los Angeles Lakers",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Van Jefferson",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Van Jefferson"}


def test_normalize_odds_does_not_consume_full_name_into_unrelated_compound_hint():
    """Round-1 review regression (gpt-5.5): the multi-token surname
    expansion in ``_resolver_player_parts`` originally allowed
    ``n == len(full_tokens)``, so a hint harvested from one bucket member
    could re-parse another player's normal two-token name as
    ``first=[], last='full name'`` and break that player's legitimate
    abbreviation merge. Specifically: a bucket of
    ``{J. Paul, John Paul, Alice John-Paul}`` would harvest the hint
    ``'john paul'`` from ``Alice John-Paul`` (her hyphenated surname folds
    to two tokens), and then ``John Paul`` (a different player) would
    re-parse as ``first=[], last='john paul'``, killing the merge
    ``J. Paul → John Paul``. The guard now requires the expansion to leave
    at least one given-name token, so ``John Paul`` stays parsed as
    ``first=['john'], last='paul'`` and merges normally with ``J. Paul``.
    ``Alice John-Paul`` remains a distinct identity (different surname
    after fold)."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="J. Paul",
            threshold=8.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="John Paul",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="John Paul",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Alice John-Paul",
            threshold=4.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = sorted({offer.player_name for offer in normalized})
    assert names == ["Alice John-Paul", "John Paul"]


def test_normalize_odds_pulls_chained_surname_particles():
    """Particle pull iterates while the trailing first-name token stays a
    surname particle. ``A. de la Cruz`` and ``Anna de la Cruz`` parse as
    ``(['a','de','la'], 'cruz')`` and ``(['anna','de','la'], 'cruz')``.
    Pulling only the trailing ``la`` would leave ``de`` in the first-name
    list on both sides — the surnames would still match (``la cruz``) but
    the comparison would have to align ``('a','de')`` with
    ``('anna','de')`` instead of the cleaner single-position comparison.
    Iterative pull leaves ``first=['a'], last='de la cruz'`` and
    ``first=['anna'], last='de la cruz'`` on both sides so the merge
    works on the abbreviation alone."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="A. de la Cruz",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Anna de la Cruz",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Anna de la Cruz",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Anna de la Cruz"}


def test_normalize_odds_picks_full_form_canonical_in_compound_surname_count_tie():
    """Round-2 review regression (opus-4.7-xhigh): the resolver's
    completeness ranking previously called the un-wrapped
    ``_player_name_parts`` to feed ``_player_name_completeness``. For the
    abbreviated surface ``S. Gilgeous Alexander`` the un-wrapper returned
    ``first_tokens=['s','gilgeous']``, so the completeness score counted
    ``gilgeous`` as a given-name token (8 chars) — outranking the proper
    full form ``Shai Gilgeous-Alexander`` (whose first_tokens=['shai']
    scores 4) on the completeness tiebreaker. In count-tied buckets the
    abbreviated surface was therefore picked as the canonical merge target,
    which is exactly the opposite of what the rank is supposed to express.

    Routing both ``_candidate_first_for_completeness`` and
    ``best_parts`` through ``_resolver_player_parts(name,
    compound_surname_hints=...)`` aligns the completeness measure with the
    parse used for matching: the abbreviated surface contributes
    ``first_tokens=['s']`` (completeness 0) and the full form contributes
    ``first_tokens=['shai']`` (completeness 4), so the full form wins the
    tiebreaker."""
    raw = []
    for bm in ("balkanbet", "365"):
        raw.append(
            RawOddsData(
                bookmaker_id=bm,
                league_id="nba",
                home_team="Oklahoma City Thunder",
                away_team="Memphis Grizzlies",
                market_type="player_points",
                player_name="S. Gilgeous Alexander",
                threshold=29.5,
                over_odds=1.85,
                under_odds=1.85,
                start_time="2026-04-13T01:00:00+00:00",
            )
        )
    for bm in ("mozzart", "maxbet"):
        raw.append(
            RawOddsData(
                bookmaker_id=bm,
                league_id="nba",
                home_team="Oklahoma City Thunder",
                away_team="Memphis Grizzlies",
                market_type="player_points",
                player_name="Shai Gilgeous-Alexander",
                threshold=29.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-13T01:00:00+00:00",
            )
        )
    raw.append(
        RawOddsData(
            bookmaker_id="superbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Sh. Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        )
    )
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Shai Gilgeous-Alexander"}


def test_resolver_player_parts_iterative_pull_handles_asymmetric_chain_depths():
    """Round-2 review note (gpt-5.5): the previous chained-particles test
    used symmetrically-shaped surfaces, so a non-iterative single-pull
    would happen to leave both sides parsed as
    ``(['anna','de'], 'la cruz')`` and ``(['a','de'], 'la cruz')`` — both
    with surname ``la cruz`` and matching trailing ``de`` first-name
    tokens — and the merge would pass even without iteration. This test
    exercises the iterative pull directly with an asymmetric pair: one
    surface has the compound surname space-separated (``A. de la Cruz``),
    the other has it hyphenated (``Anna de-la-Cruz``). The hyphenated
    surface parses straight to ``last='de la cruz'`` (one token, then
    folded). The space surface needs both ``la`` and ``de`` pulled to
    align — a single pull would leave it as
    ``(['a','de'], 'la cruz')`` and the surnames ``la cruz`` and
    ``de la cruz`` would not match."""

    from app.services.normalizer import _resolver_player_parts

    space = _resolver_player_parts("A. de la Cruz")
    hyphen = _resolver_player_parts("Anna de-la-Cruz")
    assert space == (["a"], "de la cruz")
    assert hyphen == (["anna"], "de la cruz")

    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="A. de la Cruz",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Anna de-la-Cruz",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Anna de-la-Cruz",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Anna de-la-Cruz"}


def test_normalize_odds_compound_hint_does_not_steal_middle_name_token():
    """Round-3 review regression (gpt-5.5): the multi-token expansion in
    ``_resolver_player_parts`` was originally invoked unconditionally through
    `_try_contextual_player_match`, so an unrelated bucket member's
    compound-surname hint could destructively reparse a bystander's middle-
    name token. With ``Alice John-Paul`` in the bucket contributing the hint
    ``'john paul'``, the surface ``Mary John Paul`` was being reparsed as
    ``first=['mary'], last='john paul'`` — turning ``john`` from a middle
    name into part of the surname — and the legitimate
    ``M.J. Paul → Mary John Paul`` merge stopped firing.

    The conservative-then-hint guard in `_try_contextual_player_match` only
    consults the bucket-wide hints when the simple particle/fold parse leaves
    surnames mismatched. ``Mary John Paul`` and ``M.J. Paul`` already share
    ``last='paul'`` under the simple parse, so the hint stays inert for that
    pair and the abbreviation merge proceeds. ``Alice John-Paul`` keeps her
    distinct identity (different first name, surname only matches under
    hint expansion which is gated by first-name compatibility downstream)."""

    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="M.J. Paul",
            threshold=8.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Mary John Paul",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Mary John Paul",
            threshold=8.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="maxbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Alice John-Paul",
            threshold=4.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = sorted({offer.player_name for offer in normalized})
    assert names == ["Alice John-Paul", "Mary John Paul"]


def test_normalize_odds_count_tied_compound_surname_pair_does_not_cycle():
    """Round-3 review regression (opus-4.7-1m): the existing
    `..._picks_full_form_canonical_in_compound_surname_count_tie` test was
    found to be a trivial-pass for the round-2 completeness fix because the
    full-name ``Shai Gilgeous-Alexander`` rows in that bucket let
    `_final_contextual_player_name` walk through any wrong intermediate
    replacement and still terminate on the same canonical surface.

    This bucket is the *discriminating* regression: only the abbreviated
    ``S. Gilgeous Alexander`` and the dotted ``Sh. Gilgeous-Alexander``
    surfaces, both at count 2. Under the pre-round-2 completeness ranking
    (un-wrapped `_player_name_parts`), ``S.`` scores completeness 8
    (counting ``gilgeous`` as a given-name token) and ``Sh.`` scores 2 — so
    for raw ``Sh.`` the resolver picks ``S.`` as best (`Sh. → S.`), and for
    raw ``S.`` the resolver picks ``Sh.`` as best (`S. → Sh.`). The two
    replacements form a cycle that ``_final_contextual_player_name`` short-
    circuits by simply *swapping* the names, leaving both surfaces in the
    output (no merge).

    Under the round-2 completeness fix (wrapped `_resolver_player_parts`),
    both candidates score completeness 2 (``['s']`` and ``['sh']``
    respectively), so the count tie is resolved by the wrapper-aligned
    completeness measure plus the rank's tertiary length tiebreaker —
    ``Sh. Gilgeous-Alexander`` wins for raw ``S.``, and the resolver
    refuses the reverse direction for raw ``Sh.`` because best's
    completeness is no longer above raw's. The bucket merges to a single
    canonical surface."""
    raw = []
    for bm in ("balkanbet", "365"):
        raw.append(
            RawOddsData(
                bookmaker_id=bm,
                league_id="nba",
                home_team="Oklahoma City Thunder",
                away_team="Memphis Grizzlies",
                market_type="player_points",
                player_name="S. Gilgeous Alexander",
                threshold=29.5,
                over_odds=1.85,
                under_odds=1.85,
                start_time="2026-04-13T01:00:00+00:00",
            )
        )
    for bm in ("mozzart", "maxbet"):
        raw.append(
            RawOddsData(
                bookmaker_id=bm,
                league_id="nba",
                home_team="Oklahoma City Thunder",
                away_team="Memphis Grizzlies",
                market_type="player_points",
                player_name="Sh. Gilgeous-Alexander",
                threshold=29.5,
                over_odds=1.9,
                under_odds=1.9,
                start_time="2026-04-13T01:00:00+00:00",
            )
        )
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Sh. Gilgeous-Alexander"}


def test_normalize_odds_merges_reversed_compound_surname_with_full_form():
    """Round-4 review regression (gpt-5.5): the reversed-name branches in
    `_try_contextual_player_match` (raw-swap and candidate-swap) constructed
    the swapped surname directly from the un-folded
    ``raw_first_tokens[0]`` / ``candidate_first_tokens[0]`` token, so a
    hyphenated compound surname presented in reversed order
    (``Gilgeous-Alexander S.``) compared as ``gilgeous-alexander`` against
    a non-reversed candidate's already-folded ``gilgeous alexander``. The
    surname-equality check in `_check_first_name_match` is verbatim, so
    the swap path silently failed. This regressed a very plausible
    bookmaker surface — some scrapers emit ``Surname F.`` / ``Surname-Last
    F.`` for player props, and combining that with another scraper's full
    ``Shai Gilgeous-Alexander`` form in the same event bucket would have
    left the abbreviated reversed form unmerged.

    The fix folds the swapped surname (`_fold_surname(...)`) symmetrically
    in both reversed branches before passing it to
    `_check_first_name_match`."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Gilgeous-Alexander S.",
            threshold=29.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Shai Gilgeous-Alexander"}


def test_normalize_odds_merges_space_separated_reversed_compound_surname():
    """Round-5 review regression (gpt-5.5): the round-4 fold fix only
    handled the reversed-name path when the pre-abbreviation side parses as
    exactly one ``first_token`` — covering hyphenated reversed compounds
    like ``Gilgeous-Alexander S.`` but missing the same surname emitted in
    space-separated form (``Gilgeous Alexander S.``). The space form parses
    as multiple ``first_tokens=['gilgeous','alexander']`` so the
    ``len(raw_first_tokens) == 1`` guard skipped the reversed branch
    entirely and the variant remained unmerged.

    The fix admits a multi-token reversed surname when the joined+folded
    form matches a bucket-context compound-surname hint. ``Gilgeous-
    Alexander`` (hyphenated) contributes the hint ``'gilgeous alexander'``,
    and the space-separated reversed surface ``Gilgeous Alexander S.``
    is now reparsed against that hint as
    ``surname='gilgeous alexander'``, ``first=['s']`` so the merge with
    ``Shai Gilgeous-Alexander`` proceeds via the existing reversed-match
    machinery."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Gilgeous Alexander S.",
            threshold=29.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="Oklahoma City Thunder",
            away_team="Memphis Grizzlies",
            market_type="player_points",
            player_name="Shai Gilgeous-Alexander",
            threshold=29.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T01:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"Shai Gilgeous-Alexander"}


def test_normalize_odds_merges_multi_particle_space_separated_reversed_compound():
    """Round-5 review regression (gpt-5.5): same defect class as
    `test_normalize_odds_merges_space_separated_reversed_compound_surname`,
    but with a multi-particle compound surname (``Van Der Berg``). The
    space-separated reversed surface ``Van Der Berg J.`` parses as
    ``first=['van','der','berg'], last='j'`` and only matches the
    hyphenated full form ``John Van-Der-Berg`` once the multi-token
    reversed-surname branch consults the bucket hint
    ``'van der berg'`` (folded from ``van-der-berg``)."""
    raw = [
        RawOddsData(
            bookmaker_id="balkanbet",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="Van Der Berg J.",
            threshold=10.5,
            over_odds=1.85,
            under_odds=1.85,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="mozzart",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="John Van-Der-Berg",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
        RawOddsData(
            bookmaker_id="365",
            league_id="nba",
            home_team="A",
            away_team="B",
            market_type="player_points",
            player_name="John Van-Der-Berg",
            threshold=10.5,
            over_odds=1.9,
            under_odds=1.9,
            start_time="2026-04-13T03:00:00+00:00",
        ),
    ]
    normalized = normalize_odds(raw)
    names = {offer.player_name for offer in normalized}
    assert names == {"John Van-Der-Berg"}
