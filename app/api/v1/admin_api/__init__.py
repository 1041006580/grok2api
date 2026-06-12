"""Admin API router (app_key protected)."""

from fastapi import APIRouter

from app.api.v1.admin_api.cache import router as cache_router
from app.api.v1.admin_api.config import router as config_router
from app.api.v1.admin_api.token import router as tokens_router
from app.api.v1.admin_api.logs import router as logs_router
from app.api.v1.admin_api.metrics import router as metrics_router
from app.api.v1.admin_api.xai_keys import router as xai_keys_router

router = APIRouter()

router.include_router(config_router)
router.include_router(tokens_router)
router.include_router(cache_router)
router.include_router(logs_router)
router.include_router(metrics_router)
router.include_router(xai_keys_router)

__all__ = ["router"]
