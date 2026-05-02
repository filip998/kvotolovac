from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from ._params import parse_csv_query_values
from ..models.schemas import OpportunityOut, OutcomeOfferOut
from ..store import odds_store

router = APIRouter(tags=["opportunities"])


@router.get("/opportunities", response_model=list[OpportunityOut])
async def list_opportunities(
    sport: Optional[str] = Query(None),
    bookmaker_ids: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    include_legacy_discrepancy_overlap: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_opportunities(
        sport=sport,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        market_type=market_type,
        include_legacy_discrepancy_overlap=include_legacy_discrepancy_overlap,
        limit=limit,
        offset=offset,
    )


@router.get("/market-offers", response_model=list[OutcomeOfferOut])
async def list_market_offers(
    sport: Optional[str] = Query(None),
    match_id: Optional[str] = Query(None),
    bookmaker_ids: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_outcome_offers(
        sport=sport,
        match_id=match_id,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        market_type=market_type,
        limit=limit,
        offset=offset,
    )
