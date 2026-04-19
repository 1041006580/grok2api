"""
xAI API-key based image generation service.
"""

from typing import Any, Dict, Optional

import aiohttp
import orjson

from app.core.config import get_config
from app.core.exceptions import UpstreamException, ValidationException
from app.services.grok.services.xai_key_manager import (
    XAIKeyInfo,
    XAIKeyManager,
    disable_runtime_key,
    load_runtime_manager,
)


DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
RETRYABLE_XAI_STATUS_CODES = {429, 500, 502, 503, 504}


class XAIImageService:
    """Direct x.ai image generation via API key pool."""

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
        self.timeout = float(get_config("xai.timeout", get_config("image.timeout", 60)))

    @staticmethod
    def _extract_error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
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
        kwargs: Dict[str, Any] = {"headers": self._headers_for(key_record)}
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
                    f"xAI image API request failed with status {response.status}"
                )
                raise UpstreamException(
                    message=message,
                    details={"status": response.status, "body": text[:1000]},
                )

            if not isinstance(data, dict):
                raise UpstreamException(
                    message="xAI image API returned an invalid JSON payload",
                    details={"status": response.status, "body": text[:1000]},
                )
            return data

    async def generate(
        self,
        *,
        prompt: str,
        model: str,
        n: int = 1,
        response_format: str = "b64_json",
        aspect_ratio: Optional[str] = None,
    ) -> list[str]:
        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": max(1, int(n or 1)),
            "response_format": response_format,
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio

        candidate_keys = self._create_candidate_keys()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_error: Optional[Exception] = None
            for index, candidate in enumerate(candidate_keys):
                try:
                    result = await self._request_json(
                        session,
                        "POST",
                        f"{self.base_url}/images/generations",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    return self._extract_images(result, response_format=response_format)
                except Exception as exc:
                    if self._status_from_exception(exc) == 429 and candidate:
                        await disable_runtime_key(candidate.id, last_error=str(exc))
                    if not self._is_retryable_create_error(exc) or index >= len(candidate_keys) - 1:
                        raise
                    last_error = exc

            if last_error:
                raise last_error
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )

    @staticmethod
    def _extract_images(result: Dict[str, Any], *, response_format: str) -> list[str]:
        data = result.get("data")
        if not isinstance(data, list):
            raise UpstreamException(
                message="xAI image API returned an invalid data payload",
                details={"status": 502, "body": str(result)[:1000]},
            )

        key = "url" if response_format == "url" else "b64_json"
        images: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                images.append(value.strip())

        if images:
            return images

        raise UpstreamException(
            message="xAI image API returned empty image data",
            details={"status": 502, "body": str(result)[:1000]},
        )


__all__ = ["XAIImageService", "DEFAULT_XAI_BASE_URL"]
