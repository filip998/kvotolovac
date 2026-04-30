from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import (
    EventMergeIn,
    EventMergeOut,
    EventReviewAcceptanceIn,
    EventReviewActionOut,
    EventReviewCaseIn,
    EventReviewCaseOut,
    ResolvedEventIn,
    ResolvedEventMemberIn,
)
from ..services.scheduler import scheduler
from ..store import odds_store

router = APIRouter(prefix="/event-review", tags=["event-review"])


def _manual_event_merge_fingerprint(
    *,
    sport: str,
    start_time: str | None,
    match_ids: list[str],
) -> str:
    match_key = "|".join(sorted(match_ids))
    return f"manual_event_merge:{sport}:{start_time or ''}:{match_key}"


def _manual_event_merge_resolved_event_id(fingerprint: str) -> str:
    return "evt_manual_" + hashlib.md5(fingerprint.encode()).hexdigest()[:20]


def _accepted_event_review_resolved_event_id(fingerprint: str) -> str:
    return "evt_review_" + hashlib.md5(fingerprint.encode()).hexdigest()[:20]


@router.get("/cases", response_model=list[EventReviewCaseOut])
async def list_event_review_cases(
    bookmaker_id: Optional[str] = Query(default=None),
    bookmaker_ids: Optional[str] = Query(default=None),
    sport: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[EventReviewCaseOut]:
    selected_bookmakers = parse_csv_query_values(bookmaker_ids) or []
    if bookmaker_id and bookmaker_id not in selected_bookmakers:
        selected_bookmakers.append(bookmaker_id)

    cases = await odds_store.list_event_review_cases(
        sport=sport,
        status=status,
        limit=limit,
        offset=offset,
        include_variants=True,
    )
    if not selected_bookmakers:
        return cases

    selected = set(selected_bookmakers)
    return [
        case
        for case in cases
        if selected.intersection(case.source_bookmaker_ids)
        or any(variant.bookmaker_id in selected for variant in case.variants)
    ]


@router.post("/cases/{case_id}/accept", response_model=EventReviewActionOut)
async def accept_event_review_case(
    case_id: int,
    payload: Optional[EventReviewAcceptanceIn] = None,
) -> EventReviewActionOut:
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot accept event reviews while a scrape cycle is in progress; try again shortly",
        )

    case = await odds_store.get_event_review_case(case_id, include_variants=True)
    if case is None:
        raise HTTPException(status_code=404, detail="Event review case not found")

    candidate_match_ids = list(
        dict.fromkeys(
            match_id
            for match_id in ([case.primary_match_id] if case.primary_match_id else [])
            + case.candidate_match_ids
            if match_id
        )
    )
    primary_match_id = (
        payload.primary_match_id
        if payload and payload.primary_match_id
        else case.primary_match_id or (candidate_match_ids[0] if candidate_match_ids else None)
    )
    if primary_match_id is None:
        raise HTTPException(
            status_code=400,
            detail="Event review case has no candidate matches to accept",
        )
    if primary_match_id not in candidate_match_ids:
        raise HTTPException(
            status_code=400,
            detail="primary_match_id must be one of the reviewed candidate matches",
        )

    primary_match = await odds_store.get_match(primary_match_id)
    if primary_match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Primary match {primary_match_id} not found",
        )
    if not primary_match.start_time:
        raise HTTPException(
            status_code=400,
            detail="Primary match must have a start_time for manual event merge",
        )

    for match_id in candidate_match_ids:
        match = await odds_store.get_match(match_id)
        if match is None:
            raise HTTPException(status_code=404, detail=f"Candidate match {match_id} not found")
        if match.sport != primary_match.sport:
            raise HTTPException(
                status_code=400,
                detail=f"Candidate match {match_id} sport differs from primary match",
            )
        if (match.start_time or "") != (primary_match.start_time or ""):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Candidate match {match_id} start_time differs from primary; "
                    "event review accept requires an exact start_time match"
                ),
            )

    linkable_variants = [
        variant
        for variant in case.variants
        if variant.match_id in candidate_match_ids and variant.bookmaker_id
    ]
    if not linkable_variants:
        raise HTTPException(
            status_code=400,
            detail="Event review case has no bookmaker source variants to link",
        )

    target_resolved_event_id = case.resolved_event_id or case.candidate_resolved_event_id
    if target_resolved_event_id is None and case.reason_code == "manual_event_merge":
        target_resolved_event_id = _manual_event_merge_resolved_event_id(case.fingerprint)
    elif target_resolved_event_id is None:
        target_resolved_event_id = _accepted_event_review_resolved_event_id(case.fingerprint)

    resolved_event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id=target_resolved_event_id,
            sport=primary_match.sport,
            start_time=primary_match.start_time or case.start_time,
            primary_match_id=primary_match_id,
            confidence=case.confidence,
            method="manual" if case.method == "manual" else "manual_review",
            display_home_team=case.primary_home_team or primary_match.home_team,
            display_away_team=case.primary_away_team or primary_match.away_team,
            display_league_name=case.primary_league_name or primary_match.league_name,
            metadata={
                **case.metadata,
                "review_case_id": case.id,
                "review_fingerprint": case.fingerprint,
            },
        )
    )

    for variant in linkable_variants:
        await odds_store.link_resolved_event_member(
            ResolvedEventMemberIn(
                resolved_event_id=resolved_event_id,
                match_id=variant.match_id,
                bookmaker_id=variant.bookmaker_id,
                orientation=variant.orientation,
                confidence=variant.confidence or case.confidence,
                source_url=variant.source_url,
                source_league_id=variant.source_league_id,
                source_league_name=variant.source_league_name or variant.league_name,
                source_home_team=variant.source_home_team or variant.home_team,
                source_away_team=variant.source_away_team or variant.away_team,
                source_start_time=variant.source_start_time or variant.start_time,
                evidence=variant.evidence or case.evidence,
                metadata={"review_case_id": case.id, "fingerprint": case.fingerprint},
            )
        )

    await odds_store.mark_event_review_case_accepted(
        case_id,
        resolved_event_id=resolved_event_id,
    )
    return EventReviewActionOut(
        case_id=case_id,
        status="accepted",
        resolved_event_id=resolved_event_id,
    )


