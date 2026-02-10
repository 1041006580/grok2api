"""
Grok 视频生成服务
"""

import asyncio
import re
import uuid
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import urlparse

import orjson
from curl_cffi.requests import AsyncSession

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import (
    UpstreamException,
    AppException,
    ValidationException,
    ErrorType,
)
from app.services.grok.statsig import StatsigService
from app.services.grok.model import ModelService
from app.services.token import get_token_manager, EffortType
from app.services.token.manager import mask_token
from app.services.grok.processor import VideoStreamProcessor, VideoCollectProcessor
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

# API 端点
CREATE_POST_API = "https://grok.com/rest/media/post/create"
CHAT_API = "https://grok.com/rest/app-chat/conversations/new"

# 常量
BROWSER = "chrome136"
TIMEOUT = 300
DEFAULT_MAX_CONCURRENT = 50
_MEDIA_SEMAPHORE = asyncio.Semaphore(DEFAULT_MAX_CONCURRENT)
_MEDIA_SEM_VALUE = DEFAULT_MAX_CONCURRENT
HTTP_URL_PATTERN = re.compile(r"https?://[^\s<>'\")]+", re.IGNORECASE)


def _get_api_url(default_url: str) -> str:
    """获取 API URL，支持反向代理"""
    api_base = get_config("grok.api_base", "")
    if api_base:
        return default_url.replace("https://grok.com", api_base.rstrip("/"))
    return default_url


def _get_media_semaphore() -> asyncio.Semaphore:
    global _MEDIA_SEMAPHORE, _MEDIA_SEM_VALUE
    value = get_config("performance.media_max_concurrent", DEFAULT_MAX_CONCURRENT)
    try:
        value = int(value)
    except Exception:
        value = DEFAULT_MAX_CONCURRENT
    value = max(1, value)
    if value != _MEDIA_SEM_VALUE:
        _MEDIA_SEM_VALUE = value
        _MEDIA_SEMAPHORE = asyncio.Semaphore(value)
    return _MEDIA_SEMAPHORE


