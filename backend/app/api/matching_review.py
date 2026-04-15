from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import (
    MatchingReviewApprovalIn,
    MatchingReviewApprovalOut,
    MatchingReviewOut,
    MatchingReviewSummaryOut,
)
from ..services.league_registry import league_display_name, remember_bookmaker_league_alias
from ..store import odds_store

router = APIRouter(prefix="/matching-review", tags=["matching-review"])


@router.get("/summary", response_model=MatchingReviewSummaryOut)
async def get_matching_review_summary(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
) -> MatchingReviewSummaryOut:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    return await odds_store.get_matching_review_summary(
        bookmaker_ids=selected_bookmakers or None,
    )


@router.get("/cases", response_model=list[MatchingReviewOut])
async def list_matching_review_cases(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
    league_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[MatchingReviewOut]:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    return await odds_store.get_matching_review_cases(
        bookmaker_ids=selected_bookmakers or None,
        league_id=league_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/cases/{case_id}/approve", response_model=MatchingReviewApprovalOut)
async def approve_matching_review_case(
    case_id: int,
    payload: Optional[MatchingReviewApprovalIn] = None,
) -> MatchingReviewApprovalOut:
    case = await odds_store.get_matching_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Matching review case not found")

    target_league_id = payload.league_id if payload and payload.league_id else case.suggested_league_id
    if target_league_id != case.suggested_league_id:
        raise HTTPException(
            status_code=400,
            detail="Only the suggested league can be approved in this phase",
        )
    resolution = await asyncio.to_thread(
        remember_bookmaker_league_alias,
        bookmaker_id=case.bookmaker_id,
        raw_league_id=case.raw_league_id,
        league_id=target_league_id,
    )
    await odds_store.mark_matching_review_case_approved(
        case_id=case_id,
        bookmaker_id=case.bookmaker_id,
        normalized_raw_league_id=case.normalized_raw_league_id,
        suggested_league_id=case.suggested_league_id,
        scraped_at=case.scraped_at,
    )
    return MatchingReviewApprovalOut(
        case_id=case_id,
        status="approved",
        saved_alias=case.raw_league_id,
        saved_league_id=resolution.league_id,
        saved_league_name=league_display_name(resolution.league_id),
    )
