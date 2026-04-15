from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ._params import parse_csv_query_values
from ..models.schemas import UnresolvedOddsOut
from ..store import odds_store

router = APIRouter(prefix="/unresolved-odds")


@router.get("", response_model=list[UnresolvedOddsOut])
async def list_unresolved_odds(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
    reason_code: Optional[str] = Query(default=None),
    market_type: Optional[str] = Query(default=None),
    league_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[UnresolvedOddsOut]:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    return await odds_store.get_unresolved_odds(
        bookmaker_ids=selected_bookmakers or None,
        reason_code=reason_code,
        market_type=market_type,
        league_id=league_id,
        limit=limit,
        offset=offset,
    )
