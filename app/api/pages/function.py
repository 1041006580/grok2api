from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.core.auth import is_function_enabled

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parents[3]
STATIC_ROOTS = [
    BASE_DIR / "_public" / "static",
    BASE_DIR / "app" / "static",
]


def _function_page_response(relative_paths: list[str]) -> FileResponse:
    for static_root in STATIC_ROOTS:
        for relative_path in relative_paths:
            file_path = static_root / relative_path
            if file_path.exists():
                return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Page not found")


@router.get("/", include_in_schema=False)
async def root():
    if is_function_enabled():
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/admin/login")


@router.get("/login", include_in_schema=False)
async def function_login():
    if not is_function_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _function_page_response(["function/pages/login.html", "public/pages/login.html"])


@router.get("/imagine", include_in_schema=False)
async def function_imagine():
    if not is_function_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _function_page_response(["function/pages/imagine.html", "public/pages/imagine.html"])


@router.get("/voice", include_in_schema=False)
async def function_voice():
    if not is_function_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _function_page_response(["function/pages/voice.html", "public/pages/voice.html"])


@router.get("/video", include_in_schema=False)
async def function_video():
    if not is_function_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _function_page_response(["function/pages/video.html", "public/pages/video.html"])


@router.get("/chat", include_in_schema=False)
async def function_chat():
    if not is_function_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    return _function_page_response(["function/pages/chat.html", "public/pages/chat.html"])


__all__ = ["router"]
