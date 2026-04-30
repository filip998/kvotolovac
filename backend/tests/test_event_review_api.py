from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database import close_db, init_db
from app.main import app
from app.models.schemas import EventReviewCaseIn
from app.scrapers.mock_scraper import MockScraper
from app.scrapers.registry import registry
from app.services.team_registry import create_canonical_team, get_canonical_team
from app.store import odds_store


START_TIME = "2030-01-01T20:00:00+00:00"


@pytest.fixture(autouse=True)
async def setup_app():
    await init_db(settings.db_path)
    registry._scrapers.clear()
    for bm in ("mozzart", "meridian", "maxbet"):
        registry.register(MockScraper(bm))
    yield
    await close_db()


@pytest.fixture
async def client(setup_app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_event_review_case() -> int:
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
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-mozzart",
        bookmaker_id="mozzart",
        source_url="https://mozzart.example/events/1",
    )
    await odds_store.upsert_match_bookmaker_source(
        match_id="match-meridian",
        bookmaker_id="meridian",
        source_url="https://meridian.example/events/42",
    )
    return await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint="basketball:2030-01-01:partizan-zvezda:v1",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            candidate_match_ids=["match-mozzart", "match-meridian"],
            reason_code="candidate_event_equivalence",
            confidence=0.86,
            method="heuristic",
            source_bookmaker_ids=["mozzart", "meridian"],
            source_league_labels=["Euroleague", "Euroleague - Main"],
            evidence=["exact start time", "compatible teams with reversed orientation"],
            metadata={"orientation": "reversed"},
        )
    )


@pytest.mark.asyncio
async def test_event_review_cases_include_source_variants(client: AsyncClient):
    case_id = await _seed_event_review_case()

    resp = await client.get("/api/v1/event-review/cases?status=pending&bookmaker_ids=meridian")

    assert resp.status_code == 200
    cases = resp.json()
    assert [case["id"] for case in cases] == [case_id]
    case = cases[0]
    assert case["primary_home_team"] == "Partizan"
    assert case["primary_away_team"] == "Crvena Zvezda"
    assert case["primary_league_name"] == "Euroleague"
    assert [variant["bookmaker_id"] for variant in case["variants"]] == ["mozzart", "meridian"]
    assert case["variants"][1]["source_url"] == "https://meridian.example/events/42"
    assert case["variants"][1]["source_home_team"] == "Crvena Zvezda"


@pytest.mark.asyncio
async def test_event_review_accept_persists_resolved_event_members(client: AsyncClient):
    case_id = await _seed_event_review_case()

    accept_resp = await client.post(f"/api/v1/event-review/cases/{case_id}/accept")
    accepted_resp = await client.get("/api/v1/event-review/cases?status=accepted")

    assert accept_resp.status_code == 200
    payload = accept_resp.json()
    assert payload["status"] == "accepted"
    assert payload["resolved_event_id"].startswith("evt_")

    accepted_cases = accepted_resp.json()
    assert [case["id"] for case in accepted_cases] == [case_id]
    assert accepted_cases[0]["resolved_event_id"] == payload["resolved_event_id"]

    event = await odds_store.get_resolved_event(payload["resolved_event_id"])
    assert event is not None
    assert event.primary_match_id == "match-mozzart"
    assert event.display_home_team == "Partizan"
    assert [(member.match_id, member.bookmaker_id) for member in event.members] == [
        ("match-mozzart", "mozzart"),
        ("match-meridian", "meridian"),
    ]
    assert event.members[1].source_home_team == "Crvena Zvezda"


@pytest.mark.asyncio
async def test_event_review_accept_rejects_unreviewed_primary_match(client: AsyncClient):
    case_id = await _seed_event_review_case()
    await odds_store.upsert_match(
        id="match-unreviewed",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan II",
        away_team="Crvena Zvezda II",
        start_time=START_TIME,
    )

    response = await client.post(
        f"/api/v1/event-review/cases/{case_id}/accept",
        json={"primary_match_id": "match-unreviewed"},
    )

    assert response.status_code == 400
    assert "reviewed candidate" in response.json()["detail"]


