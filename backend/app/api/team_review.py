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
from ..services.scheduler import scheduler
from ..services.team_registry import (
    CanonicalTeamSummary,
    CircularAliasError,
    MERGE_SOURCE_TEAM_REVIEW_APPROVAL,
    TeamAliasResolution,
    create_canonical_team,
    get_canonical_team,
    merge_canonical_teams,
    remember_team_alias,
    resolve_team_alias,
    validate_team_name_identity,
)
from ..services.text_normalizer import normalize_identity_text
from ..store import odds_store

router = APIRouter(prefix="/team-review", tags=["team-review"])


async def _remember_team_alias(
    case: TeamReviewOut,
    *,
    target_team_name: str,
) -> TeamAliasResolution:
    return await asyncio.to_thread(
        remember_team_alias,
        bookmaker_id=case.bookmaker_id,
        raw_team_name=case.raw_team_name,
        team_name=target_team_name,
        sport=case.sport,
    )


def _is_canonical_duplicate_conflict(
    case: TeamReviewOut,
    existing_resolution: TeamAliasResolution | None,
    source_team: CanonicalTeamSummary | None,
    *,
    target_team_id: int,
) -> bool:
    return (
        existing_resolution is not None
        and source_team is not None
        and existing_resolution.team_id != target_team_id
        and existing_resolution.bookmaker_id == ""
        and source_team.sport == case.sport
        and normalize_identity_text(source_team.display_name)
        == normalize_identity_text(case.raw_team_name)
    )


async def _merge_existing_canonical_duplicate_for_review(
    case: TeamReviewOut,
    *,
    target_team_id: int,
    target_team_name: str,
) -> tuple[TeamAliasResolution, CanonicalTeamSummary] | None:
    existing_resolution = await asyncio.to_thread(
        resolve_team_alias,
        case.raw_team_name,
        bookmaker_id=case.bookmaker_id,
        sport=case.sport,
    )
    source_team = (
        await asyncio.to_thread(get_canonical_team, existing_resolution.team_id)
        if existing_resolution is not None
        else None
    )
    if not _is_canonical_duplicate_conflict(
        case,
        existing_resolution,
        source_team,
        target_team_id=target_team_id,
    ):
        return None

    assert source_team is not None
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot merge canonical teams while a scrape cycle is in progress; try again shortly",
        )
    await asyncio.to_thread(
        merge_canonical_teams,
        source_team_id=source_team.id,
        target_team_id=target_team_id,
        allow_unsafe_subset_override=True,
        merge_source=MERGE_SOURCE_TEAM_REVIEW_APPROVAL,
        merge_reason="team_review_duplicate_canonical",
    )
    resolution = await _remember_team_alias(case, target_team_name=target_team_name)
    return resolution, source_team


@router.get("/cases", response_model=list[TeamReviewOut])
async def list_team_review_cases(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
    sport: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[TeamReviewOut]:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    return await odds_store.get_team_review_cases(
        bookmaker_ids=selected_bookmakers or None,
        sport=sport,
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
        try:
            await asyncio.to_thread(
                validate_team_name_identity,
                case.raw_team_name,
                create_team_name,
                sport=case.sport,
                allow_unsafe_subset_override=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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

    merged_source_team: CanonicalTeamSummary | None = None
    try:
        await asyncio.to_thread(
            validate_team_name_identity,
            case.raw_team_name,
            target_team_name,
            sport=case.sport,
            allow_unsafe_subset_override=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        resolution = await _remember_team_alias(case, target_team_name=target_team_name)
    except CircularAliasError as exc:
        try:
            merged_result = (
                await _merge_existing_canonical_duplicate_for_review(
                    case,
                    target_team_id=target_team_id,
                    target_team_name=target_team_name,
                )
                if target_team_id > 0
                else None
            )
        except ValueError as retry_exc:
            raise HTTPException(status_code=400, detail=str(retry_exc)) from retry_exc
        except CircularAliasError as retry_exc:
            raise HTTPException(status_code=409, detail=str(retry_exc)) from retry_exc
        if merged_result is None:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        resolution, merged_source_team = merged_result
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
        merged_source_team_id=(
            merged_source_team.id if merged_source_team is not None else None
        ),
        merged_source_team_name=(
            merged_source_team.display_name if merged_source_team is not None else None
        ),
    )


@router.post("/cases/{case_id}/decline", response_model=TeamReviewActionOut)
async def decline_team_review_case(case_id: int) -> TeamReviewActionOut:
    case = await odds_store.get_team_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Team review case not found")
    await odds_store.mark_team_review_case_declined(case_id)
    return TeamReviewActionOut(case_id=case_id, status="declined")
