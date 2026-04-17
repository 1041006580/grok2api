"""
xAI API-key based Responses API service.
"""

from typing import Any, AsyncGenerator, Dict, Optional

import aiohttp
import orjson

from app.core.config import get_config
from app.core.exceptions import UpstreamException, ValidationException
from app.core.logger import logger
from app.services.grok.services.xai_key_manager import (
    XAIKeyInfo,
    XAIKeyManager,
    load_runtime_manager,
)


DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
RETRYABLE_XAI_STATUS_CODES = {429, 500, 502, 503, 504}


class XAIResponsesService:
    """Direct x.ai Responses API via key pool."""

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
        self.timeout = float(get_config("xai.timeout", 60))

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
        return cls._status_from_exception(exc) in RETRYABLE_XAI_STATUS_CODES

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
        payload: Optional[Dict[str, Any]],
    ) -> None:
        details = getattr(exc, "details", None)
        status = details.get("status") if isinstance(details, dict) else None
        body = details.get("body") if isinstance(details, dict) else ""
        model = (payload or {}).get("model")
        include = (payload or {}).get("include")
        stream = (payload or {}).get("stream")
        key_id = getattr(key_record, "id", None)
        logger.error(
            "xAI Responses key #{}/{} failed: status={}, key_id={}, model={}, stream={}, include={}, error={}, body={}",
            candidate_index,
            candidate_total,
            status,
            key_id,
            model,
            stream,
            include,
            str(exc),
            self._truncate_debug_text(body),
        )

    @staticmethod
    def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(payload or {})
        model = str(normalized.get("model") or "").strip()
        include = normalized.get("include")
        include_list = list(include) if isinstance(include, list) else []
        if model == "grok-4.20-multi-agent" and "verbose_streaming" not in include_list:
            include_list.append("verbose_streaming")
        if include_list:
            normalized["include"] = include_list
        return normalized

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
        *,
        key_record: Optional[XAIKeyInfo],
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
                    f"xAI responses API request failed with status {response.status}"
                )
                raise UpstreamException(
                    message=message,
                    details={"status": response.status, "body": text[:1000]},
                )

            if not isinstance(data, dict):
                raise UpstreamException(
                    message="xAI responses API returned an invalid JSON payload",
                    details={"status": response.status, "body": text[:1000]},
                )
            return data

    async def _open_stream(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: Dict[str, Any],
        *,
        key_record: XAIKeyInfo,
    ) -> aiohttp.ClientResponse:
        response = await session.post(
            url,
            data=orjson.dumps(payload),
            headers=self._headers_for(key_record),
        )
        if response.status >= 400:
            text = await response.text()
            try:
                data = orjson.loads(text) if text else {}
            except orjson.JSONDecodeError:
                data = {"raw": text}
            response.close()
            message = self._extract_error_message(data) or (
                f"xAI responses API request failed with status {response.status}"
            )
            raise UpstreamException(
                message=message,
                details={"status": response.status, "body": text[:1000]},
            )
        return response

    async def create_response(self, payload: Dict[str, Any]):
        payload = self._normalize_payload(payload)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        candidate_keys = self._create_candidate_keys()
        if not candidate_keys:
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )

        if bool(payload.get("stream")):
            logger.debug(f"xAI Responses API stream request: model={payload.get('model')}, candidates={len(candidate_keys)}")
            session = aiohttp.ClientSession(timeout=timeout)
            last_error: Optional[Exception] = None
            for index, candidate in enumerate(candidate_keys):
                try:
                    logger.debug(f"xAI Responses trying key #{index+1}/{len(candidate_keys)}")
                    response = await self._open_stream(
                        session,
                        f"{self.base_url}/responses",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    logger.debug(f"xAI Responses stream opened successfully, status={response.status}")

                    async def _stream() -> AsyncGenerator[str, None]:
                        try:
                            chunk_count = 0
                            async for chunk in response.content:
                                chunk_count += 1
                                decoded = chunk.decode(errors="ignore") if isinstance(chunk, bytes) else str(chunk)
                                if chunk_count <= 3:
                                    logger.debug(f"xAI Responses chunk #{chunk_count}: {decoded[:200]}")
                                yield decoded
                            logger.debug(f"xAI Responses stream finished, total chunks: {chunk_count}")
                        finally:
                            response.close()
                            await session.close()

                    return _stream()
                except Exception as exc:
                    self._log_upstream_failure(
                        candidate_index=index + 1,
                        candidate_total=len(candidate_keys),
                        key_record=candidate,
                        exc=exc,
                        payload=payload,
                    )
                    if not self._is_retryable_create_error(exc) or index >= len(candidate_keys) - 1:
                        await session.close()
                        raise
                    last_error = exc
                    continue

            await session.close()
            if last_error:
                raise last_error
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )

        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_error: Optional[Exception] = None
            for index, candidate in enumerate(candidate_keys):
                try:
                    result = await self._request_json(
                        session,
                        "POST",
                        f"{self.base_url}/responses",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    return result
                except Exception as exc:
                    self._log_upstream_failure(
                        candidate_index=index + 1,
                        candidate_total=len(candidate_keys),
                        key_record=candidate,
                        exc=exc,
                        payload=payload,
                    )
                    if not self._is_retryable_create_error(exc) or index >= len(candidate_keys) - 1:
                        raise
                    last_error = exc
                    continue

            if last_error:
                raise last_error
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )


__all__ = ["XAIResponsesService", "DEFAULT_XAI_BASE_URL"]