@pytest.mark.asyncio
async def test_event_review_metadata_pairs_fallback_variants_without_source_rows(
    client: AsyncClient,
):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    await odds_store.upsert_bookmaker("book-a", "Book A")
    await odds_store.upsert_bookmaker("book-z", "Book Z")
    await odds_store.upsert_match(
        id="match-z",
        league_id="euroleague",
        sport="basketball",
        home_team="Z Home",
        away_team="Z Away",
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        id="match-a",
        league_id="euroleague",
        sport="basketball",
        home_team="A Home",
        away_team="A Away",
        start_time=START_TIME,
    )
    case_id = await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint="basketball:metadata-pairs:v1",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-z",
            candidate_match_ids=["match-z", "match-a"],
            reason_code="possible_event_equivalence_low_confidence",
            confidence=0.8,
            method="auto_candidate",
            source_bookmaker_ids=["book-a", "book-z"],
            metadata={
                "source_variants": [
                    {"match_id": "match-z", "bookmaker_id": "book-z"},
                    {"match_id": "match-a", "bookmaker_id": "book-a"},
                ]
            },
        )
    )

    cases_response = await client.get("/api/v1/event-review/cases?status=pending")
    accept_response = await client.post(f"/api/v1/event-review/cases/{case_id}/accept")

    assert cases_response.status_code == 200
    case_payload = cases_response.json()[0]
    source_pairs = [
        (variant["match_id"], variant["bookmaker_id"])
        for variant in case_payload["variants"]
        if variant["bookmaker_id"] is not None
    ]
    assert source_pairs == [
        ("match-z", "book-z"),
        ("match-a", "book-a"),
    ]
    assert accept_response.status_code == 200
    event = await odds_store.get_resolved_event(accept_response.json()["resolved_event_id"])
    assert event is not None
    assert [(member.match_id, member.bookmaker_id) for member in event.members] == [
        ("match-z", "book-z"),
        ("match-a", "book-a"),
    ]


