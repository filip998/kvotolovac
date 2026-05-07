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
