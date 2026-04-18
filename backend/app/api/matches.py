from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import MatchMergeIn, MatchMergeOut, MatchMergeTeamPairing, MatchOut, OddsOut
from ..services.scheduler import scheduler
from ..services.team_registry import merge_canonical_teams
from ..store import odds_store

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
async def list_matches(
    league_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    bookmaker_ids: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_matches(
        league_id=league_id,
        status=status,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}", response_model=MatchOut)
async def get_match(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.get("/{match_id}/odds", response_model=list[OddsOut])
async def get_match_odds(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_odds_for_match(match_id)


@router.get("/{match_id}/history", response_model=list[OddsOut])
async def get_match_history(match_id: str):
    match = await odds_store.get_match(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_odds_history_for_match(match_id)


@router.post("/merge", response_model=MatchMergeOut)
async def merge_matches(payload: MatchMergeIn) -> MatchMergeOut:
    if scheduler.is_cycle_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Cannot merge matches while a scrape cycle is in progress; try again shortly",
        )

    target_id = payload.target_match_id
    source_ids = list(dict.fromkeys(payload.source_match_ids))
    if not source_ids:
        raise HTTPException(status_code=400, detail="source_match_ids must not be empty")
    if target_id in source_ids:
        raise HTTPException(
            status_code=400,
            detail="target_match_id must not appear in source_match_ids",
        )

    target_match = await odds_store.get_match(target_id)
    if target_match is None:
        raise HTTPException(status_code=404, detail=f"Target match {target_id} not found")

    source_matches: list[MatchOut] = []
    for sid in source_ids:
        match = await odds_store.get_match(sid)
        if match is None:
            raise HTTPException(status_code=404, detail=f"Source match {sid} not found")
        if match.sport != target_match.sport:
            raise HTTPException(
                status_code=400,
                detail=f"Source match {sid} sport ({match.sport}) does not match target sport ({target_match.sport})",
            )
        if (match.start_time or "") != (target_match.start_time or ""):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Source match {sid} start_time differs from target; "
                    "matches must share an exact start_time to be merged"
                ),
            )
        source_matches.append(match)

    # Validate team pairings: every distinct (source_team_id -> target_team_id) requested
    # must reference real teams. The frontend computes pairings from match home/away
    # team IDs; we honor whatever it sends but reject self-pairings of different teams
    # going to the same target inconsistently.
    pairing_map: dict[int, int] = {}
    for pairing in payload.team_pairings:
        if pairing.source_team_id <= 0 or pairing.target_team_id <= 0:
            continue
        if pairing.source_team_id == pairing.target_team_id:
            continue
        existing = pairing_map.get(pairing.source_team_id)
        if existing is not None and existing != pairing.target_team_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Conflicting pairings for source team {pairing.source_team_id}: "
                    f"both {existing} and {pairing.target_team_id}"
                ),
            )
        pairing_map[pairing.source_team_id] = pairing.target_team_id

    # Pre-validate every team pairing before mutating anything: missing teams
    # raise 404, invalid pairings raise 400. We intentionally do NOT call
    # merge_canonical_teams here yet — those mutations happen AFTER the match
    # merge transaction succeeds, so a downstream odds-merge failure cannot
    # leave the team registry partially merged.
    from ..services.team_registry import (
        get_canonical_team,
    )  # local import to avoid cycle at module load
    for source_team_id, target_team_id in pairing_map.items():
        if get_canonical_team(source_team_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Source canonical team {source_team_id} not found",
            )
        if get_canonical_team(target_team_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"Target canonical team {target_team_id} not found",
            )

    try:
        counts = await odds_store.merge_matches(
            target_match_id=target_id,
            source_match_ids=source_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Match merge succeeded — now persist team aliases so the next scrape
    # auto-consolidates. If an individual team merge fails here, we do NOT
    # roll back the match merge (matches are already collapsed, which is what
    # the user requested); we surface a 500 explaining which pairings still
    # need to be merged manually via the canonical-teams endpoint.
    merged_pairings: list[MatchMergeTeamPairing] = []
    failed_pairings: list[tuple[int, int, str]] = []
    for source_team_id, target_team_id in pairing_map.items():
        try:
            await asyncio.to_thread(
                merge_canonical_teams,
                source_team_id=source_team_id,
                target_team_id=target_team_id,
            )
            merged_pairings.append(
                MatchMergeTeamPairing(
                    source_team_id=source_team_id,
                    target_team_id=target_team_id,
                )
            )
        except ValueError as exc:
            failed_pairings.append((source_team_id, target_team_id, str(exc)))

    if failed_pairings:
        details = "; ".join(
            f"{src}→{tgt}: {msg}" for src, tgt, msg in failed_pairings
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Matches were merged but the following team pairings failed and "
                f"must be retried via /canonical-teams/.../merge: {details}"
            ),
        )

    return MatchMergeOut(
        target_match_id=target_id,
        merged_source_match_ids=source_ids,
        merged_team_ids=merged_pairings,
        reassigned_odds=counts["reassigned_odds"],
        reassigned_odds_history=counts["reassigned_odds_history"],
        reassigned_discrepancies=counts["reassigned_discrepancies"],
        deleted_source_matches=counts["deleted_source_matches"],
    )