@pytest.mark.asyncio
async def test_manual_event_merge_links_resolved_event_without_deleting_or_merging_teams(
    client: AsyncClient,
):
    await _seed_event_review_case()
    target_home = create_canonical_team(display_name="Partizan", sport="basketball")
    target_away = create_canonical_team(display_name="Crvena Zvezda", sport="basketball")
    source_home = create_canonical_team(display_name="Zvezda Meridian", sport="basketball")
    source_away = create_canonical_team(display_name="Partizan Meridian", sport="basketball")
    await odds_store.upsert_match(
        id="match-mozzart",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        home_team_id=target_home.team_id,
        away_team_id=target_away.team_id,
        start_time=START_TIME,
    )
    await odds_store.upsert_match(
        id="match-meridian",
        league_id="euroleague",
        sport="basketball",
        home_team="Crvena Zvezda",
        away_team="Partizan",
        home_team_id=source_home.team_id,
        away_team_id=source_away.team_id,
        start_time=START_TIME,
    )

    response = await client.post(
        "/api/v1/event-review/merge",
        json={
            "primary_match_id": "match-mozzart",
            "source_match_ids": ["match-meridian"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["primary_match_id"] == "match-mozzart"
    assert payload["linked_match_ids"] == ["match-mozzart", "match-meridian"]
    assert payload["linked_member_count"] == 2

    primary_match = await odds_store.get_match("match-mozzart")
    source_match = await odds_store.get_match("match-meridian")
    assert primary_match is not None
    assert source_match is not None
    assert primary_match.home_team_id == target_home.team_id
    assert source_match.home_team_id == source_home.team_id
    assert get_canonical_team(source_home.team_id) is not None
    assert get_canonical_team(source_away.team_id) is not None

    event = await odds_store.get_resolved_event(payload["resolved_event_id"])
    assert event is not None
    assert event.method == "manual"
    assert event.primary_match_id == "match-mozzart"
    assert [(member.match_id, member.bookmaker_id) for member in event.members] == [
        ("match-mozzart", "mozzart"),
        ("match-meridian", "meridian"),
    ]


@pytest.mark.asyncio
async def test_manual_event_merge_requires_exact_start_time(client: AsyncClient):
    await _seed_event_review_case()
    await odds_store.upsert_match(
        id="match-late",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        start_time="2030-01-01T21:00:00+00:00",
    )

    response = await client.post(
        "/api/v1/event-review/merge",
        json={
            "primary_match_id": "match-mozzart",
            "source_match_ids": ["match-late"],
        },
    )

    assert response.status_code == 400
    assert "exact start_time" in response.json()["detail"]


@pytest.mark.asyncio
async def test_manual_event_merge_rejects_missing_start_time_before_creating_case(
    client: AsyncClient,
):
    await odds_store.upsert_league("euroleague", "Euroleague", "basketball", "Europe")
    await odds_store.upsert_bookmaker("mozzart", "Mozzart")
    await odds_store.upsert_bookmaker("meridian", "Meridian")
    await odds_store.upsert_match(
        id="match-no-time-primary",
        league_id="euroleague",
        sport="basketball",
        home_team="Partizan",
        away_team="Crvena Zvezda",
        start_time=None,
    )
    await odds_store.upsert_match(
        id="match-no-time-source",
        league_id="euroleague",
        sport="basketball",
        home_team="KK Partizan",
        away_team="Crvena Zvezda",
        start_time=None,
    )

    response = await client.post(
        "/api/v1/event-review/merge",
        json={
            "primary_match_id": "match-no-time-primary",
            "source_match_ids": ["match-no-time-source"],
        },
    )
    pending_cases = await odds_store.list_event_review_cases(status="pending")

    assert response.status_code == 400
    assert "start_time" in response.json()["detail"]
    assert pending_cases == []


@pytest.mark.asyncio
async def test_manual_event_merge_reuses_resolved_event_after_decline_and_remerge(
    client: AsyncClient,
):
    await _seed_event_review_case()

    first_response = await client.post(
        "/api/v1/event-review/merge",
        json={
            "primary_match_id": "match-mozzart",
            "source_match_ids": ["match-meridian"],
        },
    )
    accepted_cases = await odds_store.list_event_review_cases(
        status="accepted",
        include_variants=False,
    )
    assert first_response.status_code == 200
    assert len(accepted_cases) == 1

    decline_response = await client.post(
        f"/api/v1/event-review/cases/{accepted_cases[0].id}/decline"
    )
    second_response = await client.post(
        "/api/v1/event-review/merge",
        json={
            "primary_match_id": "match-mozzart",
            "source_match_ids": ["match-meridian"],
        },
    )

    first_event_id = first_response.json()["resolved_event_id"]
    second_event_id = second_response.json()["resolved_event_id"]
    manual_events = [
        event
        for event in await odds_store.list_resolved_events(sport="basketball")
        if event.method == "manual"
    ]

    assert decline_response.status_code == 200
    assert second_response.status_code == 200
    assert second_event_id == first_event_id
    assert [event.id for event in manual_events] == [first_event_id]


@pytest.mark.asyncio
async def test_event_review_decline_suppresses_same_fingerprint(client: AsyncClient):
    case_id = await _seed_event_review_case()

    decline_resp = await client.post(f"/api/v1/event-review/cases/{case_id}/decline")
    repeated_case_id = await odds_store.upsert_event_review_case(
        EventReviewCaseIn(
            fingerprint="basketball:2030-01-01:partizan-zvezda:v1",
            sport="basketball",
            start_time=START_TIME,
            primary_match_id="match-mozzart",
            candidate_match_ids=["match-mozzart", "match-meridian"],
            reason_code="candidate_event_equivalence",
            confidence=0.9,
            source_bookmaker_ids=["mozzart", "meridian"],
            evidence=["same fingerprint after another scrape"],
        )
    )
    pending_resp = await client.get("/api/v1/event-review/cases?status=pending")
    declined_resp = await client.get("/api/v1/event-review/cases?status=declined")

    assert decline_resp.status_code == 200
    assert decline_resp.json()["status"] == "declined"
    assert repeated_case_id == case_id
    assert pending_resp.json() == []
    assert [case["id"] for case in declined_resp.json()] == [case_id]
