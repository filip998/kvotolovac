from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.services.team_registry import (
    clear_team_registry_cache,
    create_canonical_team,
    merge_canonical_teams,
    resolve_team_alias,
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
