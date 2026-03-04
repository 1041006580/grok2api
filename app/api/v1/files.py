"""
文件服务 API 路由

本地缓存优先，未命中时从 assets.grok.com 流式代理。
"""

from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.logger import logger
from app.services.reverse.assets_download import AssetsDownloadReverse
from app.services.reverse.utils.session import ResettableSession
from app.services.token import get_token_manager
from app.services.grok.utils.download import get_cached_asset_token
from app.services.media_storage import get_media_cache_max_bytes, get_media_storage

router = APIRouter(tags=["Files"])

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
    lower = filename.lower()
    suffix = f".{lower.rsplit('.', 1)[-1]}" if "." in lower else ""
    return _CONTENT_TYPES.get(suffix, fallback)


async def _iter_single_chunk(data: bytes) -> AsyncGenerator[bytes, None]:
    yield data


async def _read_cached_bytes(media_type: str, name: str) -> bytes | None:
    try:
        return await get_media_storage().read_bytes(media_type, name)
    except Exception as e:
        logger.warning(f"File cache read failed ({media_type}/{name}): {e}")
        return None


async def _stream_from_upstream(asset_path: str, media_type: str, cache_name: str):
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
    max_cache_bytes = get_media_cache_max_bytes()

    async def _iter():
        cache_enabled = max_cache_bytes > 0
        cache_buffer = bytearray()
        try:
            if hasattr(response, "aiter_content"):
                async for chunk in response.aiter_content():
                    if chunk:
                        if cache_enabled:
                            if len(cache_buffer) + len(chunk) <= max_cache_bytes:
                                cache_buffer.extend(chunk)
                            else:
                                cache_enabled = False
                                cache_buffer.clear()
                        yield chunk
            else:
                content = response.content
                if cache_enabled:
                    if len(content) <= max_cache_bytes:
                        cache_buffer.extend(content)
                    else:
                        cache_enabled = False
                yield content
        finally:
            await session.close()
            if cache_enabled and cache_buffer:
                try:
                    await get_media_storage().write_bytes(
                        media_type,
                        cache_name,
                        bytes(cache_buffer),
                        content_type=content_type,
                    )
                except Exception as e:
                    logger.warning(
                        f"File proxy cache write failed ({media_type}/{cache_name}): {e}"
                    )

    return StreamingResponse(
        _iter(),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/image/{filename:path}")
async def get_image(filename: str):
    """获取图片文件（本地缓存优先，否则流式代理）"""
    flat_name = filename.replace("/", "-") if "/" in filename else filename
    cached = await _read_cached_bytes("image", flat_name)
    if cached is not None:
        return StreamingResponse(
            _iter_single_chunk(cached),
            media_type=_guess_content_type(flat_name, "image/jpeg"),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    # 本地缓存未命中，从上游流式代理
    asset_path = f"/{filename}" if not filename.startswith("/") else filename
    return await _stream_from_upstream(asset_path, "image", flat_name)


@router.get("/video/{filename:path}")
async def get_video(filename: str):
    """获取视频文件（本地缓存优先，否则流式代理）"""
    flat_name = filename.replace("/", "-") if "/" in filename else filename
    cached = await _read_cached_bytes("video", flat_name)
    if cached is not None:
        return StreamingResponse(
            _iter_single_chunk(cached),
            media_type=_guess_content_type(flat_name, "video/mp4"),
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    # 本地缓存未命中，从上游流式代理
    asset_path = f"/{filename}" if not filename.startswith("/") else filename
    return await _stream_from_upstream(asset_path, "video", flat_name)
