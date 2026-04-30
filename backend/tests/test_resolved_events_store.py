from __future__ import annotations

import pytest

from app.database import get_db
from app.models.schemas import EventReviewCaseIn, ResolvedEventIn, ResolvedEventMemberIn
from app.store import odds_store


START_TIME = "2030-01-01T20:00:00+00:00"


async def _seed_matches() -> None:
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match(
        id="match-mozzart",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        id="match-meridian",
        league_id="euroleague",
        sport="basketball",
        home_team="Crvena Zvezda",
        away_team="Partizan",
        start_time=START_TIME,
    )


@pytest.mark.asyncio
async def test_upsert_resolved_event_and_members_with_source_variants():
    await _seed_matches()

    event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-partizan-zvezda",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            confidence=0.93,
            method="manual_review",
            display_home_team="Partizan",
            display_away_team="Crvena Zvezda",
            display_league_name="Euroleague",
            metadata={"source_leagues": ["Euroleague", "Euroleague RS"]},
        )
    )
    assert event_id == "evt-partizan-zvezda"

    mozzart_member_id = await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id=event_id,
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            orientation="as_listed",
            confidence=0.98,
            source_url="https://mozzart.example/events/1",
            source_league_id="el",
            source_league_name="Euroleague",
            source_home_team="Partizan",
            source_away_team="Crvena Zvezda",
            source_start_time=START_TIME,
            evidence=["same exact start time"],
            metadata={"raw_event_id": "mzt-1"},
        )
    )
    meridian_member_id = await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id=event_id,
            match_id="match-meridian",
            bookmaker_id="meridian",
            orientation="reversed",
            confidence=0.88,
            source_url="https://meridian.example/events/42",
            source_league_name="Euroleague - Main",
            source_home_team="Crvena Zvezda",
            source_away_team="Partizan",
            source_start_time=START_TIME,
            evidence=["home/away reversed but teams match"],
            metadata={"raw_event_id": "mer-42"},
        )
    )

    updated_meridian_member_id = await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id=event_id,
            match_id="match-meridian",
            bookmaker_id="meridian",
            orientation="reversed",
            confidence=0.91,
            evidence=["same candidate after second scrape"],
            metadata={"raw_event_id": "mer-42", "source_conflicts": 0},
        )
    )

    assert updated_meridian_member_id == meridian_member_id

    event = await odds_store.get_resolved_event(event_id)
    assert event is not None
    assert event.primary_match_id == "match-mozzart"
    assert event.metadata == {"source_leagues": ["Euroleague", "Euroleague RS"]}
    assert [member.id for member in event.members] == [mozzart_member_id, meridian_member_id]

    meridian_member = await odds_store.get_resolved_event_member(
        match_id="match-meridian",
        bookmaker_id="meridian",
    )
    assert meridian_member is not None
    assert meridian_member.source_url == "https://meridian.example/events/42"
    assert meridian_member.source_home_team == "Crvena Zvezda"
    assert meridian_member.confidence == 0.91
    assert meridian_member.evidence == ["same candidate after second scrape"]
    assert meridian_member.metadata == {"raw_event_id": "mer-42", "source_conflicts": 0}

    db = await get_db()
    member_rows = await db.execute_fetchall(
        """SELECT COUNT(*) AS c
           FROM resolved_event_members
           WHERE match_id = ? AND bookmaker_id = ?""",
        ("match-meridian", "meridian"),
    )
    source_rows = await db.execute_fetchall(
        """SELECT source_url
           FROM match_bookmaker_sources
           WHERE match_id = ? AND bookmaker_id = ?""",
        ("match-meridian", "meridian"),
    )
    listed = await odds_store.list_resolved_events(sport="basketball", status="active")

    assert member_rows[0]["c"] == 1
    assert source_rows[0]["source_url"] == "https://meridian.example/events/42"
    assert [event.id for event in listed] == [event_id]


