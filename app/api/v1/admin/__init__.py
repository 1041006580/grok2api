"""Admin API router (app_key protected)."""

from fastapi import APIRouter

from app.api.v1.admin.cache import router as cache_router
from app.api.v1.admin.config import router as config_router
from app.api.v1.admin.token import router as tokens_router
from app.api.v1.admin.logs import router as logs_router
from app.api.v1.admin.xai_keys import router as xai_keys_router

router = APIRouter()

router.include_router(config_router)
router.include_router(tokens_router)
router.include_router(cache_router)
router.include_router(logs_router)
router.include_router(xai_keys_router)

__all__ = ["router"]
