from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ..models.schemas import UnresolvedOddsOut
from ..store import odds_store

router = APIRouter(prefix="/unresolved-odds")


@router.get("", response_model=list[UnresolvedOddsOut])
async def list_unresolved_odds(
    bookmaker_id: Optional[str] = Query(default=None),
    reason_code: Optional[str] = Query(default=None),
    market_type: Optional[str] = Query(default=None),
    league_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[UnresolvedOddsOut]:
    return await odds_store.get_unresolved_odds(
        bookmaker_id=bookmaker_id,
        reason_code=reason_code,
        market_type=market_type,
        league_id=league_id,
        limit=limit,
        offset=offset,
    )