@pytest.mark.asyncio
async def test_event_review_cases_persist_fingerprint_decisions():
    await _seed_matches()
    event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-review-target",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            confidence=0.9,
            method="manual_review",
        )
    )

    case = EventReviewCaseIn(
        fingerprint="basketball:2030-01-01:partizan-zvezda:v1",
        sport="basketball",
        start_time=START_TIME,
        primary_match_id="match-mozzart",
        candidate_resolved_event_id=event_id,
        candidate_match_ids=["match-mozzart", "match-meridian"],
        reason_code="candidate_event_equivalence",
        confidence=0.82,
        method="heuristic",
        source_bookmaker_ids=["mozzart", "meridian"],
        source_league_labels=["Euroleague", "Euroleague - Main"],
        evidence=["exact start time", "compatible team aliases"],
        metadata={"orientation": "reversed"},
    )
    case_id = await odds_store.upsert_event_review_case(case)

    pending_cases = await odds_store.list_event_review_cases(status="pending")
    assert [pending.id for pending in pending_cases] == [case_id]
    assert pending_cases[0].candidate_match_ids == ["match-mozzart", "match-meridian"]

    await odds_store.mark_event_review_case_declined(case_id)
    declined = await odds_store.get_event_review_case_by_fingerprint(
        case.fingerprint,
        statuses=["declined"],
    )
    assert declined is not None
    assert declined.status == "declined"
    assert declined.declined_at is not None

    repeated_case_id = await odds_store.upsert_event_review_case(
        case.model_copy(update={"status": "pending", "evidence": ["same fingerprint again"]})
    )
    repeated = await odds_store.get_event_review_case(repeated_case_id)
    assert repeated_case_id == case_id
    assert repeated is not None
    assert repeated.status == "declined"
    assert repeated.evidence == ["same fingerprint again"]

    changed_case_id = await odds_store.upsert_event_review_case(
        case.model_copy(
            update={
                "fingerprint": "basketball:2030-01-01:partizan-zvezda:v2",
                "evidence": ["source evidence changed"],
            }
        )
    )
    await odds_store.mark_event_review_case_accepted(
        changed_case_id,
        resolved_event_id=event_id,
    )

    accepted = await odds_store.get_event_review_case_by_fingerprint(
        "basketball:2030-01-01:partizan-zvezda:v2",
        statuses=["accepted"],
    )
    assert accepted is not None
    assert accepted.resolved_event_id == event_id
    assert accepted.accepted_at is not None
    assert (
        await odds_store.get_event_review_case_by_fingerprint(
            "basketball:2030-01-01:partizan-zvezda:v2",
            statuses=["declined"],
        )
        is None
    )


@pytest.mark.asyncio
async def test_auto_event_link_does_not_steal_manual_member():
    await _seed_matches()
    manual_event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-manual-partizan-zvezda",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            method="manual",
        )
    )
    auto_event_id = await odds_store.upsert_resolved_event(
        ResolvedEventIn(
            id="evt-auto-partizan-zvezda",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            method="exact",
        )
    )
    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id=manual_event_id,
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            evidence=["operator accepted event merge"],
        )
    )

    await odds_store.link_resolved_event_member(
        ResolvedEventMemberIn(
            resolved_event_id=auto_event_id,
            match_id="match-mozzart",
            bookmaker_id="mozzart",
            evidence=["next scrape exact resolver"],
        )
    )

    member = await odds_store.get_resolved_event_member(
        match_id="match-mozzart",
        bookmaker_id="mozzart",
    )
    manual_event = await odds_store.get_resolved_event(manual_event_id)
    auto_event = await odds_store.get_resolved_event(auto_event_id)

    assert member is not None
    assert member.resolved_event_id == manual_event_id
    assert member.evidence == ["operator accepted event merge"]
    assert manual_event is not None
    assert [(row.match_id, row.bookmaker_id) for row in manual_event.members] == [
        ("match-mozzart", "mozzart")
    ]
    assert auto_event is not None
    assert auto_event.members == []


@pytest.mark.asyncio
async def test_eligible_resolved_event_members_filter_player_resolution_scope():
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    event_specs = [
        ("evt-exact", "match-exact", "mozzart", "exact", "active", "active"),
        ("evt-high", "match-high", "meridian", "auto_fuzzy_high", "active", "active"),
        ("evt-manual", "match-manual", "maxbet", "manual_review", "active", "active"),
        (
            "evt-candidate",
            "match-candidate",
            "candidate",
            "auto_candidate",
            "active",
            "active",
        ),
        (
            "evt-declined",
            "match-declined",
            "declined",
            "manual_review",
            "declined",
            "active",
        ),
        (
            "evt-inactive-member",
            "match-inactive",
            "inactive",
            "exact",
            "active",
            "inactive",
        ),
    ]
    for _, match_id, bookmaker_id, _, _, _ in event_specs:
        await odds_store.upsert_bookmaker(bookmaker_id, bookmaker_id.title())
        await odds_store.upsert_match(
            id=match_id,
            league_id="euroleague",
            sport="basketball",
            home_team=f"Home {match_id}",
            away_team=f"Away {match_id}",
            start_time=START_TIME,
        )

    for member_id, (
        event_id,
        match_id,
        bookmaker_id,
        method,
        event_status,
        member_status,
    ) in enumerate(event_specs, start=1):
        await odds_store.upsert_resolved_event(
            ResolvedEventIn(
                id=event_id,
                sport="basketball",
                start_time=START_TIME,
                primary_match_id=match_id,
                status=event_status,
                method=method,
            )
        )
        await odds_store.link_resolved_event_member(
            ResolvedEventMemberIn(
                resolved_event_id=event_id,
                match_id=match_id,
                bookmaker_id=bookmaker_id,
                status=member_status,
                evidence=[f"member {member_id}"],
            )
        )

    members = await odds_store.get_eligible_resolved_event_members_for_matches(
        [match_id for _, match_id, _, _, _, _ in event_specs]
    )

    assert [member.resolved_event_id for member in members] == [
        "evt-exact",
        "evt-high",
        "evt-manual",
    ]
