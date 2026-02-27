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
from app.core.config import get_config
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
from app.services.token.manager import BASIC_POOL_NAME
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
                    )

            post_id = response.json().get("post", {}).get("id", "")
            if not post_id:
                raise UpstreamException("No post ID in response")

            logger.info(f"Media post created: {post_id} (type={media_type})")
            return post_id

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Create post error: {e}")
            raise UpstreamException(f"Create post error: {str(e)}")

    async def create_image_post(self, token: str, image_url: str) -> str:
        """Create image post and return post ID."""
        return await self.create_post(
            token, prompt="", media_type="MEDIA_POST_TYPE_IMAGE", media_url=image_url
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
        aspect_ratio: str = None,
        video_length: int = 6,
        resolution_name: str = "480p",
        preset: str = None,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video."""
        if preset is None:
            preset = get_config("video.default_mode") or "normal"
        if aspect_ratio is None:
            aspect_ratio = get_config("video.default_aspect_ratio") or "16:9"
        logger.info(
            f"Video generation: prompt='{prompt[:50]}...', ratio={aspect_ratio}, length={video_length}s, preset={preset}"
        )
        post_id = await self.create_post(token, prompt)
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

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model="grok-3",
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
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
        aspect_ratio: str = None,
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = None,
        file_attachments: Optional[list] = None,
    ) -> AsyncGenerator[bytes, None]:
        """Generate video from image."""
        if preset is None:
            preset = get_config("video.default_mode") or "normal"
        if aspect_ratio is None:
            aspect_ratio = get_config("video.default_aspect_ratio") or "16:9"
        logger.info(
            f"Image to video: prompt='{prompt[:50]}...', image={image_url[:80]}"
        )
        effective_image_url = image_url
        effective_file_attachments = list(file_attachments or [])

        try:
            post_id = await self.create_image_post(token, effective_image_url)
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
            post_id = await self.create_image_post(token, effective_image_url)
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

        async def _stream():
            session = _new_session()
            try:
                async with _get_video_semaphore():
                    stream_response = await AppChatReverse.request(
                        session,
                        token,
                        message=message,
                        model="grok-3",
                        file_attachments=effective_file_attachments or None,
                        tool_overrides={"videoGen": True},
                        model_config_override=model_config_override,
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

    @staticmethod
    async def completions(
        model: str,
        messages: list,
        stream: bool = None,
        reasoning_effort: str | None = None,
        aspect_ratio: str = None,
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = None,
    ):
        """Video generation entrypoint."""
        if preset is None:
            preset = get_config("video.default_mode") or "normal"
        if aspect_ratio is None:
            aspect_ratio = get_config("video.default_aspect_ratio") or "16:9"
        # 根据模型自动设置默认值
        is_super = model == "grok-imagine-1.0-video-super"
        if video_length is None or (is_super and video_length == 6):
            video_length = 10 if is_super else 6
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
            pool_name = token_mgr.get_pool_name_for_token(token)
            should_upscale = resolution == "720p" and pool_name == BASIC_POOL_NAME

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
                        video_length,
                        resolution,
                        preset,
                        file_attachments=origin_file_attachments,
                    )
                else:
                    response = await service.generate(
                        token,
                        prompt,
                        aspect_ratio,
                        video_length,
                        resolution,
                        preset,
                    )

                # Process response.
                if is_stream:
                    processor = VideoStreamProcessor(
                        model,
                        token,
                        show_think,
                        upscale_on_finish=should_upscale,
                    )
                    return wrap_stream_with_usage(
                        processor.process(response), token_mgr, token, model
                    )

                result = await VideoCollectProcessor(
                    model, token, upscale_on_finish=should_upscale
                ).process(response)
                try:
                    model_info = ModelService.get(model)
                    effort = (
                        EffortType.HIGH
                        if (model_info and model_info.cost.value == "high")
                        else EffortType.LOW
                    )
                    await token_mgr.consume(token, effort)
                    logger.debug(
                        f"Video completed, recorded usage (effort={effort.value})"
                    )
                except Exception as e:
                    logger.warning(f"Failed to record video usage: {e}")
                return result

            except UpstreamException as e:
                last_error = e
                if rate_limited(e):
                    await token_mgr.mark_rate_limited(token)
                    logger.warning(
                        f"Token {token[:10]}... rate limited (429), "
                        f"trying next token (attempt {attempt + 1}/{max_token_retries})"
                    )
                    continue
                raise

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
                            dl_service = self._get_dl()
                            rendered = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            yield self._sse(rendered)

                            logger.info(f"Video generated: {video_url}")
                    continue

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

                        if not video_url and is_moderated:
                            logger.warning("Video moderated by upstream, no videoUrl returned")
                            content = "视频已被上游内容审核拦截，无法生成。\n"
                        elif video_url:
                            if self.upscale_on_finish:
                                video_url = await self._upscale_video_url(video_url)
                            dl_service = self._get_dl()
                            content = await dl_service.render_video(
                                video_url, self.token, thumbnail_url
                            )
                            logger.info(f"Video generated: {video_url}")

        except asyncio.CancelledError:
            logger.debug(
                "Video collect cancelled by client", extra={"model": self.model}
            )
        except StreamIdleTimeoutError as e:
            logger.warning(
                f"Video collect idle timeout: {e}", extra={"model": self.model}
            )
        except RequestsError as e:
            if _is_http2_error(e):
                logger.warning(
                    f"HTTP/2 stream error in video collect: {e}",
                    extra={"model": self.model},
                )
            else:
                logger.error(
                    f"Video collect request error: {e}", extra={"model": self.model}
                )
        except Exception as e:
            logger.error(
                f"Video collect processing error: {e}",
                extra={"model": self.model, "error_type": type(e).__name__},
            )
        finally:
            await self.close()

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": self.created,
            "model": self.model,
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
