from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parents[3]
STATIC_DIRS = [
    BASE_DIR / "_public" / "static",
    BASE_DIR / "app" / "static",
]


def _admin_page_response(relative_path: str) -> FileResponse:
    for static_dir in STATIC_DIRS:
        file_path = static_dir / relative_path
        if file_path.exists():
            return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Page not found")


@router.get("/admin", include_in_schema=False)
async def admin_root():
    return RedirectResponse(url="/admin/login")


@router.get("/admin/login", include_in_schema=False)
async def admin_login():
    return _admin_page_response("admin/pages/login.html")


@router.get("/admin/config", include_in_schema=False)
async def admin_config():
    return _admin_page_response("admin/pages/config.html")


@router.get("/admin/cache", include_in_schema=False)
async def admin_cache():
    return _admin_page_response("admin/pages/cache.html")


@router.get("/admin/token", include_in_schema=False)
async def admin_token():
    return _admin_page_response("admin/pages/token.html")


@router.get("/admin/logs", include_in_schema=False)
async def admin_logs():
    return _admin_page_response("admin/pages/logs.html")


@router.get("/admin/xai-keys", include_in_schema=False)
async def admin_xai_keys():
    return _admin_page_response("admin/pages/xai-keys.html")
