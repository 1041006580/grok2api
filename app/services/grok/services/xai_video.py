"""
xAI API-key based video generation service.
"""

import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp
import orjson

from app.core.config import get_config
from app.core.exceptions import UpstreamException, ValidationException
from app.core.logger import logger
from app.services.grok.services.xai_key_manager import XAIKeyInfo, XAIKeyManager, load_runtime_manager
from app.services.grok.services.xai_key_manager import disable_runtime_key


DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
RETRYABLE_XAI_STATUS_CODES = {429, 500, 502, 503, 504}


class XAIVideoService:
    """Direct x.ai video generation via API key."""

    def __init__(
        self,
        *,
        key_manager: Optional[XAIKeyManager] = None,
        key_record: Optional[XAIKeyInfo] = None,
    ):
        self._key_manager = key_manager or load_runtime_manager()
        self._key_record = key_record
        self.base_url = (
            str(get_config("xai.base_url", DEFAULT_XAI_BASE_URL) or DEFAULT_XAI_BASE_URL)
            .strip()
            .rstrip("/")
        )
        self.timeout = float(get_config("xai.timeout", get_config("video.timeout", 60)))
        self.poll_interval = max(
            1.0, float(get_config("xai.video_poll_interval_seconds", 5))
        )
        self.poll_timeout = max(
            self.poll_interval,
            float(get_config("xai.video_poll_timeout_seconds", 900)),
        )
        self.poll_retry_attempts = max(
            1,
            int(get_config("xai.video_poll_retry_attempts", 3) or 3),
        )
        self.poll_retry_base_delay = max(
            0.1,
            float(get_config("xai.video_poll_retry_base_delay_seconds", 0.5) or 0.5),
        )

    @staticmethod
    def _extract_error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(payload, str):
            text = payload.strip()
            if text:
                return text
        return ""

    @staticmethod
    def _status_from_exception(exc: Exception) -> Optional[int]:
        details = getattr(exc, "details", None)
        if not isinstance(details, dict):
            return None
        status = details.get("status")
        try:
            return int(status)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _is_retryable_create_error(cls, exc: Exception) -> bool:
        status = cls._status_from_exception(exc)
        return status in RETRYABLE_XAI_STATUS_CODES

    @classmethod
    def _is_retryable_poll_error(cls, exc: Exception) -> bool:
        status = cls._status_from_exception(exc)
        return status in RETRYABLE_XAI_STATUS_CODES

    def _headers_for(self, key_record: Optional[XAIKeyInfo]) -> Dict[str, str]:
        if not key_record:
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )
        return {
            "Authorization": f"Bearer {key_record.key}",
            "Content-Type": "application/json",
        }

    def _headers(self) -> Dict[str, str]:
        if not self._key_record:
            self._key_record = self._key_manager.acquire_key()
        return self._headers_for(self._key_record)

    @staticmethod
    def _truncate_debug_text(value: Any, limit: int = 500) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...(truncated)"

    def _log_upstream_failure(
        self,
        *,
        candidate_index: int,
        candidate_total: int,
        key_record: Optional[XAIKeyInfo],
        exc: Exception,
        payload: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
        phase: str,
    ) -> None:
        details = getattr(exc, "details", None)
        status = details.get("status") if isinstance(details, dict) else None
        body = details.get("body") if isinstance(details, dict) else ""
        key_id = getattr(key_record, "id", None)
        logger.error(
            "xAI video {} key #{}/{} failed: status={}, key_id={}, request_id={}, error={}, body={}, payload={}",
            phase,
            candidate_index,
            candidate_total,
            status,
            key_id,
            request_id,
            str(exc),
            self._truncate_debug_text(body),
            self._truncate_debug_text(orjson.dumps(payload).decode() if payload else ""),
        )

    def _create_candidate_keys(self) -> list[XAIKeyInfo]:
        ordered: list[XAIKeyInfo] = []
        seen: set[tuple[str, str]] = set()

        def add_key(key_record: Optional[XAIKeyInfo]) -> None:
            if not key_record:
                return
            marker = (str(getattr(key_record, "id", "") or "").strip(), key_record.key)
            if marker in seen:
                return
            seen.add(marker)
            ordered.append(key_record)

        add_key(self._key_record)

        iter_active = getattr(self._key_manager, "iter_active_keys", None)
        if callable(iter_active):
            for key_record in iter_active():
                add_key(key_record)

        if not ordered:
            add_key(self._key_manager.acquire_key())

        return ordered

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
        key_record: Optional[XAIKeyInfo] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "headers": self._headers_for(key_record) if key_record else self._headers()
        }
        if payload is not None:
            kwargs["data"] = orjson.dumps(payload)

        async with session.request(method, url, **kwargs) as response:
            text = await response.text()
            try:
                data = orjson.loads(text) if text else {}
            except orjson.JSONDecodeError:
                data = {"raw": text}

            if response.status >= 400:
                message = self._extract_error_message(data) or (
                    f"xAI video API request failed with status {response.status}"
                )
                raise UpstreamException(
                    message=message,
                    details={"status": response.status, "body": text[:1000]},
                )

            if not isinstance(data, dict):
                raise UpstreamException(
                    message="xAI video API returned an invalid JSON payload",
                    details={"status": response.status, "body": text[:1000]},
                )
            return data

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        duration: int,
        aspect_ratio: str,
        resolution: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        started = await self.start_generation(
            prompt=prompt,
            model=model,
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            image_url=image_url,
        )
        request_id = started.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            raise UpstreamException(
                message="xAI video API did not return request_id",
                details={"status": 502, "body": str(started)[:1000]},
            )

        deadline = time.monotonic() + self.poll_timeout
        while True:
            result = await self.get_generation(request_id)
            status = str(result.get("status", "")).strip().lower()
            if status in {"done", "completed", "succeeded"}:
                video = result.get("video") or {}
                url = video.get("url") or result.get("url")
                if not isinstance(url, str) or not url.strip():
                    raise UpstreamException(
                        message="xAI video API completed without video url",
                        details={"status": 502, "body": str(result)[:1000]},
                    )
                actual_duration = video.get("duration")
                try:
                    actual_duration = int(actual_duration)
                except (TypeError, ValueError):
                    actual_duration = duration
                actual_model = video.get("model") or result.get("model") or model
                return {
                    "request_id": request_id,
                    "status": status,
                    "url": url.strip(),
                    "duration": actual_duration,
                    "model": actual_model,
                }

            if status in {"failed", "error", "expired", "cancelled"}:
                message = self._extract_error_message(result) or (
                    f"xAI video request ended with status `{status}`"
                )
                raise UpstreamException(
                    message=message,
                    details={"status": 502, "body": str(result)[:1000]},
                )

            if time.monotonic() >= deadline:
                raise UpstreamException(
                    message="xAI video generation polling timed out",
                    details={"status": 504, "request_id": request_id},
                )

            await asyncio.sleep(self.poll_interval)

    async def start_generation(
        self,
        *,
        prompt: str,
        model: str,
        duration: int,
        aspect_ratio: str,
        resolution: str,
        image_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if image_url:
            payload["image"] = {"url": image_url}

        candidate_keys = self._create_candidate_keys()
        if not candidate_keys:
            self._headers()
            candidate_keys = [self._key_record] if self._key_record else []

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_error: Optional[Exception] = None
            for index, candidate in enumerate(candidate_keys):
                try:
                    result = await self._request_json(
                        session,
                        "POST",
                        f"{self.base_url}/videos/generations",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    return result
                except Exception as exc:
                    if self._status_from_exception(exc) == 429 and candidate:
                        await disable_runtime_key(candidate.id, last_error=str(exc))
                    self._log_upstream_failure(
                        candidate_index=index + 1,
                        candidate_total=len(candidate_keys),
                        key_record=candidate,
                        exc=exc,
                        payload=payload,
                        phase="create",
                    )
                    if not self._is_retryable_create_error(exc):
                        raise
                    last_error = exc
                    if index >= len(candidate_keys) - 1:
                        raise

            if last_error:
                raise last_error
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )

    async def get_generation(self, request_id: str) -> Dict[str, Any]:
        request_id = str(request_id or "").strip()
        if not request_id:
            raise ValidationException(
                message="request_id is required",
                param="request_id",
                code="invalid_request_error",
            )

        bound_key = self._key_record or self._key_manager.acquire_key()
        self._key_record = bound_key

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(1, self.poll_retry_attempts + 1):
                try:
                    return await self._request_json(
                        session,
                        "GET",
                        f"{self.base_url}/videos/{request_id}",
                        key_record=bound_key,
                    )
                except Exception as exc:
                    if self._status_from_exception(exc) == 429 and bound_key:
                        await disable_runtime_key(bound_key.id, last_error=str(exc))
                    self._log_upstream_failure(
                        candidate_index=attempt,
                        candidate_total=self.poll_retry_attempts,
                        key_record=bound_key,
                        exc=exc,
                        request_id=request_id,
                        phase="poll",
                    )
                    if (
                        not self._is_retryable_poll_error(exc)
                        or attempt >= self.poll_retry_attempts
                    ):
                        raise
                    await asyncio.sleep(
                        self.poll_retry_base_delay * (2 ** (attempt - 1))
                    )


__all__ = ["XAIVideoService", "DEFAULT_XAI_BASE_URL"]
