"""
Reverse interface: rate limits.
"""

import orjson
from typing import Any
from curl_cffi.requests import AsyncSession

from app.core.logger import logger
from app.core.config import get_config
from app.core.proxy_pool import (
    build_http_proxies,
    get_current_proxy_from,
    rotate_proxy,
    should_rotate_proxy,
)
from app.core.exceptions import UpstreamException
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.retry import retry_on_status
from app.services.reverse.utils.urls import resolve_api_url

RATE_LIMITS_API = "https://grok.com/rest/rate-limits"


class RateLimitsReverse:
    """/rest/rate-limits reverse interface."""

    @staticmethod
    async def request(
        session: AsyncSession, token: str, model_name: str = "grok-4-1-thinking-1129"
    ) -> Any:
        """Fetch rate limits from Grok.

        Args:
            session: AsyncSession, the session to use for the request.
            token: str, the SSO token.
            model_name: str, the model name for rate-limits query.
                Valid values: "grok-3", "grok-4", "grok-420", etc.

        Returns:
            Any: The response from the request.
        """
        try:
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

            # Curl Config
            timeout = get_config("usage.timeout")
            browser = get_config("proxy.browser")
            active_proxy_key = None

            async def _do_request():
                nonlocal active_proxy_key
                active_proxy_key, proxy_url = get_current_proxy_from("proxy.base_proxy_url")
                proxies = build_http_proxies(proxy_url)
                url = resolve_api_url(RATE_LIMITS_API)
                logger.debug("[Reverse-RateLimits] >>> POST {} model={}", url, model_name)
                try:
                    response = await session.post(
                        url,
                        headers=headers,
                        data=orjson.dumps(payload),
                        timeout=timeout,
                        proxies=proxies,
                        impersonate=browser,
                    )
                    logger.debug("[Reverse-RateLimits] <<< POST {} status={}", url, response.status_code)

                    if response.status_code != 200:
                        body = ""
                        try:
                            body = response.text[:500]
                        except Exception:
                            pass
                        safe_body = body.replace("{", "{{").replace("}", "}}")
                        logger.error(
                            f"RateLimitsReverse: Request failed, {response.status_code}, body={safe_body}",
                            extra={"error_type": "UpstreamException"},
                        )
                        raise UpstreamException(
                            message=f"RateLimitsReverse: Request failed, {response.status_code}",
                            details={"status": response.status_code, "body": body},
                        )

                    return response
                except KeyError as conn_err:
                    logger.warning(
                        f"RateLimitsReverse: curl_cffi KeyError: {conn_err}, treating as 429 for retry"
                    )
                    raise UpstreamException(
                        message=f"RateLimitsReverse: curl_cffi connection error: {conn_err}",
                            details={"status": 429, "error": str(conn_err)},
                        )

            async def _on_retry(attempt: int, status_code: int, error: Exception, delay: float):
                if active_proxy_key and should_rotate_proxy(status_code):
                    rotate_proxy(active_proxy_key)

            return await retry_on_status(_do_request, on_retry=_on_retry)

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
