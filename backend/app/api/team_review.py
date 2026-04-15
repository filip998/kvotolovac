from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import TeamReviewActionOut, TeamReviewApprovalOut, TeamReviewOut
from ..services.team_registry import remember_team_alias
from ..store import odds_store

router = APIRouter(prefix="/team-review", tags=["team-review"])


@router.get("/cases", response_model=list[TeamReviewOut])
async def list_team_review_cases(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[TeamReviewOut]:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    return await odds_store.get_team_review_cases(
        bookmaker_ids=selected_bookmakers or None,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post("/cases/{case_id}/approve", response_model=TeamReviewApprovalOut)
async def approve_team_review_case(case_id: int) -> TeamReviewApprovalOut:
    case = await odds_store.get_team_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Team review case not found")
    if case.scope_league_id is None:
        raise HTTPException(
            status_code=409,
            detail="Team review case needs a resolved competition scope before approval",
        )

    await asyncio.to_thread(
        remember_team_alias,
        bookmaker_id=case.bookmaker_id,
        raw_team_name=case.raw_team_name,
        team_name=case.suggested_team_name,
        competition_id=case.scope_league_id,
    )
    await odds_store.mark_team_review_case_approved(case_id)
    return TeamReviewApprovalOut(
        case_id=case_id,
        status="approved",
        saved_alias=case.raw_team_name,
        saved_team_name=case.suggested_team_name,
    )


@router.post("/cases/{case_id}/decline", response_model=TeamReviewActionOut)
async def decline_team_review_case(case_id: int) -> TeamReviewActionOut:
    case = await odds_store.get_team_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Team review case not found")
    await odds_store.mark_team_review_case_declined(case_id)
    return TeamReviewActionOut(case_id=case_id, status="declined")
