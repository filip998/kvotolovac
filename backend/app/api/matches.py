from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ._params import parse_csv_query_values
from ..models.schemas import (
    MatchMergeIn,
    MatchMergeOut,
    MatchMergeTeamPairing,
    MatchOut,
    OddsOut,
    OutcomeOfferOut,
)
from ..services.scheduler import scheduler
from ..services.team_registry import (
    merge_canonical_teams,
    validate_canonical_team_merge_identity,
)
from ..store import odds_store

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=list[MatchOut])
async def list_matches(
    league_id: Optional[str] = Query(None),
    sport: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    bookmaker_ids: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await odds_store.get_matches(
        league_id=league_id,
        sport=sport,
        status=status,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}", response_model=MatchOut)
async def get_match(match_id: str):
    match = await odds_store.get_match(match_id, require_current_snapshot=True)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match


@router.get("/{match_id}/odds", response_model=list[OddsOut])
async def get_match_odds(match_id: str):
    match = await odds_store.get_match(match_id, require_current_snapshot=True)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_odds_for_match(match_id)


@router.get("/{match_id}/market-offers", response_model=list[OutcomeOfferOut])
async def get_match_market_offers(
    match_id: str,
    bookmaker_ids: Optional[str] = Query(None),
    market_type: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    match = await odds_store.get_match(match_id, require_current_snapshot=True)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return await odds_store.get_outcome_offers_for_match(
        match_id,
        bookmaker_ids=parse_csv_query_values(bookmaker_ids),
        market_type=market_type,
        limit=limit,
        offset=offset,
    )


@router.get("/{match_id}/history", response_model=list[OddsOut])
async def get_match_history(match_id: str):
    match = await odds_store.get_match(match_id, require_current_snapshot=True)
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

    # Validate team pairings: every distinct source->target team relationship must
    # reference real teams and pass identity guardrails before match rows mutate.
    # Submitted pairings are the only ones persisted after a successful match merge,
    # but derived home/away pairings are also prevalidated so clients cannot bypass
    # qualifier checks by omitting pairings.
    requested_pairing_map: dict[int, int] = {}
    validation_pairing_map: dict[int, int] = {}
    explicit_pairing_source_team_ids: set[int] = set()
    target_team_ids = {
        team_id
        for team_id in (target_match.home_team_id, target_match.away_team_id)
        if team_id and team_id > 0
    }
    source_team_ids = {
        team_id
        for source_match in source_matches
        for team_id in (source_match.home_team_id, source_match.away_team_id)
        if team_id and team_id > 0
    }

    def add_pairing(
        pairing_map: dict[int, int],
        *,
        source_team_id: int | None,
        target_team_id: int | None,
    ) -> None:
        if not source_team_id or not target_team_id:
            return
        if source_team_id <= 0 or target_team_id <= 0:
            return
        if source_team_id == target_team_id:
            return
        existing = pairing_map.get(source_team_id)
        if existing is not None and existing != target_team_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Conflicting pairings for source team {source_team_id}: "
                    f"both {existing} and {target_team_id}"
                ),
            )
        pairing_map[source_team_id] = target_team_id

    def add_derived_pairing(
        *,
        source_team_id: int | None,
        target_team_id: int | None,
    ) -> None:
        if source_team_id in explicit_pairing_source_team_ids:
            return
        add_pairing(
            validation_pairing_map,
            source_team_id=source_team_id,
            target_team_id=target_team_id,
        )

    for pairing in payload.team_pairings:
        if pairing.source_team_id > 0 and pairing.target_team_id > 0:
            if pairing.source_team_id not in source_team_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Source canonical team {pairing.source_team_id} "
                        "is not part of the source matches"
                    ),
                )
            if pairing.target_team_id not in target_team_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Target canonical team {pairing.target_team_id} "
                        "is not part of the target match"
                    ),
                )
            explicit_pairing_source_team_ids.add(pairing.source_team_id)
        add_pairing(
            requested_pairing_map,
            source_team_id=pairing.source_team_id,
            target_team_id=pairing.target_team_id,
        )
        add_pairing(
            validation_pairing_map,
            source_team_id=pairing.source_team_id,
            target_team_id=pairing.target_team_id,
        )
    for source_match in source_matches:
        add_derived_pairing(
            source_team_id=source_match.home_team_id,
            target_team_id=target_match.home_team_id,
        )
        add_derived_pairing(
            source_team_id=source_match.away_team_id,
            target_team_id=target_match.away_team_id,
        )

    # Pre-validate every team pairing before mutating anything: missing teams
    # raise 404, invalid pairings raise 400. We intentionally do NOT call
    # merge_canonical_teams here yet — those mutations happen AFTER the match
    # merge transaction succeeds, so a downstream odds-merge failure cannot
    # leave the team registry partially merged.
    from ..services.team_registry import (
        get_canonical_team,
    )  # local import to avoid cycle at module load
    for source_team_id, target_team_id in validation_pairing_map.items():
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
            validate_canonical_team_merge_identity(
                source_team_id=source_team_id,
                target_team_id=target_team_id,
                allow_unsafe_subset=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    for source_team_id, target_team_id in requested_pairing_map.items():
        try:
            await asyncio.to_thread(
                merge_canonical_teams,
                source_team_id=source_team_id,
                target_team_id=target_team_id,
                allow_unsafe_subset=True,
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
        reassigned_outcome_offers=counts["reassigned_outcome_offers"],
        reassigned_opportunities=counts["reassigned_opportunities"],
        deleted_source_matches=counts["deleted_source_matches"],
    )
