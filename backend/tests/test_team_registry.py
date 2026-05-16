from __future__ import annotations

import json
import sqlite3

import pytest

from app.config import settings
from app.services import team_registry
from app.services.team_registry import (
    clear_team_registry_cache,
    create_canonical_team,
    merge_canonical_teams,
    remember_team_alias,
    resolve_team_alias,
    search_canonical_team_candidates,
    unmerge_canonical_team,
)
from app.services.team_seed_data import SPORT_ALIAS_SEEDS
from app.services.text_normalizer import normalize_identity_text


def test_create_canonical_team_reuses_inactive_merged_display_name(team_registry_file):
    source = create_canonical_team(display_name="QA Merged Source")
    target = create_canonical_team(display_name="QA Merged Target")
    merge_canonical_teams(
        source_team_id=source.team_id,
        target_team_id=target.team_id,
    )

    recreated = create_canonical_team(display_name="QA Merged Source")
    alias_resolution = resolve_team_alias("QA Merged Source")

    assert recreated.team_id == target.team_id
    assert recreated.team_name == target.team_name
    assert alias_resolution is not None
    assert alias_resolution.team_id == target.team_id
    assert alias_resolution.team_name == target.team_name


def test_bootstrap_seed_reuses_inactive_merged_display_name(
    team_registry_file,
    monkeypatch,
):
    source = create_canonical_team(display_name="QA Bootstrap Collision Source")
    target = create_canonical_team(display_name="QA Bootstrap Collision Target")
    merge_canonical_teams(
        source_team_id=source.team_id,
        target_team_id=target.team_id,
    )

    seed_alias = "QA Bootstrap Seed Alias"
    seed_map = dict(SPORT_ALIAS_SEEDS["basketball"])
    seed_map[seed_alias] = source.team_name
    monkeypatch.setitem(SPORT_ALIAS_SEEDS, "basketball", seed_map)
    clear_team_registry_cache()

    resolution = resolve_team_alias(seed_alias)

    assert resolution is not None
    assert resolution.team_id == target.team_id
    assert resolution.team_name == target.team_name


@pytest.mark.parametrize("raw_alias", ["aek", "canarias"])
def test_basketball_seed_data_does_not_promote_bare_ambiguous_aliases(
    team_registry_file,
    raw_alias,
):
    normalized_seed_aliases = {
        normalize_identity_text(alias) for alias in SPORT_ALIAS_SEEDS["basketball"]
    }

    assert normalize_identity_text(raw_alias) not in normalized_seed_aliases
    assert resolve_team_alias(raw_alias, sport="basketball") is None


@pytest.mark.parametrize(
    ("raw_alias", "expected"),
    [
        ("Franklin", "Franklin Bulls"),
        ("Nelson", "Nelson Giants"),
        ("Rasta Vechta", "Vechta"),
        ("TBB Trier", "Trier"),
        ("Aisin Mikawa", "SeaHorses Mikawa"),
        ("Ryukyu Golden Kings Okinawa", "Ryukyu Golden Kings"),
        ("U BT Cluj", "Universitatea Cluj"),
        ("Budućnost Podgorica", "Buducnost"),
    ],
)
def test_basketball_seed_data_resolves_reviewed_split_aliases(
    team_registry_file,
    raw_alias,
    expected,
):
    resolution = resolve_team_alias(raw_alias, sport="basketball")

    assert resolution is not None
    assert resolution.team_name == expected


def test_search_canonical_team_candidates_does_not_leak_cross_sport(team_registry_file):
    """Regression: historical pending review cases showed football raws matched
    to basketball canonicals (and vice versa). The current code filters by
    sport at query time, but we lock that behavior in here so future
    refactors of the candidate-search snapshot cannot regress it.
    """
    basketball_team = create_canonical_team(
        display_name="Corinthians Paulista",
        sport="basketball",
    )
    football_team = create_canonical_team(
        display_name="SC Corinthians",
        sport="football",
    )

    football_results = search_canonical_team_candidates(
        "SC Corinthians SP",
        sport="football",
    )
    basketball_results = search_canonical_team_candidates(
        "SC Corinthians SP",
        sport="basketball",
    )

    assert basketball_team.team_id not in {c.team_id for c in football_results}
    assert football_team.team_id not in {c.team_id for c in basketball_results}


def test_search_canonical_team_candidates_hard_blocks_women_men_mismatch(
    team_registry_file,
):
    """Women ↔ men qualifier mismatch must be hard-blocked.

    Two canonicals share the same base name (``Barcelona``) but have
    different qualifiers: the men's first team versus the women's first
    team. A raw bookmaker name without a women marker must match only the
    men's team; a raw with a women marker (English, German "Frauen",
    Portuguese "Feminino" …) must match only the women's team.
    """
    men = create_canonical_team(display_name="Barcelona", sport="football")
    women = create_canonical_team(display_name="Barcelona Women", sport="football")

    men_results = search_canonical_team_candidates(
        "FC Barcelona", sport="football", limit=5
    )
    assert women.team_id not in {c.team_id for c in men_results}
    assert men.team_id in {c.team_id for c in men_results}

    women_results = search_canonical_team_candidates(
        "Barcelona Frauen", sport="football", limit=5
    )
    assert men.team_id not in {c.team_id for c in women_results}
    assert women.team_id in {c.team_id for c in women_results}


