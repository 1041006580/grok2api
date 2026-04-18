"""
xAI API-key based chat completions service.
"""

from typing import Any, AsyncGenerator, Dict, List, Optional

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


class XAIChatService:
    """Direct x.ai chat completions via API key pool."""

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
                    f"xAI chat API request failed with status {response.status}"
                )
                raise UpstreamException(
                    message=message,
                    details={"status": response.status, "body": text[:1000]},
                )

            if not isinstance(data, dict):
                raise UpstreamException(
                    message="xAI chat API returned an invalid JSON payload",
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
                f"xAI chat API request failed with status {response.status}"
            )
            raise UpstreamException(
                message=message,
                details={"status": response.status, "body": text[:1000]},
            )
        return response

    async def create_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        deferred: bool = False,
        reasoning_effort: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
        parallel_tool_calls: Optional[bool] = None,
    ):
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        if deferred:
            payload["deferred"] = True
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            payload["parallel_tool_calls"] = parallel_tool_calls

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        candidate_keys = self._create_candidate_keys()
        if not candidate_keys:
            raise ValidationException(
                message="xAI key pool is not configured with any enabled key",
                param="model",
                code="xai_api_key_missing",
            )

        if stream:
            from app.core.logger import logger
            logger.debug(f"xAI chat stream request: model={model}, candidates={len(candidate_keys)}")
            session = aiohttp.ClientSession(timeout=timeout)
            last_error: Optional[Exception] = None
            for index, candidate in enumerate(candidate_keys):
                try:
                    logger.debug(f"xAI trying key #{index+1}/{len(candidate_keys)}")
                    response = await self._open_stream(
                        session,
                        f"{self.base_url}/chat/completions",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    logger.debug(f"xAI stream opened successfully, status={response.status}")

                    async def _stream() -> AsyncGenerator[str, None]:
                        from app.core.logger import logger
                        try:
                            chunk_count = 0
                            async for chunk in response.content:
                                chunk_count += 1
                                decoded = chunk.decode(errors="ignore") if isinstance(chunk, bytes) else str(chunk)
                                if chunk_count <= 3:
                                    logger.debug(f"xAI stream chunk #{chunk_count}: {decoded[:200]}")
                                yield decoded
                            logger.debug(f"xAI stream finished, total chunks: {chunk_count}")
                        finally:
                            response.close()
                            await session.close()

                    return _stream()
                except Exception as exc:
                    if self._status_from_exception(exc) == 429 and candidate:
                        await disable_runtime_key(candidate.id, last_error=str(exc))
                    logger.error(f"xAI key #{index+1} failed: {exc}")
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
                        f"{self.base_url}/chat/completions",
                        payload,
                        key_record=candidate,
                    )
                    self._key_record = candidate
                    return result
                except Exception as exc:
                    if self._status_from_exception(exc) == 429 and candidate:
                        await disable_runtime_key(candidate.id, last_error=str(exc))
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

    async def get_deferred_completion(self, request_id: str):
        request_id = str(request_id or "").strip()
        if not request_id:
            raise ValidationException(
                message="request_id is required",
                param="request_id",
                code="invalid_request_error",
            )
        key_record = self._key_record or self._key_manager.acquire_key()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            response = await session.get(
                f"{self.base_url}/chat/deferred-completion/{request_id}",
                headers=self._headers_for(key_record),
            )
            if response.status == 202:
                response.close()
                return 202, None
            text = await response.text()
            try:
                data = orjson.loads(text) if text else {}
            except orjson.JSONDecodeError:
                data = {"raw": text}

            if response.status >= 400:
                message = self._extract_error_message(data) or (
                    f"xAI deferred chat request failed with status {response.status}"
                )
                if response.status == 429 and key_record:
                    await disable_runtime_key(key_record.id, last_error=message)
                raise UpstreamException(
                    message=message,
                    details={"status": response.status, "body": text[:1000]},
                )
            if not isinstance(data, dict):
                raise UpstreamException(
                    message="xAI deferred chat API returned an invalid JSON payload",
                    details={"status": response.status, "body": text[:1000]},
                )
            return response.status, data


__all__ = ["XAIChatService", "DEFAULT_XAI_BASE_URL"]
