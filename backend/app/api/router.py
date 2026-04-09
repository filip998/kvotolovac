from __future__ import annotations

from fastapi import APIRouter

from .discrepancies import router as discrepancies_router
from .matches import router as matches_router
from .status import router as status_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(discrepancies_router)
api_router.include_router(matches_router)
api_router.include_router(status_router)