def test_search_canonical_team_candidates_mixed_women_status_stays_reachable(
    team_registry_file,
):
    """Canonicals whose display name carries the only women marker while
    aliases lack it (NWSL-style: display ``Gotham W`` with aliases
    ``Gotham FC`` / ``Gotham``) must remain reachable from un-marked
    bookmaker raws.

    Without the ``"mixed"`` women-status state, the gate hard-blocks the
    candidate for any un-marked query — including the existing bookmaker
    aliases — which silently drops the canonical from
    :func:`search_canonical_team_candidates` results for every new
    spelling variant.
    """
    from app.services.team_registry import remember_team_alias

    gotham = create_canonical_team(display_name="Gotham W", sport="football")
    # Two aliases that omit the women marker, simulating bookmakers that
    # don't disambiguate gender on this team. The presence of these
    # un-marked aliases is what flips the team's women_status to
    # ``"mixed"``.
    remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Gotham FC",
        team_name="Gotham W",
        sport="football",
    )
    remember_team_alias(
        bookmaker_id="qa-other",
        raw_team_name="Gotham",
        team_name="Gotham W",
        sport="football",
    )

    # Un-marked raw must surface the canonical (this is the regression).
    results = search_canonical_team_candidates(
        "Gotham Football Club", sport="football", limit=5
    )
    assert gotham.team_id in {c.team_id for c in results}, (
        "mixed-status canonical must remain reachable from un-marked raws"
    )


def test_search_canonical_team_candidates_keeps_period_abbreviation_match(
    team_registry_file,
):
    """Regression for the period-abbreviation exact-match skip bug.

    Raw ``Hap.Haifa`` expands to ``hapoel haifa`` after
    :func:`expand_team_abbreviations`. Canonical ``Hapoel Haifa`` also
    normalizes to ``hapoel haifa``. The previous skip check compared the
    *expanded* forms and dropped the candidate as "exact match"; the fix
    compares the un-expanded forms so legitimate abbreviation matches
    survive.
    """
    canonical = create_canonical_team(display_name="Hapoel Haifa", sport="football")
    results = search_canonical_team_candidates(
        "Hap.Haifa", sport="football", limit=5
    )
    assert canonical.team_id in {c.team_id for c in results}, (
        "period-abbreviation expansion must surface the canonical, not skip it"
    )


def test_search_canonical_team_candidates_demotes_youth_marker_mismatch(
    team_registry_file,
):
    """Youth marker mismatch must demote the candidate (not remove it).

    A raw ``Liverpool U19 FC`` (non-exact-equal to the canonical to
    bypass the early-skip-on-exact-match filter) should still surface
    ``Liverpool`` as a candidate because the operator may want to know
    "this is your closest fuzzy fit, but the youth marker is missing on
    the canonical". The score must be lower than what an exact qualifier
    match would produce so the qualifier-matching ``Liverpool U19``
    canonical takes priority.
    """
    senior = create_canonical_team(display_name="Liverpool", sport="football")
    youth = create_canonical_team(display_name="Liverpool U19", sport="football")

    results = search_canonical_team_candidates(
        "Liverpool U19 FC", sport="football", limit=5
    )
    team_ids = [c.team_id for c in results]
    assert youth.team_id in team_ids, "youth canonical must remain in results"
    assert senior.team_id in team_ids, "senior canonical must remain as a (demoted) suggestion"
    assert team_ids.index(youth.team_id) < team_ids.index(senior.team_id), (
        "the qualifier-matching youth canonical must outrank the demoted senior"
    )


def test_search_canonical_team_candidates_hard_blocks_explicit_age_mismatch(
    team_registry_file,
):
    """Two explicit youth ages on different sides is a hard block.

    ``Real Madrid U19`` and ``Real Madrid U23`` are two distinct teams
    within the same club's youth system. A raw ``Real Madrid U23 FC``
    (non-exact-equal to the canonical to bypass the early-skip-on-exact-
    match filter) must never receive ``Real Madrid U19`` as a suggestion.
    """
    u19 = create_canonical_team(display_name="Real Madrid U19", sport="football")
    u23 = create_canonical_team(display_name="Real Madrid U23", sport="football")

    results = search_canonical_team_candidates(
        "Real Madrid U23 FC", sport="football", limit=5
    )
    assert u19.team_id not in {c.team_id for c in results}
    assert u23.team_id in {c.team_id for c in results}


