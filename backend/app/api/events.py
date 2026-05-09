from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import EventDetailOut, EventOddsOut, OutcomeOfferOut
from ..store import odds_store

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/{resolved_event_id}", response_model=EventDetailOut)
async def get_event(resolved_event_id: str):
    event = await odds_store.get_event_detail(resolved_event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/{resolved_event_id}/odds", response_model=list[EventOddsOut])
async def get_event_odds(
    resolved_event_id: str,
    bookmaker_ids: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    odds = await odds_store.get_event_odds(
        resolved_event_id,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        market_type=market_type,
        limit=limit,
        offset=offset,
    )
    if odds is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return odds


@router.get("/{resolved_event_id}/market-offers", response_model=list[OutcomeOfferOut])
async def get_event_market_offers(
    resolved_event_id: str,
    bookmaker_ids: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    offers = await odds_store.get_event_outcome_offers(
        resolved_event_id,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        market_type=market_type,
        limit=limit,
        offset=offset,
    )
    if offers is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return offers
