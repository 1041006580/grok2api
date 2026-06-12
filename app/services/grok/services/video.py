"""
Grok video generation service.
"""

import asyncio
import uuid
import re
from typing import Any, AsyncGenerator, AsyncIterable, Dict, Optional
from urllib.parse import urlparse

import orjson
from curl_cffi.requests.errors import RequestsError

from app.core.logger import logger
from app.core.mask import mask_token_for_log
from app.core.config import get_config, feature_enabled
from app.core.exceptions import (
    UpstreamException,
    AppException,
    ValidationException,
    ErrorType,
    StreamIdleTimeoutError,
)
from app.services.grok.services.model import ModelService
from app.services.token import get_token_manager, EffortType
from app.services.grok.utils.stream import wrap_stream_with_usage
from app.services.grok.utils.process import (
    BaseProcessor,
    _with_idle_timeout,
    _normalize_line,
    _is_http2_error,
)
from app.services.grok.utils.retry import rate_limited
from app.services.reverse.app_chat import AppChatReverse
from app.services.reverse.media_post import MediaPostReverse
from app.services.reverse.video_upscale import VideoUpscaleReverse
from app.services.reverse.utils.session import ResettableSession
from app.services.reverse.utils.urls import resolve_asset_url
from app.services.token.manager import BASIC_POOL_NAME, SUPER_POOL_NAME
from app.services.image_origin import (
    ORIGIN_GENERATED,
    ORIGIN_UNKNOWN,
    ORIGIN_UPLOADED,
    REFERENCE_BASE64,
    REFERENCE_GENERATED_URL,
    REFERENCE_UNKNOWN_URL,
    REFERENCE_UPLOADED_URL,
    get_image_origin_ledger,
    inspect_image_reference,
    is_http_url,
    sha256_of_image_base64,
)

HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)

_VIDEO_SEMAPHORE = None
_VIDEO_SEM_VALUE = 0

def _get_video_semaphore() -> asyncio.Semaphore:
    """Reverse 接口并发控制（video 服务）。"""
    global _VIDEO_SEMAPHORE, _VIDEO_SEM_VALUE
    value = max(1, int(get_config("video.concurrent")))
    if value != _VIDEO_SEM_VALUE:
        _VIDEO_SEM_VALUE = value
        _VIDEO_SEMAPHORE = asyncio.Semaphore(value)
    return _VIDEO_SEMAPHORE


def _new_session() -> ResettableSession:
    browser = get_config("proxy.browser")
    if browser:
        return ResettableSession(impersonate=browser)
    return ResettableSession()


