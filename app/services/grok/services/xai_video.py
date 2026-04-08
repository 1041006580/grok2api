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
from app.services.grok.services.xai_key_manager import XAIKeyInfo, XAIKeyManager, load_runtime_manager


DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"


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

    def _headers(self) -> Dict[str, str]:
        if not self._key_record:
            self._key_record = self._key_manager.acquire_key()
        if not self._key_record:
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )
        return {
            "Authorization": f"Bearer {self._key_record.key}",
            "Content-Type": "application/json",
        }

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"headers": self._headers()}
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

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await self._request_json(
                session,
                "POST",
                f"{self.base_url}/videos/generations",
                payload,
            )

    async def get_generation(self, request_id: str) -> Dict[str, Any]:
        request_id = str(request_id or "").strip()
        if not request_id:
            raise ValidationException(
                message="request_id is required",
                param="request_id",
                code="invalid_request_error",
            )

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            return await self._request_json(
                session,
                "GET",
                f"{self.base_url}/videos/{request_id}",
            )


__all__ = ["XAIVideoService", "DEFAULT_XAI_BASE_URL"]