@router.post("/merge", response_model=EventMergeOut)
async def merge_events(payload: EventMergeIn) -> EventMergeOut:
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot merge events while a scrape cycle is in progress; try again shortly",
        )

    primary_match_id = payload.primary_match_id
    source_match_ids = list(dict.fromkeys(payload.source_match_ids))
    if not source_match_ids:
        raise HTTPException(status_code=400, detail="source_match_ids must not be empty")
    if primary_match_id in source_match_ids:
        raise HTTPException(
            status_code=400,
            detail="primary_match_id must not appear in source_match_ids",
        )

    primary_match = await odds_store.get_match(primary_match_id)
    if primary_match is None:
        raise HTTPException(
            status_code=404,
            detail=f"Primary match {primary_match_id} not found",
        )
    if not primary_match.start_time:
        raise HTTPException(
            status_code=400,
            detail="Primary match must have a start_time for manual event merge",
        )

    candidate_match_ids = [primary_match_id, *source_match_ids]
    source_league_labels = [
        primary_match.league_name or primary_match.league_id or "",
    ]
    for match_id in source_match_ids:
        match = await odds_store.get_match(match_id)
        if match is None:
            raise HTTPException(status_code=404, detail=f"Source match {match_id} not found")
        if match.sport != primary_match.sport:
            raise HTTPException(
                status_code=400,
                detail=f"Source match {match_id} sport differs from primary match",
            )
        if (match.start_time or "") != (primary_match.start_time or ""):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Source match {match_id} start_time differs from primary; "
                    "manual event merge requires an exact start_time match"
                ),
            )
        if match.league_name or match.league_id:
            source_league_labels.append(match.league_name or match.league_id or "")

    fingerprint = _manual_event_merge_fingerprint(
        sport=primary_match.sport,
        start_time=primary_match.start_time,
        match_ids=candidate_match_ids,
    )
    case_id = await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint=fingerprint,
            sport=primary_match.sport,
            start_time=primary_match.start_time or "",
            primary_match_id=primary_match_id,
            candidate_match_ids=candidate_match_ids,
            reason_code="manual_event_merge",
            confidence=1.0,
            method="manual",
            source_league_labels=list(dict.fromkeys(label for label in source_league_labels if label)),
            evidence=[
                "Manual event-only merge accepted by operator",
                "Canonical team aliases were not merged",
            ],
            metadata={"source": "tracked_matches_merge"},
        )
    )
    action = await accept_event_review_case(
        case_id,
        EventReviewAcceptanceIn(primary_match_id=primary_match_id),
    )
    if action.resolved_event_id is None:
        raise HTTPException(
            status_code=500,
            detail="Event merge accepted but no resolved_event_id was returned",
        )

    resolved_event = await odds_store.get_resolved_event(action.resolved_event_id)
    linked_match_ids = []
    if resolved_event is not None:
        linked_match_ids = list(dict.fromkeys(member.match_id for member in resolved_event.members))

    return EventMergeOut(
        resolved_event_id=action.resolved_event_id,
        primary_match_id=primary_match_id,
        linked_match_ids=linked_match_ids,
        linked_member_count=len(resolved_event.members) if resolved_event is not None else 0,
    )


@router.post("/cases/{case_id}/decline", response_model=EventReviewActionOut)
async def decline_event_review_case(case_id: int) -> EventReviewActionOut:
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot decline event reviews while a scrape cycle is in progress; try again shortly",
        )

    case = await odds_store.get_event_review_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Event review case not found")
    await odds_store.mark_event_review_case_declined(case_id)
    return EventReviewActionOut(case_id=case_id, status="declined")
