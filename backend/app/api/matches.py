from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import MatchOut, OddsOut
from ..store import odds_store

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
async def list_matches(
    league_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_matches(
        league_id=league_id, status=status, limit=limit, offset=offset
    )


@router.get("/{match_id}", response_model=MatchOut)
async def get_match(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.get("/{match_id}/odds", response_model=list[OddsOut])
async def get_match_odds(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_odds_for_match(match_id)


@router.get("/{match_id}/history", response_model=list[OddsOut])
async def get_match_history(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_odds_history_for_match(match_id)
