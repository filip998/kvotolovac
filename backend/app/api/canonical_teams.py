from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import (
    CanonicalTeamMergeIn,
    CanonicalTeamMergeOut,
    CanonicalTeamOut,
    CanonicalTeamUnmergeOut,
)
from ..services.scheduler import scheduler
from ..services.team_registry import (
    list_canonical_teams,
    merge_canonical_teams,
    unmerge_canonical_team,
)

router = APIRouter(prefix="/canonical-teams", tags=["canonical-teams"])


@router.get("", response_model=list[CanonicalTeamOut])
async def get_canonical_teams(
    sport: str = Query(default="basketball"),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_merged: bool = Query(default=False),
) -> list[CanonicalTeamOut]:
    teams = await asyncio.to_thread(
        list_canonical_teams,
        sport=sport,
        search=search,
        limit=limit,
        offset=offset,
        include_merged=include_merged,
    )
    return [
        CanonicalTeamOut(
            id=team.id,
            sport=team.sport,
            display_name=team.display_name,
            aliases=list(team.aliases),
            alias_count=team.alias_count,
            merged_into_team_id=team.merged_into_team_id,
        )
        for team in teams
    ]


@router.post("/{team_id}/merge", response_model=CanonicalTeamMergeOut)
async def merge_team(
    team_id: int,
    payload: CanonicalTeamMergeIn,
) -> CanonicalTeamMergeOut:
    try:
        merged = await asyncio.to_thread(
            merge_canonical_teams,
            source_team_id=team_id,
            target_team_id=payload.target_team_id,
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if "must exist" in detail else 400,
            detail=detail,
        )
    return CanonicalTeamMergeOut(
        source_team_id=team_id,
        target_team_id=payload.target_team_id,
        merged_team_name=merged.display_name,
    )


@router.post("/{team_id}/unmerge", response_model=CanonicalTeamUnmergeOut)
async def unmerge_team(team_id: int) -> CanonicalTeamUnmergeOut:
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot unmerge canonical teams while a scrape cycle is in progress; try again shortly",
        )
    try:
        result = await asyncio.to_thread(
            unmerge_canonical_team,
            source_team_id=team_id,
        )
    except ValueError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if "No active merge history" in detail else 400,
            detail=detail,
        )
    return CanonicalTeamUnmergeOut(
        source_team_id=result.source_team_id,
        target_team_id=result.target_team_id,
        restored_team_name=result.source_team_name,
    )