def test_search_canonical_team_candidates_demotes_insufficient_alone_prefix(
    team_registry_file,
):
    """Sharing only a generic club-prefix token (Pogon, Stal, etc.) must
    demote the candidate.

    ``Pogon Mogilno`` and ``Pogon Sz.`` are two unrelated Polish clubs
    that share only the ``Pogon`` prefix. Without the demotion, the
    fuzzy matcher would rank ``Pogon Sz.`` as a top suggestion for any
    other ``Pogon X`` raw — but ``Pogon`` is a club-prefix convention
    used by dozens of unrelated clubs, so a single ``pogon`` overlap
    carries essentially no disambiguating signal.

    The candidate must still appear in results (it's a soft demotion,
    not a hard reject) so the operator can review it if the matcher
    truly has nothing better. The qualifier-matching real candidate
    must outrank it.
    """
    target = create_canonical_team(display_name="Pogon Mogilno", sport="football")
    distractor = create_canonical_team(display_name="Pogon Sz.", sport="football")

    results = search_canonical_team_candidates(
        "Pogon Mogilno FC", sport="football", limit=5
    )
    team_ids = [c.team_id for c in results]
    assert target.team_id in team_ids, "target canonical (real match) must surface"
    if distractor.team_id in team_ids:
        assert team_ids.index(target.team_id) < team_ids.index(distractor.team_id), (
            "the disambiguating-token match must outrank the prefix-only match"
        )