class VideoService:
    """视频生成服务"""

    def __init__(self, proxy: str = None):
        self.proxy = proxy or get_config("grok.base_proxy_url", "")
        self.timeout = get_config("grok.timeout", TIMEOUT)

    def _build_headers(
        self, token: str, referer: str = "https://grok.com/imagine"
    ) -> dict:
        """构建请求头"""
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Baggage": "sentry-environment=production,sentry-release=d6add6fb0460641fd482d767a335ef72b9b6abb8,sentry-public_key=b311e0f2690c81f25e2c4cf6d4f7ce1c",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "Origin": "https://grok.com",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Referer": referer,
            "Sec-Ch-Ua": '"Google Chrome";v="136", "Chromium";v="136", "Not(A:Brand";v="24"',
            "Sec-Ch-Ua-Arch": "arm",
            "Sec-Ch-Ua-Bitness": "64",
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Model": "",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        }

        # Statsig ID
        headers["x-statsig-id"] = StatsigService.gen_id()
        headers["x-xai-request-id"] = str(uuid.uuid4())

        # Cookie
        token = token[4:] if token.startswith("sso=") else token
        cf = get_config("grok.cf_clearance", "")
        cookie_parts = [f"sso={token}", f"sso-rw={token}"]
        if cf:
            cookie_parts.append(f"cf_clearance={cf}")
        headers["Cookie"] = "; ".join(cookie_parts)

        return headers

    def _build_proxies(self) -> Optional[dict]:
        """构建代理"""
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    async def create_post(
        self,
        token: str,
        prompt: str,
        media_type: str = "MEDIA_POST_TYPE_VIDEO",
        media_url: str = None,
    ) -> str:
        """
        创建媒体帖子

        Args:
            token: 认证 Token
            prompt: 提示词（视频生成用）
            media_type: 媒体类型 (MEDIA_POST_TYPE_VIDEO 或 MEDIA_POST_TYPE_IMAGE)
            media_url: 媒体 URL（图片模式用）

        Returns:
            post ID
        """
        try:
            headers = self._build_headers(token)

            # 根据类型构建不同的载荷
            if media_type == "MEDIA_POST_TYPE_IMAGE" and media_url:
                payload = {"mediaType": media_type, "mediaUrl": media_url}
            else:
                payload = {"mediaType": media_type, "prompt": prompt}

            async with AsyncSession() as session:
                response = await session.post(
                    _get_api_url(CREATE_POST_API),
                    headers=headers,
                    json=payload,
                    impersonate=BROWSER,
                    timeout=30,
                    proxies=self._build_proxies(),
                )

            if response.status_code != 200:
                response_text = (response.text or "")[:400]
                logger.error(
                    f"Create post failed: {response.status_code}, response={response_text}"
                )
                raise UpstreamException(
                    f"Failed to create post: {response.status_code}",
                    details={
                        "status": response.status_code,
                        "response": response_text,
                    },
                )

            data = response.json()
            post_id = data.get("post", {}).get("id", "")

            if not post_id:
                raise UpstreamException("No post ID in response")

            logger.info(f"Media post created: {post_id} (type={media_type})")
            return post_id

        except Exception as e:
            logger.error(f"Create post error: {e}")
            if isinstance(e, AppException):
                raise e
            raise UpstreamException(f"Create post error: {str(e)}")

    async def create_image_post(self, token: str, image_url: str) -> str:
        """
        创建图片帖子

        Args:
            token: 认证 Token
            image_url: 完整的图片 URL (https://assets.grok.com/...)

        Returns:
            post ID
        """
        return await self.create_post(
            token, prompt="", media_type="MEDIA_POST_TYPE_IMAGE", media_url=image_url
        )

    @staticmethod
    def _extract_first_text_url(messages: list) -> Optional[str]:
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
        source_input: str,
        asset_url: str,
        asset_id: str,
        kind_hint: str,
    ):
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

        suffix = path[idx + len(marker) :].lstrip("/")
        if not suffix:
            return None

        if suffix.lower().startswith("imagine-public/"):
            return f"https://imagine-public.x.ai/{suffix}"

        return f"https://assets.grok.com/{suffix}"

    @staticmethod
    def _build_uploadable_url_for_local_path(image_ref: str) -> Optional[str]:
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
    async def _resolve_video_image_source(messages: list, attachments: list, token: str) -> Dict[str, Any]:
        ledger = get_image_origin_ledger()

        if attachments:
            attach_type, attach_data = attachments[0]
            if attach_type == "image":
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
                            source_type=ORIGIN_GENERATED,
                            canonical_url=generated_url,
                            original_url=attach_data,
                            metadata={"via": "openai_image_url"},
                        )
                        return {
                            "image_url": generated_url,
                            "source_type": ORIGIN_GENERATED,
                            "file_attachments": [],
                        }

                    uploadable = VideoService._build_uploadable_url_for_local_path(attach_data)
                    if uploadable:
                        from app.services.grok.assets import UploadService

                        upload_service = UploadService()
                        try:
                            asset_id, file_uri = await upload_service.upload(uploadable, token)
                            image_url = f"https://assets.grok.com/{file_uri}"
                            await VideoService._record_uploaded_origin(
                                source_input=uploadable,
                                asset_url=image_url,
                                asset_id=asset_id,
                                kind_hint=REFERENCE_UNKNOWN_URL,
                            )
                            return {
                                "image_url": image_url,
                                "source_type": ORIGIN_UPLOADED,
                                "file_attachments": [asset_id] if asset_id else [],
                            }
                        finally:
                            await upload_service.close()

                    await ledger.upsert_origin(
                        source_type=ORIGIN_GENERATED,
                        canonical_url=normalized,
                        original_url=attach_data,
                        metadata={"via": "openai_image_url"},
                    )
                    return {
                        "image_url": normalized,
                        "source_type": ORIGIN_GENERATED,
                        "file_attachments": [],
                    }

                if kind == REFERENCE_UPLOADED_URL:
                    await ledger.upsert_origin(
                        source_type=ORIGIN_UPLOADED,
                        canonical_url=normalized,
                        original_url=attach_data,
                        asset_id=asset_id or "",
                        metadata={"via": "openai_image_url"},
                    )
                    return {
                        "image_url": normalized,
                        "source_type": ORIGIN_UPLOADED,
                        "file_attachments": [asset_id] if asset_id else [],
                    }

                if kind == REFERENCE_BASE64:
                    image_hash = sha256_of_image_base64(attach_data)
                    if image_hash:
                        matched = await ledger.find_by_hash(image_hash)
                        if matched and matched.get("source_type") == ORIGIN_GENERATED:
                            generated_url = matched.get("canonical_url") or matched.get("original_url")
                            if generated_url:
                                return {
                                    "image_url": generated_url,
                                    "source_type": ORIGIN_GENERATED,
                                    "file_attachments": [],
                                }

                    from app.services.grok.assets import UploadService

                    upload_service = UploadService()
                    try:
                        asset_id, file_uri = await upload_service.upload(attach_data, token)
                        image_url = f"https://assets.grok.com/{file_uri}"
                        await VideoService._record_uploaded_origin(
                            source_input=attach_data,
                            asset_url=image_url,
                            asset_id=asset_id,
                            kind_hint=REFERENCE_BASE64,
                        )
                        return {
                            "image_url": image_url,
                            "source_type": ORIGIN_UPLOADED,
                            "file_attachments": [asset_id] if asset_id else [],
                        }
                    finally:
                        await upload_service.close()

                if is_http_url(attach_data):
                    from app.services.grok.assets import UploadService

                    upload_service = UploadService()
                    try:
                        asset_id, file_uri = await upload_service.upload(attach_data, token)
                        image_url = f"https://assets.grok.com/{file_uri}"
                        await VideoService._record_uploaded_origin(
                            source_input=attach_data,
                            asset_url=image_url,
                            asset_id=asset_id,
                            kind_hint=REFERENCE_UNKNOWN_URL,
                        )
                        return {
                            "image_url": image_url,
                            "source_type": ORIGIN_UPLOADED,
                            "file_attachments": [asset_id] if asset_id else [],
                        }
                    finally:
                        await upload_service.close()

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
                        source_type=ORIGIN_GENERATED,
                        canonical_url=generated_url,
                        original_url=text_link,
                        metadata={"via": "user_text_url"},
                    )
                    return {
                        "image_url": generated_url,
                        "source_type": ORIGIN_GENERATED,
                        "file_attachments": [],
                    }

                await ledger.upsert_origin(
                    source_type=ORIGIN_GENERATED,
                    canonical_url=normalized_url,
                    original_url=text_link,
                    metadata={"via": "user_text_url"},
                )
                return {
                    "image_url": normalized_url,
                    "source_type": ORIGIN_GENERATED,
                    "file_attachments": [],
                }

            if kind == REFERENCE_UPLOADED_URL:
                await ledger.upsert_origin(
                    source_type=ORIGIN_UPLOADED,
                    canonical_url=normalized_url,
                    original_url=text_link,
                    asset_id=asset_id or "",
                    metadata={"via": "user_text_url"},
                )
                return {
                    "image_url": normalized_url,
                    "source_type": ORIGIN_UPLOADED,
                    "file_attachments": [asset_id] if asset_id else [],
                }

        return {
            "image_url": None,
            "source_type": ORIGIN_UNKNOWN,
            "file_attachments": [],
        }

    def _build_payload(
        self,
        prompt: str,
        post_id: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "480p",
        preset: str = "normal",
        image_url: str = None,
        file_attachments: Optional[list[str]] = None,
    ) -> dict:
        """构建视频生成载荷"""
        preset = str(preset or "custom").strip().lower()
        mode_flag = "--mode=custom"
        if preset == "fun":
            mode_flag = "--mode=extremely-crazy"
        elif preset == "normal":
            mode_flag = "--mode=normal"
        elif preset == "spicy":
            mode_flag = "--mode=extremely-spicy-or-crazy"

        prompt = (prompt or "").strip()
        if image_url:
            image_url = image_url.strip()
            # Browser parity: include prompt text for image+prompt mode.
            if prompt:
                full_prompt = f"{image_url}  {prompt} {mode_flag}"
            else:
                full_prompt = f"{image_url}  {mode_flag}"
        else:
            full_prompt = f"{prompt} {mode_flag}" if prompt else mode_flag

        payload = {
            "temporary": True,
            "modelName": "grok-3",
            "message": full_prompt,
            "toolOverrides": {"videoGen": True},
            "enableSideBySide": True,
            "responseMetadata": {
                "experiments": [],
                "modelConfigOverride": {
                    "modelMap": {
                        "videoGenModelConfig": {
                            "parentPostId": post_id,
                            "aspectRatio": aspect_ratio,
                            "videoLength": video_length,
                            "isVideoEdit": False,
                            "resolutionName": resolution,
                        }
                    }
                },
            },
        }

        if file_attachments:
            payload["fileAttachments"] = file_attachments

        logger.info(
            f"Video payload: video_length={video_length}, resolution={resolution}, "
            f"aspect_ratio={aspect_ratio}, preset={preset}"
        )

        return payload

    async def generate(
        self,
        token: str,
        prompt: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "SD",
        stream: bool = True,
        preset: str = "normal",
    ) -> AsyncGenerator[bytes, None]:
        """
        生成视频

        Args:
            token: 认证 Token
            prompt: 视频描述
            aspect_ratio: 宽高比
            video_length: 视频时长
            resolution: 分辨率
            stream: 是否流式
            preset: 预设

        Returns:
            AsyncGenerator，流式传输

        Raises:
            UpstreamException: 连接失败时
        """
        async with _get_media_semaphore():
            session = None
            try:
                # Step 1: 创建帖子
                post_id = await self.create_post(token, prompt)

                # Step 2: 建立连接
                headers = self._build_headers(token)
                payload = self._build_payload(
                    prompt, post_id, aspect_ratio, video_length, resolution, preset
                )

                # 调试日志：打印完整的 videoGenModelConfig
                video_config = payload.get("responseMetadata", {}).get("modelConfigOverride", {}).get("modelMap", {}).get("videoGenModelConfig", {})
                logger.info(f"Video API request: videoGenModelConfig={video_config}")

                session = AsyncSession(impersonate=BROWSER)
                response = await session.post(
                    _get_api_url(CHAT_API),
                    headers=headers,
                    data=orjson.dumps(payload),
                    timeout=self.timeout,
                    stream=True,
                    proxies=self._build_proxies(),
                )

                if response.status_code != 200:
                    logger.error(f"Video generation failed: {response.status_code}")
                    try:
                        await session.close()
                    except:
                        pass
                    raise UpstreamException(
                        message=f"Video generation failed: {response.status_code}",
                        details={"status": response.status_code},
                    )

                # Step 3: 流式传输
                async def stream_response():
                    try:
                        async for line in response.aiter_lines():
                            yield line
                    finally:
                        if session:
                            await session.close()

                return stream_response()

            except Exception as e:
                if session:
                    try:
                        await session.close()
                    except:
                        pass
                logger.error(f"Video generation error: {e}")
                if isinstance(e, AppException):
                    raise e
                raise UpstreamException(f"Video generation error: {str(e)}")

    async def generate_from_image(
        self,
        token: str,
        prompt: str,
        image_url: str,
        aspect_ratio: str = "3:2",
        video_length: int = 6,
        resolution: str = "SD",
        stream: bool = True,
        preset: str = "normal",
        file_attachments: Optional[list[str]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        从图片生成视频

        Args:
            token: 认证 Token
            prompt: 视频描述
            image_url: 图片 URL
            aspect_ratio: 宽高比
            video_length: 视频时长
            resolution: 分辨率
            stream: 是否流式
            preset: 预设

        Returns:
            AsyncGenerator，流式传输
        """
        async with _get_media_semaphore():
            session = None
            try:
                effective_image_url = image_url
                effective_file_attachments = list(file_attachments or [])

                # Step 1: 创建帖子
                try:
                    post_id = await self.create_image_post(token, effective_image_url)
                except UpstreamException as e:
                    status = (e.details or {}).get("status") if getattr(e, "details", None) else None
                    if status != 400:
                        raise

                    from app.services.grok.assets import UploadService

                    logger.warning(
                        f"Create image post failed with 400, fallback to re-upload url: {effective_image_url}"
                    )
                    upload_service = UploadService()
                    try:
                        asset_id, file_uri = await upload_service.upload(effective_image_url, token)
                        effective_image_url = f"https://assets.grok.com/{file_uri}"
                        if asset_id and asset_id not in effective_file_attachments:
                            effective_file_attachments.append(asset_id)
                        await VideoService._record_uploaded_origin(
                            source_input=image_url,
                            asset_url=effective_image_url,
                            asset_id=asset_id,
                            kind_hint=REFERENCE_UNKNOWN_URL,
                        )
                    finally:
                        await upload_service.close()

                    post_id = await self.create_image_post(token, effective_image_url)

                # Step 2: 建立连接
                headers = self._build_headers(token)
                payload = self._build_payload(
                    prompt,
                    post_id,
                    aspect_ratio,
                    video_length,
                    resolution,
                    preset,
                    image_url=effective_image_url,
                    file_attachments=effective_file_attachments,
                )

                session = AsyncSession(impersonate=BROWSER)
                response = await session.post(
                    _get_api_url(CHAT_API),
                    headers=headers,
                    data=orjson.dumps(payload),
                    timeout=self.timeout,
                    stream=True,
                    proxies=self._build_proxies(),
                )

                if response.status_code != 200:
                    logger.error(f"Video from image failed: {response.status_code}")
                    try:
                        await session.close()
                    except:
                        pass
                    raise UpstreamException(
                        message=f"Video from image failed: {response.status_code}",
                        details={"status": response.status_code},
                    )

                # Step 3: 流式传输
                async def stream_response():
                    try:
                        async for line in response.aiter_lines():
                            yield line
                    finally:
                        if session:
                            await session.close()

                return stream_response()

            except Exception as e:
                if session:
                    try:
                        await session.close()
                    except:
                        pass
                logger.error(f"Video from image error: {e}")
                if isinstance(e, AppException):
                    raise e
                raise UpstreamException(f"Video from image error: {str(e)}")

    @staticmethod
    async def _wrap_stream(stream: AsyncGenerator, token_mgr, token: str, model: str, pool_name: str):
        """
        包装流式响应，在完成时记录使用

        Args:
            stream: 原始 AsyncGenerator
            token_mgr: TokenManager 实例
            token: Token 字符串
            model: 模型名称
            pool_name: Token 池名称
        """
        try:
            async for chunk in stream:
                yield chunk
        finally:
            try:
                model_info = ModelService.get(model)
                effort = (
                    EffortType.HIGH
                    if (model_info and model_info.cost.value == "high")
                    else EffortType.LOW
                )
                await token_mgr.consume(token, effort)
                logger.debug(
                    f"Video stream completed, recorded usage for token {mask_token(token, pool_name)} (effort={effort.value})"
                )
            except Exception as e:
                logger.warning(f"Failed to record video stream usage: {e}")

    @staticmethod
    async def completions(
        model: str,
        messages: list,
        stream: bool = None,
        thinking: str = None,
        aspect_ratio: str = "3:2",
        video_length: int = None,
        resolution: str = None,
        preset: str = "custom",
        client_type: str = "",
    ):
        """
        视频生成入口

        Args:
            model: 模型名称
            messages: 消息列表
            stream: 是否流式
            thinking: 思考模式
            aspect_ratio: 宽高比
            video_length: 视频时长
            resolution: 分辨率
            preset: 预设模式

        Returns:
            AsyncGenerator (流式) 或 dict (非流式)
        """
        # 根据模型设置默认值
        is_super = model == "grok-imagine-1.0-video-super"
        if video_length is None:
            video_length = 10 if is_super else 6
        if resolution is None:
            resolution = "720p" if is_super else "480p"
        if aspect_ratio is None:
            aspect_ratio = get_config("imagine.default_aspect_ratio", "2:3")

        # 获取 token
        try:
            token_mgr = await get_token_manager()
            await token_mgr.reload_if_stale()
            pool_name = ModelService.pool_for_model(model)
            token = token_mgr.get_token(pool_name)
        except Exception as e:
            logger.error(f"Failed to get token: {e}")
            raise AppException(
                message="Internal service error obtaining token",
                error_type=ErrorType.SERVER.value,
                code="internal_error",
            )

        if not token:
            raise AppException(
                message="No available tokens. Please try again later.",
                error_type=ErrorType.RATE_LIMIT.value,
                code="rate_limit_exceeded",
                status_code=429,
            )

        # 解析参数
        think = None
        if thinking == "enabled":
            think = True
        elif thinking == "disabled":
            think = False

        is_stream = stream if stream is not None else get_config("grok.stream", True)

        # 提取内容
        from app.services.grok.chat import MessageExtractor

        try:
            prompt, attachments = MessageExtractor.extract(messages, is_video=True)
        except ValueError as e:
            raise ValidationException(str(e))

        source_info = await VideoService._resolve_video_image_source(messages, attachments, token)
        image_url = source_info.get("image_url")
        source_type = source_info.get("source_type", ORIGIN_UNKNOWN)
        file_attachments = source_info.get("file_attachments") or []

        # 生成视频
        service = VideoService()

        # 图片转视频
        if image_url:
            configured_mode = str(
                get_config("grok.video_no_prompt_mode", "normal")
            ).strip().lower()
            if configured_mode not in {"fun", "normal", "spicy"}:
                configured_mode = "normal"
            effective_preset = preset or ("custom" if prompt else configured_mode)
            logger.info(
                f"Video image source resolved: source_type={source_type}, "
                f"has_file_attachments={bool(file_attachments)}"
            )
            response = await service.generate_from_image(
                token,
                prompt,
                image_url,
                aspect_ratio,
                video_length,
                resolution,
                stream,
                effective_preset,
                file_attachments=file_attachments,
            )
        else:
            effective_preset = preset or "custom"
            response = await service.generate(
                token,
                prompt,
                aspect_ratio,
                video_length,
                resolution,
                stream,
                effective_preset,
            )

        # 处理响应
        if is_stream:
            processor = VideoStreamProcessor(model, token, think, client_type)
            return VideoService._wrap_stream(
                processor.process(response), token_mgr, token, model, pool_name
            )
        else:
            result = await VideoCollectProcessor(model, token, client_type).process(response)
            # 非流式：处理完成后立即记录使用
            try:
                model_info = ModelService.get(model)
                effort = (
                    EffortType.HIGH
                    if (model_info and model_info.cost.value == "high")
                    else EffortType.LOW
                )
                await token_mgr.consume(token, effort)
                logger.debug(
                    f"Video completed, recorded usage for token {mask_token(token, pool_name)} (effort={effort.value})"
                )
            except Exception as e:
                logger.warning(f"Failed to record video usage: {e}")
            return result


__all__ = ["VideoService"]
