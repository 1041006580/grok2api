"""
Reverse interface: rate limits.

Uses aiohttp instead of curl_cffi to avoid persistent HTTP/2 KeyError bug
in curl_cffi on certain platforms. The rate-limits API is a simple JSON POST
that does not require browser fingerprinting.
"""

import aiohttp
import orjson
from typing import Any

from aiohttp_socks import ProxyConnector

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import UpstreamException
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.retry import retry_on_status
from app.services.reverse.utils.urls import resolve_api_url

RATE_LIMITS_API = "https://grok.com/rest/rate-limits"


class _SimpleResponse:
    """Lightweight response wrapper compatible with curl_cffi Response interface."""

    def __init__(self, status_code: int, body: bytes, headers: dict):
        self.status_code = status_code
        self._body = body
        self.headers = headers

    def json(self):
        return orjson.loads(self._body)

    @property
    def text(self):
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self):
        return self._body


class RateLimitsReverse:
    """/rest/rate-limits reverse interface."""

    @staticmethod
    async def request(
        session, token: str, model_name: str = "grok-3"
    ) -> Any:
        """Fetch rate limits from Grok.

        Args:
            session: Unused (kept for interface compatibility).
            token: str, the SSO token.
            model_name: str, the model name for rate-limits query.
                Valid values: "grok-3", "grok-4", "grok-420", etc.

        Returns:
            Any: The response from the request.
        """
        try:
            # Get proxy
            base_proxy = get_config("proxy.base_proxy_url")

            # Build headers
            headers = build_headers(
                cookie_token=token,
                content_type="application/json",
                origin="https://grok.com",
                referer="https://grok.com/",
            )

            # Build payload
            payload = {
                "requestKind": "DEFAULT",
                "modelName": model_name,
            }

            # Config
            timeout_val = float(get_config("usage.timeout") or 30)

            async def _do_request():
                connector = None
                try:
                    if base_proxy:
                        connector = ProxyConnector.from_url(base_proxy)

                    timeout = aiohttp.ClientTimeout(total=timeout_val)
                    async with aiohttp.ClientSession(
                        connector=connector, timeout=timeout
                    ) as aio_session:
                        async with aio_session.post(
                            resolve_api_url(RATE_LIMITS_API),
                            headers=headers,
                            data=orjson.dumps(payload),
                        ) as resp:
                            body = await resp.read()

                            if resp.status != 200:
                                body_text = body[:500].decode(
                                    "utf-8", errors="replace"
                                )
                                logger.error(
                                    f"RateLimitsReverse: Request failed, {resp.status}, body={body_text}",
                                    extra={"error_type": "UpstreamException"},
                                )
                                raise UpstreamException(
                                    message=f"RateLimitsReverse: Request failed, {resp.status}",
                                    details={
                                        "status": resp.status,
                                        "body": body_text,
                                    },
                                )

                            return _SimpleResponse(
                                resp.status, body, dict(resp.headers)
                            )
                finally:
                    if connector:
                        await connector.close()

            return await retry_on_status(_do_request)

        except Exception as e:
            if isinstance(e, UpstreamException):
                raise

            # Handle other non-upstream exceptions
            logger.error(
                f"RateLimitsReverse: Request failed ({type(e).__name__}): {str(e)}",
                extra={"error_type": type(e).__name__},
                exc_info=True,
            )
            raise UpstreamException(
                message=f"RateLimitsReverse: Request failed, {str(e)}",
                details={"status": 502, "error": str(e)},
            )


__all__ = ["RateLimitsReverse"]