def _extract_video_url(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        return ""

    md_match = re.search(r"\[video\]\(([^)\s]+)\)", content)
    if md_match:
        return md_match.group(1).strip()

    html_match = re.search(r"""<source[^>]+src=["']([^"']+)["']""", content)
    if html_match:
        return html_match.group(1).strip()

    url_match = re.search(r"""https?://[^\s"'<>]+""", content)
    if url_match:
        return url_match.group(0).strip().rstrip(".,)")

    return ""


def _extract_post_id(video_url: str) -> Optional[str]:
    if not video_url:
        return None
    match = re.search(r"/generated/([0-9a-zA-Z-]{6,64})/", video_url)
    if match:
        return match.group(1)
    match = re.search(r"/([0-9a-zA-Z-]{6,64})/generated_video", video_url)
    if match:
        return match.group(1)
    return None


def _round_length_for_video(pool_name: Optional[str], target_length: int) -> int:
    if pool_name == BASIC_POOL_NAME:
        return 6
    if pool_name == SUPER_POOL_NAME:
        return 10 if target_length <= 10 else 15
    return 6


def _build_extension_start_times(target_length: int, round_length: int) -> list[float]:
    starts: list[float] = []
    current_total = round_length
    while current_total < target_length:
        next_total = min(target_length, current_total + round_length)
        starts.append(float(next_total - round_length))
        current_total = next_total
    return starts


def _video_meta_from_result(result: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(result, dict):
        return {}
    meta = result.get("_video_meta")
    if isinstance(meta, dict):
        return meta
    extracted = {}
    if isinstance(result.get("raw_video_url"), str):
        extracted["raw_video_url"] = result["raw_video_url"]
    if isinstance(result.get("raw_thumbnail_url"), str):
        extracted["raw_thumbnail_url"] = result["raw_thumbnail_url"]
    if isinstance(result.get("post_id"), str):
        extracted["post_id"] = result["post_id"]
    return extracted


def _video_extra_cookies_from_token_info(token_info: Any) -> str:
    if not token_info:
        return ""
    note = getattr(token_info, "note", "") or ""
    if not isinstance(note, str):
        note = str(note)
    note = note.strip()
    if not note:
        return ""
    lowered = note.lower()
    if lowered.startswith("cookie:"):
        return note.split(":", 1)[1].strip()
    if lowered.startswith("cookies:"):
        return note.split(":", 1)[1].strip()
    if note.startswith("x-userid="):
        return note
    return ""


def _extract_upstream_error_message(error: UpstreamException) -> str:
    if not isinstance(error, UpstreamException):
        return ""
    details = getattr(error, "details", None) or {}
    body = details.get("body")
    payload: Any = body
    if isinstance(body, str):
        text = body.strip()
        if not text:
            return ""
        try:
            payload = orjson.loads(text)
        except orjson.JSONDecodeError:
            return text
    if isinstance(payload, dict):
        nested = payload.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return ""


def _fallback_round_length_from_error(
    error: UpstreamException, requested_round_length: int
) -> Optional[int]:
    if not isinstance(error, UpstreamException):
        return None
    details = getattr(error, "details", None) or {}
    status = details.get("status")
    if status != 400:
        return None
    message = _extract_upstream_error_message(error)
    if not message:
        # Some upstream 400 responses on the streaming chat endpoint come back
        # with an empty body even though the same request is rejected as >10s
        # in the browser. Preserve the fallback for 15s-style round requests.
        if requested_round_length > 10:
            return 10
        return None
    match = re.search(
        r"Video duration must be between 1 and (\d+) seconds, got (\d+)",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    max_allowed = int(match.group(1))
    requested = int(match.group(2))
    if requested != requested_round_length:
        return None
    if max_allowed <= 0 or max_allowed >= requested_round_length:
        return None
    return max_allowed


class VideoService:
    """Video generation service."""

    def __init__(self):
        self.timeout = None

    async def create_post(
        self,
        token: str,
        prompt: str,
        media_type: str = "MEDIA_POST_TYPE_VIDEO",
        media_url: str = None,
        extra_cookies: str | None = None,
        referer_override: str | None = None,
    ) -> str:
        """Create media post and return post ID."""
        try:
            if media_type == "MEDIA_POST_TYPE_IMAGE" and not media_url:
                raise ValidationException("media_url is required for image posts")

            prompt_value = prompt if media_type == "MEDIA_POST_TYPE_VIDEO" else ""
            media_value = media_url or ""

            async with _new_session() as session:
                async with _get_video_semaphore():
                    response = await MediaPostReverse.request(
                        session,
                        token,
                        media_type,
                        media_value,
                        prompt=prompt_value,
                        extra_cookies=extra_cookies,
                        referer_override=referer_override,
                    )

            try:
                body = response.json()
            except Exception as json_err:
                raw = ""
                try:
                    raw = response.text[:500]
                except Exception:
                    pass
                logger.error(f"Create post: failed to parse response JSON: {json_err}, raw={raw}")
                raise UpstreamException("Create post: invalid JSON response")

            post_id = body.get("post", {}).get("id", "") if isinstance(body, dict) else ""
            if not post_id:
                logger.error(f"Create post: no post ID, body={str(body)[:300]}")
                raise UpstreamException("No post ID in response")

            logger.info(f"Media post created: {post_id} (type={media_type})")
            return post_id

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Create post error: {e}")
            raise UpstreamException(f"Create post error: {str(e)}")

    async def create_image_post(
        self,
        token: str,
        image_url: str,
        extra_cookies: str | None = None,
        referer_override: str | None = None,
    ) -> str:
        """Create image post and return post ID."""
        return await self.create_post(
            token,
            prompt="",
            media_type="MEDIA_POST_TYPE_IMAGE",
            media_url=image_url,
            extra_cookies=extra_cookies,
            referer_override=referer_override,
        )

    @staticmethod
    def _extract_first_text_url(messages: list) -> Optional[str]:
        """Extract the first HTTP URL from the last user message text."""
        for msg in reversed(messages or []):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            candidates = []
            if isinstance(content, str):
                candidates = HTTP_URL_PATTERN.findall(content)
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        candidates.extend(HTTP_URL_PATTERN.findall(item.get("text", "")))
            for candidate in candidates:
                if is_http_url(candidate):
                    return candidate
            break
        return None

    @staticmethod
    async def _record_uploaded_origin(
        source_input: str, asset_url: str, asset_id: str, kind_hint: str,
    ):
        """Record an uploaded image origin in the ledger."""
        ledger = get_image_origin_ledger()
        metadata = {
            "kind": kind_hint,
            "source_input_is_url": bool(is_http_url(source_input)),
        }
        sha256_hash = ""
        if kind_hint == REFERENCE_BASE64:
            sha256_hash = sha256_of_image_base64(source_input) or ""
        await ledger.upsert_origin(
            source_type=ORIGIN_UPLOADED,
            canonical_url=asset_url,
            original_url=source_input,
            sha256_hash=sha256_hash,
            asset_id=asset_id or "",
            metadata=metadata,
        )

    @staticmethod
    def _recover_generated_url_from_proxy(image_ref: str) -> Optional[str]:
        """Recover original generated URL from a proxy path."""
        normalized = (image_ref or "").strip()
        if not normalized:
            return None
        if is_http_url(normalized):
            path = urlparse(normalized).path or ""
        else:
            path = normalized
        marker = "/v1/files/image/"
        lower_path = path.lower()
        idx = lower_path.find(marker)
        if idx < 0:
            return None
        suffix = path[idx + len(marker):].lstrip("/")
        if not suffix:
            return None
        if suffix.lower().startswith("imagine-public/"):
            return f"https://imagine-public.x.ai/{suffix}"
        return f"https://assets.grok.com/{suffix}"

    @staticmethod
    def _build_uploadable_url_for_local_path(image_ref: str) -> Optional[str]:
        """Build a full URL for a local asset path."""
        normalized = (image_ref or "").strip()
        if not normalized:
            return None
        if is_http_url(normalized):
            return normalized
        if not normalized.startswith("/"):
            return None
        app_url = str(get_config("app.app_url", "")).strip().rstrip("/")
        if not app_url:
            return None
        return f"{app_url}{normalized}"

    @staticmethod
    async def _resolve_video_image_source(
        messages: list, image_attachments: list, token: str,
    ) -> Dict[str, Any]:
        """Resolve image source for video generation with origin tracking."""
        from app.services.grok.utils.upload import UploadService
        ledger = get_image_origin_ledger()

        if image_attachments:
            attach_data = image_attachments[0]
            info = inspect_image_reference(attach_data)
            kind = info.get("kind")
            normalized = info.get("normalized") or attach_data
            asset_id = info.get("asset_id")

            if kind == REFERENCE_GENERATED_URL:
                generated_url = normalized if is_http_url(normalized) else ""
                matched = await ledger.find_by_url(normalized)
                if matched:
                    candidate = (matched.get("original_url") or matched.get("canonical_url") or "").strip()
                    if candidate and is_http_url(candidate):
                        generated_url = candidate
                if not generated_url:
                    generated_url = VideoService._recover_generated_url_from_proxy(normalized) or ""
                if generated_url:
                    await ledger.upsert_origin(
                        source_type=ORIGIN_GENERATED, canonical_url=generated_url,
                        original_url=attach_data, metadata={"via": "openai_image_url"},
                    )
                    return {"image_url": generated_url, "source_type": ORIGIN_GENERATED, "file_attachments": []}
                uploadable = VideoService._build_uploadable_url_for_local_path(attach_data)
                if uploadable:
                    upload_service = UploadService()
                    try:
                        asset_id, file_uri = await upload_service.upload_file(uploadable, token)
                        image_url = f"https://assets.grok.com/{file_uri}"
                        await VideoService._record_uploaded_origin(
                            source_input=uploadable, asset_url=image_url,
                            asset_id=asset_id, kind_hint=REFERENCE_UNKNOWN_URL,
                        )
                        return {"image_url": image_url, "source_type": ORIGIN_UPLOADED,
                                "file_attachments": [asset_id] if asset_id else []}
                    finally:
                        await upload_service.close()
                await ledger.upsert_origin(
                    source_type=ORIGIN_GENERATED, canonical_url=normalized,
                    original_url=attach_data, metadata={"via": "openai_image_url"},
                )
                return {"image_url": normalized, "source_type": ORIGIN_GENERATED, "file_attachments": []}

            if kind == REFERENCE_UPLOADED_URL:
                await ledger.upsert_origin(
                    source_type=ORIGIN_UPLOADED, canonical_url=normalized,
                    original_url=attach_data, asset_id=asset_id or "",
                    metadata={"via": "openai_image_url"},
                )
                return {"image_url": normalized, "source_type": ORIGIN_UPLOADED,
                        "file_attachments": [asset_id] if asset_id else []}

            if kind == REFERENCE_BASE64:
                image_hash = sha256_of_image_base64(attach_data)
                if image_hash:
                    matched = await ledger.find_by_hash(image_hash)
                    if matched and matched.get("source_type") == ORIGIN_GENERATED:
                        generated_url = matched.get("canonical_url") or matched.get("original_url")
                        if generated_url:
                            return {"image_url": generated_url, "source_type": ORIGIN_GENERATED, "file_attachments": []}
                upload_service = UploadService()
                try:
                    asset_id, file_uri = await upload_service.upload_file(attach_data, token)
                    image_url = f"https://assets.grok.com/{file_uri}"
                    await VideoService._record_uploaded_origin(
                        source_input=attach_data, asset_url=image_url,
                        asset_id=asset_id, kind_hint=REFERENCE_BASE64,
                    )
                    return {"image_url": image_url, "source_type": ORIGIN_UPLOADED,
                            "file_attachments": [asset_id] if asset_id else []}
                finally:
                    await upload_service.close()

            if is_http_url(attach_data):
                upload_service = UploadService()
                try:
                    asset_id, file_uri = await upload_service.upload_file(attach_data, token)
                    image_url = f"https://assets.grok.com/{file_uri}"
                    await VideoService._record_uploaded_origin(
                        source_input=attach_data, asset_url=image_url,
                        asset_id=asset_id, kind_hint=REFERENCE_UNKNOWN_URL,
                    )
                    return {"image_url": image_url, "source_type": ORIGIN_UPLOADED,
                            "file_attachments": [asset_id] if asset_id else []}
                finally:
                    await upload_service.close()

        # Fallback: check for URL in message text
        text_link = VideoService._extract_first_text_url(messages)
        if text_link:
            info = inspect_image_reference(text_link)
            kind = info.get("kind")
            normalized_url = info.get("normalized") or text_link
            asset_id = info.get("asset_id")

            if kind == REFERENCE_GENERATED_URL:
                generated_url = normalized_url if is_http_url(normalized_url) else ""
                matched = await ledger.find_by_url(normalized_url)
                if matched:
                    candidate = (matched.get("original_url") or matched.get("canonical_url") or "").strip()
                    if candidate and is_http_url(candidate):
                        generated_url = candidate
                if not generated_url:
                    generated_url = VideoService._recover_generated_url_from_proxy(normalized_url) or ""
                if generated_url:
                    await ledger.upsert_origin(
                        source_type=ORIGIN_GENERATED, canonical_url=generated_url,
                        original_url=text_link, metadata={"via": "user_text_url"},
                    )
                    return {"image_url": generated_url, "source_type": ORIGIN_GENERATED, "file_attachments": []}
                await ledger.upsert_origin(
                    source_type=ORIGIN_GENERATED, canonical_url=normalized_url,
                    original_url=text_link, metadata={"via": "user_text_url"},
                )
                return {"image_url": normalized_url, "source_type": ORIGIN_GENERATED, "file_attachments": []}

            if kind == REFERENCE_UPLOADED_URL:
                await ledger.upsert_origin(
                    source_type=ORIGIN_UPLOADED, canonical_url=normalized_url,
                    original_url=text_link, asset_id=asset_id or "",
                    metadata={"via": "user_text_url"},
                )
                return {"image_url": normalized_url, "source_type": ORIGIN_UPLOADED,
                        "file_attachments": [asset_id] if asset_id else []}

        return {"image_url": None, "source_type": ORIGIN_UNKNOWN, "file_attachments": []}

    async def generate(
        self,
        token: str,
        prompt: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution_name: str = "480p",
        preset: str = "normal",
        grok_model: str = "grok-3",
        model_mode: str | None = None,
        extra_cookies: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video."""
        logger.info(
            f"Video generation: prompt='{prompt[:50]}...', ratio={aspect_ratio}, length={video_length}s, preset={preset}"
        )
        post_id = await self.create_post(
            token,
            prompt,
            extra_cookies=extra_cookies,
            referer_override="https://grok.com/imagine",
        )
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        mode_flag = mode_map.get(preset, "--mode=custom")
        message = f"{prompt} {mode_flag}"
        model_config_override = {
            "modelMap": {
                "videoGenModelConfig": {
                    "aspectRatio": aspect_ratio,
                    "parentPostId": post_id,
                    "resolutionName": resolution_name,
                    "videoLength": video_length,
                }
            }
        }
        payload_override = AppChatReverse.build_video_payload(
            message=message,
            model=grok_model,
            tool_overrides={"videoGen": True},
            model_config_override=model_config_override,
        )

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model=grok_model,
                        mode=None,
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                        payload_override=payload_override,
                        referer_override="https://grok.com/imagine",
                        extra_cookies=extra_cookies,
                    )
                    logger.info(f"Video generation started: post_id={post_id}")
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video generation error: {str(e)}")

        return _stream()

    async def generate_from_image(
        self,
        token: str,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
        file_attachments: Optional[list] = None,
        grok_model: str = "grok-3",
        model_mode: str | None = None,
        extra_cookies: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video from image."""
        logger.info(
            f"Image to video: prompt='{prompt[:50]}...', image={image_url[:80]}"
        )
        effective_image_url = image_url
        effective_file_attachments = list(file_attachments or [])

        try:
            post_id = await self.create_image_post(
                token,
                effective_image_url,
                extra_cookies=extra_cookies,
                referer_override="https://grok.com/imagine",
            )
        except UpstreamException as e:
            status = (e.details or {}).get("status") if getattr(e, "details", None) else None
            if status != 400:
                raise
            from app.services.grok.utils.upload import UploadService
            logger.warning(
                f"Create image post failed with 400, fallback to re-upload: {effective_image_url}"
            )
            upload_service = UploadService()
            try:
                asset_id, file_uri = await upload_service.upload_file(effective_image_url, token)
                effective_image_url = f"https://assets.grok.com/{file_uri}"
                if asset_id and asset_id not in effective_file_attachments:
                    effective_file_attachments.append(asset_id)
                await VideoService._record_uploaded_origin(
                    source_input=image_url, asset_url=effective_image_url,
                    asset_id=asset_id, kind_hint=REFERENCE_UNKNOWN_URL,
                )
            finally:
                await upload_service.close()
            post_id = await self.create_image_post(
                token,
                effective_image_url,
                extra_cookies=extra_cookies,
                referer_override="https://grok.com/imagine",
            )
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        mode_flag = mode_map.get(preset, "--mode=custom")
        message = f"{prompt} {mode_flag}"
        model_config_override = {
            "modelMap": {
                "videoGenModelConfig": {
                    "aspectRatio": aspect_ratio,
                    "parentPostId": post_id,
                    "resolutionName": resolution,
                    "videoLength": video_length,
                }
            }
        }
        payload_override = AppChatReverse.build_video_payload(
            message=message,
            model=grok_model,
            file_attachments=effective_file_attachments or None,
            tool_overrides={"videoGen": True},
            model_config_override=model_config_override,
        )

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model=grok_model,
                        mode=None,
                        file_attachments=effective_file_attachments or None,
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                        payload_override=payload_override,
                        referer_override="https://grok.com/imagine",
                        extra_cookies=extra_cookies,
                    )
                    logger.info(f"Video generation started: post_id={post_id}")
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video generation error: {str(e)}")

        return _stream()

    async def generate_extension(
        self,
        token: str,
        prompt: str,
        parent_post_id: str,
        original_post_id: str,
        start_time: float,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution_name: str = "480p",
        preset: str = "normal",
        grok_model: str = "grok-3",
        model_mode: str | None = None,
        extra_cookies: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """Extend a previously generated video."""
        logger.info(
            f"Video extension: prompt='{prompt[:50]}...', parent={parent_post_id}, original={original_post_id}, "
            f"start_time={start_time}, ratio={aspect_ratio}, length={video_length}s"
        )
        mode_map = {
            "fun": "--mode=extremely-crazy",
            "normal": "--mode=normal",
            "spicy": "--mode=extremely-spicy-or-crazy",
        }
        mode_flag = mode_map.get(preset, "--mode=custom")
        message = f"{prompt} {mode_flag}"
        model_config_override = {
            "modelMap": {
                "videoGenModelConfig": {
                    "isVideoExtension": True,
                    "videoExtensionStartTime": float(start_time),
                    "extendPostId": parent_post_id,
                    "stitchWithExtendPostId": True,
                    "originalPrompt": prompt,
                    "originalPostId": original_post_id,
                    "originalRefType": "ORIGINAL_REF_TYPE_VIDEO_EXTENSION",
                    "mode": "custom",
                    "aspectRatio": aspect_ratio,
                    "videoLength": video_length,
                    "resolutionName": resolution_name,
                    "parentPostId": parent_post_id,
                    "isVideoEdit": False,
                }
            }
        }
        payload_override = AppChatReverse.build_video_payload(
            message=message,
            model=grok_model,
            tool_overrides={"videoGen": True},
            model_config_override=model_config_override,
        )

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model=grok_model,
                        mode=None,
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
                        payload_override=payload_override,
                        referer_override="https://grok.com/imagine",
                        extra_cookies=extra_cookies,
                    )
                    logger.info(
                        f"Video extension started: parent_post_id={parent_post_id}, start_time={start_time}"
                    )
                    async for line in stream_response:
                        yield line
            except Exception as e:
                try:
                    await session.close()
                except Exception:
                    pass
                logger.error(f"Video extension error: {e}")
                if isinstance(e, AppException):
                    raise
                raise UpstreamException(f"Video extension error: {str(e)}")

        return _stream()

    @staticmethod
    async def completions(
        model: str,
        messages: list,
        stream: bool = None,
        reasoning_effort: str | None = None,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
    ):
        """Video generation entrypoint."""
        # Auto-set defaults based on model
        is_super = model == "grok-imagine-1.0-video-super"
        request_model_info = ModelService.get(model)
        grok_model = getattr(request_model_info, "grok_model", "grok-3")
        model_mode = getattr(request_model_info, "model_mode", None)
        if video_length is None or (is_super and video_length == 6):
            video_length = 15 if is_super else 6
        if resolution is None or (is_super and resolution == "480p"):
            resolution = "720p" if is_super else "480p"

        # Get token via intelligent routing.
        token_mgr = await get_token_manager()
        await token_mgr.reload_if_stale()

        max_token_retries = int(get_config("retry.max_retry"))
        last_error: Exception | None = None

        if reasoning_effort is None:
            show_think = get_config("app.thinking")
        else:
            show_think = reasoning_effort != "none"
        is_stream = stream if stream is not None else get_config("app.stream")
        target_length = int(video_length or 6)

        # Extract content.
        from app.services.grok.services.chat import MessageExtractor

        prompt, file_attachments, image_attachments = MessageExtractor.extract(messages)

        for attempt in range(max_token_retries):
            # Select token based on video requirements and pool candidates.
            pool_candidates = ModelService.pool_candidates_for_model(model)
            token_info = token_mgr.get_token_for_video(
                resolution=resolution,
                video_length=video_length,
                pool_candidates=pool_candidates,
            )

            if not token_info:
                if last_error:
                    raise last_error
                raise AppException(
                    message="No available tokens. Please try again later.",
                    error_type=ErrorType.RATE_LIMIT.value,
                    code="rate_limit_exceeded",
                    status_code=429,
                )

            # Extract token string from TokenInfo.
            token = token_info.token
            if token.startswith("sso="):
                token = token[4:]
            extra_cookies = _video_extra_cookies_from_token_info(token_info)
            pool_name = token_mgr.get_pool_name_for_token(token) or BASIC_POOL_NAME
            should_upscale = resolution == "720p" and pool_name == BASIC_POOL_NAME
            forced_round_length: Optional[int] = None
            duration_fallback_used = False

            inflight = feature_enabled("token.inflight_enabled", False)
            if inflight:
                token_mgr.acquire_token(token)
            stream_transferred = False
            try:
                while True:
                    round_length = forced_round_length or _round_length_for_video(
                        pool_name, target_length
                    )
                    use_auto_extension = (not is_stream) and target_length > round_length

                    try:
                        # Resolve image source with origin tracking.
                        source_info = await VideoService._resolve_video_image_source(
                            messages, image_attachments, token
                        )
                        image_url = source_info.get("image_url")
                        source_type = source_info.get("source_type", ORIGIN_UNKNOWN)
                        origin_file_attachments = source_info.get("file_attachments") or []

                        # Generate video.
                        service = VideoService()
                        if use_auto_extension:
                            logger.info(
                                f"Video auto extension enabled: target_length={target_length}s, round_length={round_length}s"
                            )
                            if image_url:
                                logger.info(
                                    f"Video image source resolved: source_type={source_type}, "
                                    f"has_file_attachments={bool(origin_file_attachments)}"
                                )
                                first_response = await service.generate_from_image(
                                    token,
                                    prompt,
                                    image_url,
                                    aspect_ratio,
                                    round_length,
                                    resolution,
                                    preset,
                                    file_attachments=origin_file_attachments,
                                    grok_model=grok_model,
                                    model_mode=model_mode,
                                    extra_cookies=extra_cookies,
                                )
                            else:
                                first_response = await service.generate(
                                    token,
                                    prompt,
                                    aspect_ratio,
                                    round_length,
                                    resolution,
                                    preset,
                                    grok_model=grok_model,
                                    model_mode=model_mode,
                                    extra_cookies=extra_cookies,
                                )

                            first_result = await VideoCollectProcessor(
                                model, token, upscale_on_finish=False
                            ).process(first_response)
                            current_result = first_result
                            first_meta = _video_meta_from_result(current_result)
                            current_content = (
                                (first_result.get("choices") or [{}])[0]
                                .get("message", {})
                                .get("content", "")
                            )
                            current_video_url = first_meta.get("raw_video_url") or _extract_video_url(current_content)
                            original_post_id = first_meta.get("post_id") or _extract_post_id(current_video_url)
                            last_post_id = original_post_id

                            if not last_post_id:
                                raise UpstreamException(
                                    message="Video auto extension failed: missing first round post id",
                                    details={"status": 502, "type": "missing_post_id"},
                                )

                            extension_starts = _build_extension_start_times(
                                target_length, round_length
                            )

                            for index, start_time in enumerate(extension_starts, start=1):
                                is_last = index == len(extension_starts)
                                extension_response = await service.generate_extension(
                                    token,
                                    prompt,
                                    parent_post_id=last_post_id,
                                    original_post_id=original_post_id,
                                    start_time=start_time,
                                    aspect_ratio=aspect_ratio,
                                    video_length=round_length,
                                    resolution_name=resolution,
                                    preset=preset,
                                    grok_model=grok_model,
                                    model_mode=model_mode,
                                    extra_cookies=extra_cookies,
                                )
                                current_result = await VideoCollectProcessor(
                                    model, token, upscale_on_finish=is_last and should_upscale
                                ).process(extension_response)
                                current_meta = _video_meta_from_result(current_result)
                                current_content = (
                                    (current_result.get("choices") or [{}])[0]
                                    .get("message", {})
                                    .get("content", "")
                                )
                                current_video_url = current_meta.get("raw_video_url") or _extract_video_url(current_content)
                                next_post_id = current_meta.get("post_id") or _extract_post_id(current_video_url)
                                if not next_post_id and not is_last:
                                    raise UpstreamException(
                                        message="Video auto extension failed: missing round post id",
                                        details={"status": 502, "type": "missing_post_id", "round": index + 1},
                                    )
                                if next_post_id:
                                    last_post_id = next_post_id

                            result = current_result
                        else:
                            if image_url:
                                logger.info(
                                    f"Video image source resolved: source_type={source_type}, "
                                    f"has_file_attachments={bool(origin_file_attachments)}"
                                )
                                response = await service.generate_from_image(
                                    token,
                                    prompt,
                                    image_url,
                                    aspect_ratio,
                                    round_length,
                                    resolution,
                                    preset,
                                    file_attachments=origin_file_attachments,
                                    grok_model=grok_model,
                                    model_mode=model_mode,
                                    extra_cookies=extra_cookies,
                                )
                            else:
                                response = await service.generate(
                                    token,
                                    prompt,
                                    aspect_ratio,
                                    round_length,
                                    resolution,
                                    preset,
                                    grok_model=grok_model,
                                    model_mode=model_mode,
                                    extra_cookies=extra_cookies,
                                )

                        # Process response.
                        if is_stream:
                            processor = VideoStreamProcessor(
                                model,
                                token,
                                show_think,
                                upscale_on_finish=should_upscale,
                            )
                            stream_transferred = True
                            return wrap_stream_with_usage(
                                processor.process(response), token_mgr, token, model
                            )

                        if not use_auto_extension:
                            result = await VideoCollectProcessor(
                                model, token, upscale_on_finish=should_upscale
                            ).process(response)
                        if isinstance(result, dict) and "_video_meta" in result:
                            result = dict(result)
                            result.pop("_video_meta", None)
                        try:
                            model_info = request_model_info or ModelService.get(model)
                            effort = (
                                EffortType.HIGH
                                if (model_info and model_info.cost.value == "high")
                                else EffortType.LOW
                            )
                            mode = ModelService.quota_mode_for_model(model)
                            await token_mgr.consume(token, effort, mode=mode)
                            logger.debug(
                                f"Video completed, recorded usage (effort={effort.value}, mode={mode})"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to record video usage: {e}")
                        return result

                    except UpstreamException as e:
                        fallback_round_length = _fallback_round_length_from_error(
                            e, round_length
                        )
                        if (
                            not is_stream
                            and not duration_fallback_used
                            and fallback_round_length is not None
                        ):
                            duration_fallback_used = True
                            forced_round_length = fallback_round_length
                            logger.warning(
                                f"Video round length {round_length}s rejected by upstream; "
                                f"retrying with {forced_round_length}s rounds for target_length={target_length}s"
                            )
                            continue

                        last_error = e
                        if rate_limited(e):
                            await token_mgr.mark_rate_limited(
                                token, mode=ModelService.quota_mode_for_model(model)
                            )
                            logger.warning(
                                f"Token {mask_token_for_log(token)} rate limited (429), "
                                f"trying next token (attempt {attempt + 1}/{max_token_retries})"
                            )
                            break
                        raise

            finally:
                if inflight and not stream_transferred:
                    token_mgr.release_token(token)
        if last_error:
            raise last_error
        raise AppException(
            message="No available tokens. Please try again later.",
            error_type=ErrorType.RATE_LIMIT.value,
            code="rate_limit_exceeded",
            status_code=429,
        )


class VideoStreamProcessor(BaseProcessor):
    """Video stream response processor."""

    def __init__(
        self,
        model: str,
        token: str = "",
        show_think: bool = None,
        upscale_on_finish: bool = False,
    ):
        super().__init__(model, token)
        self.response_id: Optional[str] = None
        self.think_opened: bool = False
        self.role_sent: bool = False

        self.show_think = bool(show_think)
        self.upscale_on_finish = bool(upscale_on_finish)

    @staticmethod
    def _extract_video_id(video_url: str) -> str:
        if not video_url:
            return ""
        match = re.search(r"/generated/([0-9a-fA-F-]{32,36})/", video_url)
        if match:
            return match.group(1)
        match = re.search(r"/([0-9a-fA-F-]{32,36})/generated_video", video_url)
        if match:
            return match.group(1)
        return ""

    async def _upscale_video_url(self, video_url: str) -> str:
        if not video_url or not self.upscale_on_finish:
            return video_url
        video_id = self._extract_video_id(video_url)
        if not video_id:
            logger.warning("Video upscale skipped: unable to extract video id")
            return video_url
        try:
            async with _new_session() as session:
                response = await VideoUpscaleReverse.request(
                    session, self.token, video_id
                )
            payload = response.json() if response is not None else {}
            hd_url = payload.get("hdMediaUrl") if isinstance(payload, dict) else None
            if hd_url:
                logger.info(f"Video upscale completed: {hd_url}")
                return hd_url
        except Exception as e:
            logger.warning(f"Video upscale failed: {e}")
        return video_url

    def _sse(self, content: str = "", role: str = None, finish: str = None) -> str:
        """Build SSE response."""
        delta = {}
        if role:
            delta["role"] = role
            delta["content"] = ""
        elif content:
            delta["content"] = content

        chunk = {
            "id": self.response_id or f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [
                {"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}
            ],
        }
        return f"data: {orjson.dumps(chunk).decode()}\n\n"

    async def process(
        self, response: AsyncIterable[bytes]
    ) -> AsyncGenerator[str, None]:
        """Process video stream response."""
        idle_timeout = get_config("video.stream_timeout")
        last_progress = -1

        try:
            async for line in _with_idle_timeout(response, idle_timeout, self.model):
                line = _normalize_line(line)
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                resp = data.get("result", {}).get("response", {})
                is_thinking = bool(resp.get("isThinking"))

                if rid := resp.get("responseId"):
                    self.response_id = rid

                if not self.role_sent:
                    yield self._sse(role="assistant")
                    self.role_sent = True

                if token := resp.get("token"):
                    if is_thinking:
                        if not self.show_think:
                            continue
                        if not self.think_opened:
                            yield self._sse("<think>\n")
                            self.think_opened = True
                    else:
                        if self.think_opened:
                            yield self._sse("\n</think>\n")
                            self.think_opened = False
                    yield self._sse(token)
                    continue

                if video_resp := resp.get("streamingVideoGenerationResponse"):
                    progress = video_resp.get("progress", 0)
                    last_progress = progress

                    if self.show_think:
                        if not self.think_opened:
                            yield self._sse("<think>\n")
                            self.think_opened = True
                        yield self._sse(f"正在生成视频中，当前进度{progress}%\n")

                    if progress == 100:
                        video_url = video_resp.get("videoUrl", "")
                        thumbnail_url = video_resp.get("thumbnailImageUrl", "")
                        is_moderated = video_resp.get("moderated", False)

                        if self.think_opened:
                            yield self._sse("\n</think>\n")
                            self.think_opened = False

                        if not video_url and is_moderated:
                            logger.warning("Video moderated by upstream, no videoUrl returned")
                            yield self._sse("视频已被上游内容审核拦截，无法生成。\n")
                        elif video_url:
                            if self.upscale_on_finish:
                                yield self._sse("正在对视频进行超分辨率\n")
                                video_url = await self._upscale_video_url(video_url)
                            video_url = resolve_asset_url(video_url)
                            thumbnail_url = resolve_asset_url(thumbnail_url) if thumbnail_url else ""
                            dl_service = self._get_dl()
                            rendered = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            yield self._sse(rendered)

                            logger.info(f"Video generated: {video_url}")
                        else:
                            logger.warning(
                                f"Video progress 100%% but no videoUrl, response={video_resp}"
                            )
                            yield self._sse("视频生成完成但未返回视频链接，请重试。\n")
                    continue

            if last_progress >= 0 and last_progress != 100:
                logger.warning(
                    f"Video stream ended at progress {last_progress}%% (expected 100%%)"
                )
            if self.think_opened:
                yield self._sse("</think>\n")
            yield self._sse(finish="stop")
            yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.debug(
                "Video stream cancelled by client", extra={"model": self.model}
            )
        except StreamIdleTimeoutError as e:
            raise UpstreamException(
                message=f"Video stream idle timeout after {e.idle_seconds}s",
                status_code=504,
                details={
                    "error": str(e),
                    "type": "stream_idle_timeout",
                    "idle_seconds": e.idle_seconds,
                },
            )
        except RequestsError as e:
            if _is_http2_error(e):
                logger.warning(
                    f"HTTP/2 stream error in video: {e}", extra={"model": self.model}
                )
                raise UpstreamException(
                    message="Upstream connection closed unexpectedly",
                    status_code=502,
                    details={"error": str(e), "type": "http2_stream_error"},
                )
            logger.error(
                f"Video stream request error: {e}", extra={"model": self.model}
            )
            raise UpstreamException(
                message=f"Upstream request failed: {e}",
                status_code=502,
                details={"error": str(e)},
            )
        except Exception as e:
            logger.error(
                f"Video stream processing error: {e}",
                extra={"model": self.model, "error_type": type(e).__name__},
            )
            raise
        finally:
            await self.close()


class VideoCollectProcessor(BaseProcessor):
    """Video non-stream response processor."""

    def __init__(self, model: str, token: str = "", upscale_on_finish: bool = False):
        super().__init__(model, token)
        self.upscale_on_finish = bool(upscale_on_finish)

    @staticmethod
    def _extract_video_id(video_url: str) -> str:
        if not video_url:
            return ""
        match = re.search(r"/generated/([0-9a-fA-F-]{32,36})/", video_url)
        if match:
            return match.group(1)
        match = re.search(r"/([0-9a-fA-F-]{32,36})/generated_video", video_url)
        if match:
            return match.group(1)
        return ""

    async def _upscale_video_url(self, video_url: str) -> str:
        if not video_url or not self.upscale_on_finish:
            return video_url
        video_id = self._extract_video_id(video_url)
        if not video_id:
            logger.warning("Video upscale skipped: unable to extract video id")
            return video_url
        try:
            async with _new_session() as session:
                response = await VideoUpscaleReverse.request(
                    session, self.token, video_id
                )
            payload = response.json() if response is not None else {}
            hd_url = payload.get("hdMediaUrl") if isinstance(payload, dict) else None
            if hd_url:
                logger.info(f"Video upscale completed: {hd_url}")
                return hd_url
        except Exception as e:
            logger.warning(f"Video upscale failed: {e}")
        return video_url

    async def process(self, response: AsyncIterable[bytes]) -> dict[str, Any]:
        """Process and collect video response."""
        response_id = ""
        content = ""
        raw_video_url = ""
        raw_thumbnail_url = ""
        post_id = ""
        idle_timeout = get_config("video.stream_timeout")

        try:
            async for line in _with_idle_timeout(response, idle_timeout, self.model):
                line = _normalize_line(line)
                if not line:
                    continue
                try:
                    data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue

                resp = data.get("result", {}).get("response", {})

                if video_resp := resp.get("streamingVideoGenerationResponse"):
                    if video_resp.get("progress") == 100:
                        response_id = resp.get("responseId", "")
                        video_url = video_resp.get("videoUrl", "")
                        thumbnail_url = video_resp.get("thumbnailImageUrl", "")
                        is_moderated = video_resp.get("moderated", False)
                        raw_video_url = video_url or ""
                        raw_thumbnail_url = thumbnail_url or ""
                        post_id = _extract_post_id(raw_video_url) or ""

                        if not video_url and is_moderated:
                            logger.warning("Video moderated by upstream, no videoUrl returned")
                            content = "视频已被上游内容审核拦截，无法生成。\n"
                        elif video_url:
                            if self.upscale_on_finish:
                                video_url = await self._upscale_video_url(video_url)
                            video_url = resolve_asset_url(video_url)
                            thumbnail_url = resolve_asset_url(thumbnail_url) if thumbnail_url else ""
                            dl_service = self._get_dl()
                            content = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            logger.info(f"Video generated: {video_url}")
                        else:
                            logger.warning(
                                f"Video progress 100%% but no videoUrl, response={video_resp}"
                            )
                            content = "视频生成完成但未返回视频链接，请重试。\n"

        except asyncio.CancelledError:
            logger.debug(
                "Video collect cancelled by client", extra={"model": self.model}
            )
            raise
        except StreamIdleTimeoutError as e:
            logger.warning(
                f"Video collect idle timeout: {e}", extra={"model": self.model}
            )
            raise UpstreamException(
                message=f"Video collect idle timeout after {e.idle_seconds}s",
                status_code=504,
                details={"error": str(e), "type": "stream_idle_timeout"},
            )
        except RequestsError as e:
            if _is_http2_error(e):
                logger.warning(
                    f"HTTP/2 stream error in video collect: {e}",
                    extra={"model": self.model},
                )
                raise UpstreamException(
                    message="Upstream connection closed unexpectedly",
                    status_code=502,
                    details={"error": str(e), "type": "http2_stream_error"},
                )
            else:
                logger.error(
                    f"Video collect request error: {e}", extra={"model": self.model}
                )
                raise UpstreamException(
                    message=f"Upstream request failed: {e}",
                    status_code=502,
                    details={"error": str(e)},
                )
        except Exception as e:
            logger.error(
                f"Video collect processing error: {e}",
                extra={"model": self.model, "error_type": type(e).__name__},
            )
            raise
        finally:
            await self.close()

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
            "_video_meta": {
                "raw_video_url": raw_video_url,
                "raw_thumbnail_url": raw_thumbnail_url,
                "post_id": post_id,
            },
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "refusal": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


__all__ = ["VideoService"]
