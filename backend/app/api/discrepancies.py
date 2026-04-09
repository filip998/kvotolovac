from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..models.schemas import DiscrepancyDetail, DiscrepancyOut
from ..store import odds_store

router = APIRouter(prefix="/discrepancies", tags=["discrepancies"])


@router.get("", response_model=list[DiscrepancyOut])
async def list_discrepancies(
    sport: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    min_gap: Optional[float] = Query(None),
    market_type: Optional[str] = Query(None),
    sort_by: str = Query("profit_margin"),
    sort_order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_discrepancies(
        sport=sport,
        league_id=league,
        market_type=market_type,
        min_gap=min_gap,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )


@router.get("/{disc_id}", response_model=DiscrepancyDetail)
async def get_discrepancy(disc_id: int):
    disc = await odds_store.get_discrepancy(disc_id)
    if not disc:
        raise HTTPException(status_code=404, detail="Discrepancy not found")
    return disc
