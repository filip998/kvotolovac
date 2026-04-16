from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import CanonicalTeamMergeIn, CanonicalTeamMergeOut, CanonicalTeamOut
from ..services.team_registry import list_canonical_teams, merge_canonical_teams

router = APIRouter(prefix="/canonical-teams", tags=["canonical-teams"])


@router.get("", response_model=list[CanonicalTeamOut])
async def get_canonical_teams(
    sport: str = Query(default="basketball"),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[CanonicalTeamOut]:
    teams = await asyncio.to_thread(
        list_canonical_teams,
        sport=sport,
        search=search,
        limit=limit,
        offset=offset,
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
