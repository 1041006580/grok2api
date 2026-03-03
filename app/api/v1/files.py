"""
文件服务 API 路由

本地缓存优先，未命中时从 assets.grok.com 流式代理。
"""

import aiofiles.os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.logger import logger
from app.core.storage import DATA_DIR
from app.services.reverse.assets_download import AssetsDownloadReverse
from app.services.reverse.utils.session import ResettableSession
from app.services.token import get_token_manager
from app.services.grok.utils.download import get_cached_asset_token

router = APIRouter(tags=["Files"])

# 缓存根目录
BASE_DIR = DATA_DIR / "tmp"
IMAGE_DIR = BASE_DIR / "image"
VIDEO_DIR = BASE_DIR / "video"

# 扩展名 -> Content-Type
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    suffix = Path(filename).suffix.lower()
    return _CONTENT_TYPES.get(suffix, fallback)


async def _stream_from_upstream(asset_path: str):
    """从 assets.grok.com 流式代理资源。"""
    # 优先使用生成该资产时的 token（assets.grok.com 要求资产所有者的 token）
    token = get_cached_asset_token(asset_path)
    if token:
        logger.debug(f"File proxy: cache hit for {asset_path}")
    else:
        logger.debug(f"File proxy: cache miss for {asset_path}, falling back to pool")
        tm = await get_token_manager()
        await tm.reload_if_stale()
        token = tm.get_token("ssoBasic") or tm.get_token("ssoSuper")
        if not token:
            result = await tm.refresh_cooling_tokens()
            if result.get("recovered", 0) > 0:
                token = tm.get_token("ssoBasic") or tm.get_token("ssoSuper")
    if not token:
        raise HTTPException(status_code=503, detail="No available token for proxy")

    session = ResettableSession()
    try:
        response = await AssetsDownloadReverse.request(session, token, asset_path)
    except Exception as e:
        await session.close()
        logger.error(f"File proxy failed: {e}")
        raise HTTPException(status_code=502, detail="Upstream download failed")

    upstream_ct = response.headers.get("content-type", "").split(";")[0].strip()
    content_type = upstream_ct or _guess_content_type(asset_path)

    async def _iter():
        try:
            if hasattr(response, "aiter_content"):
                async for chunk in response.aiter_content():
                    if chunk:
                        yield chunk
            else:
                yield response.content
        finally:
            await session.close()

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/image/{filename:path}")
async def get_image(filename: str):
    """获取图片文件（本地缓存优先，否则流式代理）"""
    flat_name = filename.replace("/", "-") if "/" in filename else filename
    file_path = IMAGE_DIR / flat_name

    if await aiofiles.os.path.exists(file_path):
        if await aiofiles.os.path.isfile(file_path):
            return FileResponse(
                file_path,
                media_type=_guess_content_type(flat_name, "image/jpeg"),
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

    # 本地缓存未命中，从上游流式代理
    asset_path = f"/{filename}" if not filename.startswith("/") else filename
    return await _stream_from_upstream(asset_path)


@router.get("/video/{filename:path}")
async def get_video(filename: str):
    """获取视频文件（本地缓存优先，否则流式代理）"""
    flat_name = filename.replace("/", "-") if "/" in filename else filename
    file_path = VIDEO_DIR / flat_name

    if await aiofiles.os.path.exists(file_path):
        if await aiofiles.os.path.isfile(file_path):
            return FileResponse(
                file_path,
                media_type="video/mp4",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

    # 本地缓存未命中，从上游流式代理
    asset_path = f"/{filename}" if not filename.startswith("/") else filename
    return await _stream_from_upstream(asset_path)
