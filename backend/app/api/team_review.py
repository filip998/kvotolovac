from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import (
    TeamReviewActionOut,
    TeamReviewApprovalIn,
    TeamReviewApprovalOut,
    TeamReviewOut,
)
from ..services.team_registry import (
    CircularAliasError,
    create_canonical_team,
    get_canonical_team,
    remember_team_alias,
)
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
async def approve_team_review_case(
    case_id: int,
    payload: Optional[TeamReviewApprovalIn] = None,
) -> TeamReviewApprovalOut:
    case = await odds_store.get_team_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Team review case not found")

    requested_team_id = payload.team_id if payload else None
    create_team_name = payload.create_team_name.strip() if payload and payload.create_team_name else None

    if requested_team_id is not None and create_team_name:
        raise HTTPException(
            status_code=400,
            detail="Choose an existing team or create a new one, not both",
        )

    if create_team_name:
        target_resolution = await asyncio.to_thread(
            create_canonical_team,
            display_name=create_team_name,
            sport=case.sport,
        )
        target_team_id = target_resolution.team_id
        target_team_name = target_resolution.team_name
    elif requested_team_id is not None:
        target_team = await asyncio.to_thread(
            get_canonical_team,
            requested_team_id,
            follow_merge=True,
        )
        if target_team is None:
            raise HTTPException(status_code=404, detail="Canonical team not found")
        if target_team.sport != case.sport:
            raise HTTPException(
                status_code=400,
                detail="Canonical team sport does not match the review case",
            )
        target_team_id = target_team.id
        target_team_name = target_team.display_name
    elif case.suggested_team_name:
        target_team_id = case.suggested_team_id or 0
        target_team_name = case.suggested_team_name
    else:
        raise HTTPException(
            status_code=400,
            detail="Review case has no suggested team; choose a candidate or create a new canonical team",
        )

    try:
        resolution = await asyncio.to_thread(
            remember_team_alias,
            bookmaker_id=case.bookmaker_id,
            raw_team_name=case.raw_team_name,
            team_name=target_team_name,
            sport=case.sport,
        )
    except CircularAliasError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await odds_store.mark_team_review_case_approved(case_id)
    return TeamReviewApprovalOut(
        case_id=case_id,
        status="approved",
        saved_alias=case.raw_team_name,
        saved_team_id=resolution.team_id,
        saved_team_name=target_team_name,
        resolved_team_name=(
            resolution.team_name
            if resolution.team_name != target_team_name
            else None
        ),
    )


@router.post("/cases/{case_id}/decline", response_model=TeamReviewActionOut)
async def decline_team_review_case(case_id: int) -> TeamReviewActionOut:
    case = await odds_store.get_team_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Team review case not found")
    await odds_store.mark_team_review_case_declined(case_id)
    return TeamReviewActionOut(case_id=case_id, status="declined")
