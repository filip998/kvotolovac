from __future__ import annotations

from fastapi import APIRouter

from .canonical_teams import router as canonical_teams_router
from .event_review import router as event_review_router
from .matches import router as matches_router
from .opportunities import router as opportunities_router
from .scraper_benchmarks import router as scraper_benchmarks_router
from .status import router as status_router
from .team_review import router as team_review_router
from .unresolved_odds import router as unresolved_odds_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(canonical_teams_router)
api_router.include_router(event_review_router)
api_router.include_router(matches_router)
api_router.include_router(opportunities_router)
api_router.include_router(scraper_benchmarks_router)
api_router.include_router(status_router)
api_router.include_router(team_review_router)
api_router.include_router(unresolved_odds_router)