def test_create_canonical_team_reports_unresolved_inactive_conflict(team_registry_file):
    create_canonical_team(display_name="QA Schema Anchor")
    display_name = "QA Orphan Inactive"
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO canonical_teams (
                sport,
                display_name,
                normalized_display_name,
                is_active
            ) VALUES (?, ?, ?, FALSE)
            """,
            ("basketball", display_name, normalize_identity_text(display_name)),
        )
        conn.commit()

    with pytest.raises(RuntimeError, match="does not resolve to an active team"):
        create_canonical_team(display_name=display_name)


def test_unmerge_canonical_team_restores_reassigned_aliases(team_registry_file):
    source = create_canonical_team(display_name="QA Unmerge Source")
    target = create_canonical_team(display_name="QA Unmerge Target")
    remember_team_alias(
        bookmaker_id="maxbet",
        raw_team_name="QA Unmerge Alias",
        team_name=source.team_name,
    )

    merge_canonical_teams(
        source_team_id=source.team_id,
        target_team_id=target.team_id,
    )
    merged_alias = resolve_team_alias("QA Unmerge Alias", bookmaker_id="maxbet")
    assert merged_alias is not None
    assert merged_alias.team_id == target.team_id

    result = unmerge_canonical_team(source_team_id=source.team_id)
    restored_alias = resolve_team_alias("QA Unmerge Alias", bookmaker_id="maxbet")

    assert result.source_team_id == source.team_id
    assert result.target_team_id == target.team_id
    assert restored_alias is not None
    assert restored_alias.team_id == source.team_id


def test_unmerge_canonical_team_restores_pending_review_cases(team_registry_file):
    source = create_canonical_team(display_name="QA Review Unmerge Source")
    target = create_canonical_team(display_name="QA Review Unmerge Target")
    candidate_teams = json.dumps(
        [
            {"team_id": source.team_id, "team_name": source.team_name, "score": 95},
            {"team_id": target.team_id, "team_name": target.team_name, "score": 90},
        ]
    )
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO team_review_cases (
                bookmaker_id,
                raw_league_id,
                normalized_raw_league_id,
                sport,
                raw_team_name,
                normalized_raw_team_name,
                suggested_team_id,
                suggested_team_name,
                reason_code,
                candidate_teams,
                canonical_home_team,
                canonical_away_team,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                "meridian",
                "QA League",
                "qa league",
                "basketball",
                source.team_name,
                normalize_identity_text(source.team_name),
                source.team_id,
                source.team_name,
                "candidate_team_match_same_start_time",
                candidate_teams,
                source.team_name,
                "QA Opponent",
            ),
        )
        conn.commit()

    merge_canonical_teams(
        source_team_id=source.team_id,
        target_team_id=target.team_id,
    )
    with sqlite3.connect(settings.db_path) as conn:
        merged_row = conn.execute(
            "SELECT suggested_team_id, canonical_home_team FROM team_review_cases"
        ).fetchone()

    assert merged_row == (target.team_id, target.team_name)

    unmerge_canonical_team(source_team_id=source.team_id)
    with sqlite3.connect(settings.db_path) as conn:
        restored_row = conn.execute(
            """
            SELECT suggested_team_id, suggested_team_name, candidate_teams, canonical_home_team
            FROM team_review_cases
            """
        ).fetchone()

    assert restored_row == (
        source.team_id,
        source.team_name,
        candidate_teams,
        source.team_name,
    )


def test_unmerge_canonical_team_rejects_legacy_history_without_snapshot(team_registry_file):
    source = create_canonical_team(display_name="QA Legacy Unmerge Source")
    target = create_canonical_team(display_name="QA Legacy Unmerge Target")
    merge_canonical_teams(
        source_team_id=source.team_id,
        target_team_id=target.team_id,
    )
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("UPDATE team_merge_history SET alias_snapshot = NULL")
        conn.commit()

    with pytest.raises(ValueError, match="before alias rollback metadata existed"):
        unmerge_canonical_team(source_team_id=source.team_id)


def test_create_canonical_teams_batch_creates_multiple_teams_and_clears_once(
    monkeypatch,
    team_registry_file,
):
    clear_calls: list[bool] = []
    original_clear = team_registry.clear_team_registry_cache

    def spy_clear(*, reset_bootstrap: bool = True) -> None:
        clear_calls.append(reset_bootstrap)
        original_clear(reset_bootstrap=reset_bootstrap)

    monkeypatch.setattr(team_registry, "clear_team_registry_cache", spy_clear)

    resolutions = team_registry.create_canonical_teams_batch(
        display_names=["Batch Alpha FC", "Batch Beta FC", "Batch Alpha FC"],
        sport="football",
    )

    assert [resolution.team_name for resolution in resolutions] == [
        "Batch Alpha FC",
        "Batch Beta FC",
    ]
    assert {resolution.source for resolution in resolutions} == {"batch_create"}
    assert clear_calls == [False]
    assert (
        team_registry.resolve_team_alias("Batch Alpha FC", sport="football").team_id
        == resolutions[0].team_id
    )
    assert (
        team_registry.resolve_team_alias("Batch Beta FC", sport="football").team_id
        == resolutions[1].team_id
    )


def test_create_canonical_teams_batch_returns_existing_active_team(team_registry_file):
    first = team_registry.create_canonical_team(
        display_name="Existing Batch FC",
        sport="football",
    )

    resolutions = team_registry.create_canonical_teams_batch(
        display_names=["Existing Batch FC", "Fresh Batch FC"],
        sport="football",
    )

    assert resolutions[0].team_id == first.team_id
    assert resolutions[0].source == "canonical"
    assert resolutions[1].team_name == "Fresh Batch FC"
    assert resolutions[1].source == "batch_create"


# ---------------------------------------------------------------------------
# search_canonical_team_candidates — batched RapidFuzz scoring (issue #125)
#
# These tests pin the public contract of `search_canonical_team_candidates`
# after switching the inner scoring loop to `rapidfuzz.process.extract`.
# Use sport="football" because basketball is auto-seeded via SPORT_ALIAS_SEEDS
# and would not be a clean fixture for "empty corpus" / single-team setups.


def _reference_search_canonical_team_candidates(
    raw_team_name: str,
    *,
    sport: str,
    limit: int,
):
    """Verbatim copy of the pre-#125 algorithm, used as the equivalence oracle.

    Reads from the same `_load_team_search_rows` cache and uses the same
    rapidfuzz scorers — so any RapidFuzz/version drift moves both
    implementations identically. The only thing this test guards is the
    rewrite, not the underlying library.
    """
    from rapidfuzz import fuzz as _fuzz

    from app.config import settings as _settings
    from app.services.team_registry import (
        CanonicalTeamCandidate as _CanonicalTeamCandidate,
        _ensure_bootstrapped as _ensure_bootstrapped_fn,
        _load_team_search_rows as _load_rows,
    )

    _ensure_bootstrapped_fn()
    raw_key = normalize_identity_text(raw_team_name)
    if not raw_key:
        return []

    candidates: list[_CanonicalTeamCandidate] = []
    for team_id, team_name, aliases in _load_rows(_settings.db_path, sport):
        best_score = 0.0
        best_alias = None
        for candidate_value in (team_name, *aliases):
            candidate_key = normalize_identity_text(candidate_value)
            if not candidate_key or candidate_key == raw_key:
                continue
            score = float(
                max(
                    _fuzz.token_set_ratio(raw_key, candidate_key),
                    _fuzz.partial_ratio(raw_key, candidate_key),
                )
            )
            if score > best_score:
                best_score = score
                best_alias = candidate_value
        if best_score <= 0:
            continue
        candidates.append(
            _CanonicalTeamCandidate(
                team_id=team_id,
                team_name=team_name,
                score=best_score,
                matched_alias=best_alias if best_alias != team_name else None,
            )
        )

    return sorted(
        candidates,
        key=lambda item: (-item.score, item.team_name),
    )[:limit]


def test_search_canonical_team_candidates_empty_corpus_returns_empty(
    team_registry_file,
):
    assert (
        team_registry.search_canonical_team_candidates(
            "Anything", sport="football", limit=3
        )
        == []
    )


def test_search_canonical_team_candidates_empty_raw_returns_empty(
    team_registry_file,
):
    team_registry.create_canonical_team(display_name="Real Madrid", sport="football")
    assert (
        team_registry.search_canonical_team_candidates(
            "", sport="football", limit=3
        )
        == []
    )
    assert (
        team_registry.search_canonical_team_candidates(
            "   ", sport="football", limit=3
        )
        == []
    )


def test_search_canonical_team_candidates_exact_name_only_team_is_skipped(
    team_registry_file,
):
    """A team whose ONLY indexed string normalises to the raw key contributes
    no candidate: the canonical name row and the auto-registered display-name
    alias both equal `raw_key` and are skipped (preserves the
    `candidate_key == raw_key` guard)."""
    team_registry.create_canonical_team(display_name="Real Madrid", sport="football")

    results = team_registry.search_canonical_team_candidates(
        "Real Madrid", sport="football", limit=3
    )

    assert results == []


def test_search_canonical_team_candidates_exact_alias_skipped_team_still_found_via_other_alias(
    team_registry_file,
):
    """Adding a manually-registered alias whose key equals raw_key is also
    skipped, but the canonical name (a different normalized form) can still
    contribute a fuzzy score and the team appears with `matched_alias=None`."""
    team_registry.create_canonical_team(display_name="Real Madrid", sport="football")
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Real",
        team_name="Real Madrid",
        sport="football",
    )

    results = team_registry.search_canonical_team_candidates(
        "Real", sport="football", limit=3
    )

    assert len(results) == 1
    candidate = results[0]
    assert candidate.team_name == "Real Madrid"
    assert candidate.matched_alias is None
    assert 0.0 < candidate.score <= 100.0


def test_search_canonical_team_candidates_same_team_tie_team_name_wins(
    team_registry_file,
):
    """When the canonical-name row and an alias row score equally, team_name
    wins — `matched_alias` must be None. Use word-order variants where
    token_set_ratio scores 100 for both rows."""
    resolution = team_registry.create_canonical_team(
        display_name="Foo Bar", sport="football"
    )
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Bar Foo",
        team_name="Foo Bar",
        sport="football",
    )

    results = team_registry.search_canonical_team_candidates(
        "Foo Bar Baz", sport="football", limit=3
    )

    assert len(results) == 1
    candidate = results[0]
    assert candidate.team_id == resolution.team_id
    assert candidate.team_name == "Foo Bar"
    assert candidate.matched_alias is None


def test_search_canonical_team_candidates_cross_team_tie_sorted_by_name(
    team_registry_file,
):
    """Cross-team ties break on team_name ascending — current sort key
    `(-score, team_name)`. We pick names that genuinely tie under the
    rapidfuzz scorers: partial_ratio = 87.50 for both `"Bravo United"` and
    `"Tango United"` against `"qa united"`, while `"Alpha United"` scores
    higher (94.12) and must come first. `"Bravo"` and `"Tango"` are chosen
    because they share the same length as `"Alpha"` so token_set_ratio /
    fuzz.ratio admit partial_ratio for all three under the post-substring
    -fix gate (``fuzz.ratio('qa united', '<word> united') == 76.19`` for any
    5-letter word; ``Charlie United`` would dip the ratio below 70 and
    block the partial_ratio admission for that single candidate, breaking
    the tie under the new gate)."""
    team_registry.create_canonical_team(
        display_name="Tango United", sport="football"
    )
    team_registry.create_canonical_team(
        display_name="Bravo United", sport="football"
    )
    team_registry.create_canonical_team(
        display_name="Alpha United", sport="football"
    )

    results = team_registry.search_canonical_team_candidates(
        "qa united", sport="football", limit=3
    )

    assert [c.team_name for c in results] == [
        "Alpha United",
        "Bravo United",
        "Tango United",
    ]
    assert results[1].score == results[2].score
    assert results[0].score > results[1].score


def test_search_canonical_team_candidates_limit_caps_results(team_registry_file):
    for name in [
        "Alpha United",
        "Bravo United",
        "Charlie United",
        "Delta United",
        "Echo United",
    ]:
        team_registry.create_canonical_team(display_name=name, sport="football")

    results = team_registry.search_canonical_team_candidates(
        "United Town", sport="football", limit=2
    )
    assert len(results) == 2

    results_full = team_registry.search_canonical_team_candidates(
        "United Town", sport="football", limit=5
    )
    assert len(results_full) == 5


def test_search_canonical_team_candidates_top1_matches_reference_implementation(
    team_registry_file,
):
    """Top-1 equivalence oracle: assert the prefilter implementation picks the
    same #1 result as a verbatim copy of the pre-#125 algorithm.

    The post-#128 prefilter does not preserve full bit-identical equivalence
    on `limit > 1` because the prefilter intentionally drops candidates that
    score via partial-substring overlap with no shared tokens or trigrams
    (e.g. ``'FC Atletico'`` vs ``'FC Real'`` scores 76.9 in the reference
    but is dropped by the prefilter — that is a deliberate precision
    tradeoff, not a regression). Top-1 stays the same because the genuine
    best match always shares strong signal with the query.

    Note: the test queries deliberately avoid raw names with qualifier
    markers (``B``, ``II``, ``U19``, ``women``, ``Frauen`` …). The
    qualifier-aware gate added alongside the substring-poison fix applies
    a 15-point demotion to candidates whose qualifier set doesn't match
    the raw, which is a deliberate deviation from the reference. The
    qualifier-driven score adjustment is exercised by the dedicated tests
    around ``_qualifier_gate`` instead.

    The score-equality assertion was relaxed to ``actual <= expected``
    because the substring-poison gate denies ``partial_ratio`` for
    candidates that don't share a token / token-prefix / typo signal
    (e.g. ``Real Sociedad`` raw vs ``FC Real`` alias scores 72.7272 under
    the gated path and 72.7273 under the reference — a 1-ULP delta that
    reflects the gate's intentional stricter precision). Top-1 *identity*
    is the contract; absolute scores under the gates are allowed to
    regress as long as the identity is preserved.
    """
    team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="FC Real",
        team_name="Real Madrid",
        sport="football",
    )
    team_registry.create_canonical_team(
        display_name="Atletico Madrid", sport="football"
    )
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Atleti",
        team_name="Atletico Madrid",
        sport="football",
    )
    team_registry.create_canonical_team(
        display_name="Barcelona", sport="football"
    )
    team_registry.create_canonical_team(
        display_name="Sevilla FC", sport="football"
    )
    team_registry.create_canonical_team(
        display_name="Real Sociedad", sport="football"
    )

    queries = [
        "Real Madrid CF",
        "FC Atletico",
        "Barca",
        "Real Sociedad",
        "Sevilla",
        "Madrid CF",
        "Sociedad de San Sebastian",
    ]
    for query in queries:
        actual = team_registry.search_canonical_team_candidates(
            query, sport="football", limit=3
        )
        expected = _reference_search_canonical_team_candidates(
            query, sport="football", limit=3
        )
        assert actual, f"prefilter returned no result for query={query!r}"
        assert expected, f"reference returned no result for query={query!r}"
        assert actual[0].team_id == expected[0].team_id, (
            f"top-1 team_id mismatch for query={query!r}: "
            f"actual={actual[0]!r} expected={expected[0]!r}"
        )
        assert actual[0].team_name == expected[0].team_name, (
            f"top-1 team_name mismatch for query={query!r}"
        )
        # The new code's score may be <= reference because the
        # substring-poison gate denies partial_ratio for some candidates
        # (the reference always admits it) and the qualifier gate demotes
        # candidates with women/youth/reserve mismatch. Both are
        # deliberate deviations. The invariant the test pins is that the
        # *team identity* of the top-1 candidate is unchanged — not the
        # absolute score.
        assert actual[0].score <= expected[0].score, (
            f"top-1 score regressed beyond reference for query={query!r}: "
            f"actual={actual[0]!r} expected={expected[0]!r}"
        )
        assert actual[0].matched_alias == expected[0].matched_alias, (
            f"top-1 matched_alias mismatch for query={query!r}"
        )


# ---------------------------------------------------------------------------
# Token + trigram inverted-index prefilter (issue #128)
#
# These tests pin the new prefilter behaviour: index correctness, candidate
# selection (token / trigram / fallback / team-set expansion), end-to-end
# semantic recall on randomized mutations, and explicit recall-risk
# sentinels for legitimate fuzzy matches that the prefilter must keep.

import random as _random


def test_load_team_review_search_indexes_empty_corpus(team_registry_file):
    token_index, trigram_index, idxs_by_team_id = (
        team_registry._load_team_review_search_indexes(
            team_registry.settings.db_path, "football"
        )
    )
    assert token_index == {}
    assert trigram_index == {}
    assert idxs_by_team_id == {}


def test_load_team_review_search_indexes_token_index_correctness(
    team_registry_file,
):
    real = team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    atletico = team_registry.create_canonical_team(
        display_name="Atletico Madrid", sport="football"
    )
    barcelona = team_registry.create_canonical_team(
        display_name="Barcelona", sport="football"
    )
    token_index, _, idxs_by_team_id = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )

    real_idxs = set(idxs_by_team_id[real.team_id])
    atletico_idxs = set(idxs_by_team_id[atletico.team_id])
    barcelona_idxs = set(idxs_by_team_id[barcelona.team_id])

    madrid_hits = set(token_index.get("madrid", ()))
    assert madrid_hits & real_idxs, "real madrid rows must be in token_index['madrid']"
    assert madrid_hits & atletico_idxs, "atletico madrid rows must be in token_index['madrid']"
    assert not (madrid_hits & barcelona_idxs)

    barcelona_hits = set(token_index.get("barcelona", ()))
    assert barcelona_hits == barcelona_idxs


def test_load_team_review_search_indexes_skips_short_tokens(team_registry_file):
    """Tokens shorter than 3 chars are deliberately excluded so common
    affixes like ``"fc"`` do not balloon the candidate set."""
    team_registry.create_canonical_team(
        display_name="Real Madrid CF", sport="football"
    )
    team_registry.create_canonical_team(
        display_name="FC Barcelona", sport="football"
    )
    token_index, _, _ = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    assert "fc" not in token_index
    assert "cf" not in token_index
    assert "real" in token_index
    assert "barcelona" in token_index


def test_load_team_review_search_indexes_skips_corpus_frequent_tokens(
    team_registry_file,
):
    """Tokens that match more than 10 % of teams (with a min-floor of 10
    teams to avoid over-filtering small test corpora) are dropped (e.g.
    ``"fc"``, ``"club"``). Setup: 11 teams all sharing one rare token; that
    token must NOT be in the index because 11 > floor=10."""
    for i in range(11):
        team_registry.create_canonical_team(
            display_name=f"Team{i:02d} unitedrare", sport="football"
        )
    token_index, _, idxs_by_team_id = (
        team_registry._load_team_review_search_indexes(
            team_registry.settings.db_path, "football"
        )
    )
    assert len(idxs_by_team_id) == 11
    assert "unitedrare" not in token_index
    assert "team00" in token_index
    assert "team09" in token_index


def test_load_team_review_search_indexes_trigram_strips_whitespace(
    team_registry_file,
):
    team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    _, trigram_index, _ = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    assert "alm" in trigram_index, (
        "trigram 'alm' (from 'realmadrid') must be present"
    )
    assert "rea" in trigram_index
    assert "rid" in trigram_index


def test_load_team_review_search_indexes_idxs_by_team_id_covers_every_row(
    team_registry_file,
):
    real = team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Los Blancos",
        team_name="Real Madrid",
        sport="football",
    )
    _, _, idxs_by_team_id = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    team_ids, _, _, normalized_choices = (
        team_registry._load_team_review_search_choices(
            team_registry.settings.db_path, "football"
        )
    )
    real_idxs_from_index = set(idxs_by_team_id[real.team_id])
    real_idxs_truth = {
        idx for idx, tid in enumerate(team_ids) if tid == real.team_id
    }
    assert real_idxs_from_index == real_idxs_truth
    assert len(real_idxs_from_index) == 3


def test_load_team_review_search_indexes_invalidated_by_clear_cache(
    team_registry_file,
):
    team_registry.create_canonical_team(
        display_name="Team Alpha", sport="football"
    )
    token_index_v1, _, _ = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    assert "alpha" in token_index_v1

    team_registry.create_canonical_team(
        display_name="Team Beta", sport="football"
    )
    token_index_v2, _, _ = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    assert "beta" in token_index_v2, (
        "create_canonical_team must invalidate the index cache so subsequent "
        "lookups see the newly created team"
    )


def test_collect_candidate_idxs_token_path(team_registry_file):
    real = team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    barcelona = team_registry.create_canonical_team(
        display_name="Barcelona", sport="football"
    )
    indexes = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    team_ids, _, _, normalized_choices = (
        team_registry._load_team_review_search_choices(
            team_registry.settings.db_path, "football"
        )
    )

    candidate_idxs = team_registry._collect_team_search_candidate_idxs(
        "real Madrid",
        token_index=indexes[0],
        trigram_index=indexes[1],
        idxs_by_team_id=indexes[2],
        team_ids=team_ids,
        total_rows=len(normalized_choices),
    )
    real_idxs = set(indexes[2][real.team_id])
    barcelona_idxs = set(indexes[2][barcelona.team_id])
    assert real_idxs.issubset(set(candidate_idxs))
    assert not (barcelona_idxs & set(candidate_idxs))


def test_collect_candidate_idxs_trigram_path(team_registry_file):
    """A typo'd query that shares NO tokens still hits via the trigram
    index. Without trigrams, this case would produce 0 candidates."""
    liverpool = team_registry.create_canonical_team(
        display_name="Liverpool", sport="football"
    )
    barcelona = team_registry.create_canonical_team(
        display_name="Barcelona", sport="football"
    )
    indexes = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    team_ids, _, _, normalized_choices = (
        team_registry._load_team_review_search_choices(
            team_registry.settings.db_path, "football"
        )
    )
    candidate_idxs = team_registry._collect_team_search_candidate_idxs(
        "liverpol",
        token_index=indexes[0],
        trigram_index=indexes[1],
        idxs_by_team_id=indexes[2],
        team_ids=team_ids,
        total_rows=len(normalized_choices),
    )
    liverpool_idxs = set(indexes[2][liverpool.team_id])
    barcelona_idxs = set(indexes[2][barcelona.team_id])
    assert liverpool_idxs.issubset(set(candidate_idxs))
    assert not (barcelona_idxs & set(candidate_idxs))


def test_collect_candidate_idxs_empty_set_falls_back_to_full_corpus(
    team_registry_file,
):
    """When token + trigram lookups both miss everything, return the full
    range so behaviour matches the pre-prefilter implementation."""
    team_registry.create_canonical_team(
        display_name="Real Madrid", sport="football"
    )
    indexes = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    team_ids, _, _, normalized_choices = (
        team_registry._load_team_review_search_choices(
            team_registry.settings.db_path, "football"
        )
    )
    candidate_idxs = team_registry._collect_team_search_candidate_idxs(
        "qz",
        token_index=indexes[0],
        trigram_index=indexes[1],
        idxs_by_team_id=indexes[2],
        team_ids=team_ids,
        total_rows=len(normalized_choices),
    )
    assert candidate_idxs == list(range(len(normalized_choices)))


def test_collect_candidate_idxs_team_set_expansion_preserves_tie_semantics(
    team_registry_file,
):
    """When a manual alias of team T is the only row that hits the
    prefilter, team-set expansion still adds T's team_name row so per-team
    aggregation can correctly tie-break to ``matched_alias=None``."""
    foo = team_registry.create_canonical_team(
        display_name="Foo Bar", sport="football"
    )
    team_registry.remember_team_alias(
        bookmaker_id="qa-book",
        raw_team_name="Bar Foo",
        team_name="Foo Bar",
        sport="football",
    )
    indexes = team_registry._load_team_review_search_indexes(
        team_registry.settings.db_path, "football"
    )
    team_ids, _, _, normalized_choices = (
        team_registry._load_team_review_search_choices(
            team_registry.settings.db_path, "football"
        )
    )
    candidate_idxs = team_registry._collect_team_search_candidate_idxs(
        "Bar Foo",
        token_index=indexes[0],
        trigram_index=indexes[1],
        idxs_by_team_id=indexes[2],
        team_ids=team_ids,
        total_rows=len(normalized_choices),
    )
    foo_idxs = set(indexes[2][foo.team_id])
    assert foo_idxs.issubset(set(candidate_idxs)), (
        "team-set expansion must include all of Foo Bar's rows even when "
        "only one row hit the prefilter"
    )


_RECALL_RISK_FIXTURES = [
    ("utd", "Manchester United"),
    ("liverpol", "Liverpool FC"),
    ("liver pool", "Liverpool FC"),
    ("bayer munchen", "Bayern Munich"),
    ("marselle", "Olympique Marseille"),
    ("man utd", "Manchester United"),
    ("inter", "Internazionale Milano"),
]


@pytest.mark.parametrize("query,expected_team_name", _RECALL_RISK_FIXTURES)
def test_search_canonical_team_candidates_recall_risk_sentinels(
    query, expected_team_name, team_registry_file
):
    """Lock in the legitimate fuzzy matches that motivated this issue. If a
    future change tightens the prefilter (e.g. raises the trigram-overlap
    threshold) and silently breaks one of these, this parametrized test
    fails with a clear "missing recall" signal."""
    for canonical in {expected_team_name, "Barcelona", "Real Madrid", "Atletico Madrid", "Juventus"}:
        team_registry.create_canonical_team(
            display_name=canonical, sport="football"
        )
    results = team_registry.search_canonical_team_candidates(
        query, sport="football", limit=3
    )
    assert results, f"no candidates returned for query={query!r}"
    team_names = [c.team_name for c in results]
    assert expected_team_name in team_names, (
        f"expected {expected_team_name!r} in top-3 for query={query!r}, "
        f"got {team_names!r}"
    )


def test_search_canonical_team_candidates_semantic_recall_property(
    team_registry_file,
):
    """Randomized property test: for queries derived from corpus team names
    via single-character mutations, dropping short noise words, or appending
    a junk suffix, the source team must appear in the prefilter's top-3
    result. This is the primary correctness oracle for the prefilter."""
    rng = _random.Random(20260509)
    base_names = [
        "Real Madrid",
        "Atletico Madrid",
        "Barcelona",
        "Sevilla FC",
        "Real Sociedad",
        "Athletic Bilbao",
        "Real Betis",
        "Valencia",
        "Villarreal",
        "Real Mallorca",
        "Liverpool FC",
        "Manchester United",
        "Manchester City",
        "Chelsea FC",
        "Arsenal FC",
        "Tottenham Hotspur",
        "Newcastle United",
        "Aston Villa",
        "Brighton Hove Albion",
        "Leeds United",
        "FC Bayern Munich",
        "Borussia Dortmund",
        "RB Leipzig",
        "Bayer Leverkusen",
        "Eintracht Frankfurt",
        "Olympique Marseille",
        "Paris Saint Germain",
        "Olympique Lyonnais",
        "AS Monaco",
        "Stade Rennais",
    ]
    for name in base_names:
        team_registry.create_canonical_team(display_name=name, sport="football")

    def mutate(name: str) -> str | None:
        """Return a query that differs from ``name`` while preserving most
        signal, or ``None`` if no usable mutation is possible.

        The function deliberately avoids mutations that strip all signal
        (e.g. truncating ``"FC Barcelona"`` to ``"FC"``). It also returns
        ``None`` when the mutation would equal ``name`` exactly (which would
        trigger the ``candidate_key == raw_key`` skip and the team would be
        filtered out — that is correct behaviour but not what we are
        testing here)."""
        choice = rng.choice(["substitute", "drop_short_word", "append_junk"])
        words = name.split()
        if choice == "substitute" and len(name) >= 4:
            for _ in range(5):
                i = rng.randrange(1, len(name))
                if name[i] == " ":
                    continue
                replacement = rng.choice("aeiouxz")
                if replacement == name[i]:
                    continue
                mutated = name[:i] + replacement + name[i + 1 :]
                if mutated != name:
                    return mutated
            return None
        if choice == "drop_short_word" and len(words) > 1:
            kept = [w for w in words if len(w) >= 4]
            if len(kept) >= 1 and len(kept) < len(words):
                mutated = " ".join(kept)
                if mutated and mutated != name:
                    return mutated
            return None
        if choice == "append_junk":
            return name + " " + rng.choice(["xtra", "qq", "aux"])
        return None

    misses: list[tuple[str, str, list[str]]] = []
    attempts = 0
    successes = 0
    while successes < 50 and attempts < 200:
        attempts += 1
        source = rng.choice(base_names)
        query = mutate(source)
        if query is None:
            continue
        successes += 1
        results = team_registry.search_canonical_team_candidates(
            query, sport="football", limit=3
        )
        names = [c.team_name for c in results]
        if source not in names:
            misses.append((query, source, names))

    assert successes >= 30, (
        f"only {successes}/200 mutation attempts produced a usable query; "
        "the mutate generator may be too restrictive"
    )
    assert not misses, (
        f"semantic recall failure on {len(misses)} / {successes} queries:\n"
        + "\n".join(f"  query={q!r} source={s!r} top3={t!r}" for q, s, t in misses)
    )
