"""
Reverse interface: media post create.
"""

import orjson
from typing import Any
from curl_cffi.requests import AsyncSession

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import UpstreamException
from app.services.token.service import TokenService
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.retry import retry_on_status
from app.services.reverse.utils.urls import resolve_api_url

MEDIA_POST_API = "https://grok.com/rest/media/post/create"


class MediaPostReverse:
    """/rest/media/post/create reverse interface."""

    @staticmethod
    async def request(
        session: AsyncSession,
        token: str,
        mediaType: str,
        mediaUrl: str,
        prompt: str = "",
    ) -> Any:
        """Create media post in Grok.

        Args:
            session: AsyncSession, the session to use for the request.
            token: str, the SSO token.
            mediaType: str, the media type.
            mediaUrl: str, the media URL.

        Returns:
            Any: The response from the request.
        """
        try:
            # Get proxies
            base_proxy = get_config("proxy.base_proxy_url")
            proxies = {"http": base_proxy, "https": base_proxy} if base_proxy else None

            # Build headers
            headers = build_headers(
                cookie_token=token,
                content_type="application/json",
                origin="https://grok.com",
                referer="https://grok.com",
            )

            # Build payload
            payload = {"mediaType": mediaType}
            if mediaUrl:
                payload["mediaUrl"] = mediaUrl
            if prompt:
                payload["prompt"] = prompt

            # Curl Config
            timeout = get_config("video.timeout")
            browser = get_config("proxy.browser")

            async def _do_request():
                try:
                    response = await session.post(
                        resolve_api_url(MEDIA_POST_API),
                        headers=headers,
                        data=orjson.dumps(payload),
                        timeout=timeout,
                        proxies=proxies,
                        impersonate=browser,
                    )

                    # Access status_code safely - curl_cffi may raise KeyError here
                    try:
                        status = response.status_code
                    except Exception as sc_err:
                        logger.error(
                            f"MediaPostReverse: response.status_code raised {type(sc_err).__name__}: {sc_err}",
                            exc_info=True,
                        )
                        # Try to dump raw response info for diagnosis
                        diag = {}
                        for attr in ("url", "http_version", "content", "headers"):
                            try:
                                val = getattr(response, attr, None)
                                if attr == "content" and val is not None:
                                    diag[attr] = repr(val[:500])
                                elif val is not None:
                                    diag[attr] = str(val)[:200]
                            except Exception:
                                diag[attr] = "<error>"
                        logger.error(f"MediaPostReverse: response diagnosis: {diag}")
                        raise UpstreamException(
                            message=f"MediaPostReverse: Failed to read response status: {sc_err}",
                            details={"status": 502, "error": str(sc_err), "diag": diag},
                        )

                    if status != 200:
                        content = ""
                        try:
                            content = response.text[:500]
                        except Exception:
                            try:
                                content = (await response.atext())[:500]
                            except Exception:
                                pass
                        logger.error(
                            f"MediaPostReverse: Media post create failed, {status}, body={content}",
                            extra={"error_type": "UpstreamException"},
                        )
                        raise UpstreamException(
                            message=f"MediaPostReverse: Media post create failed, {status}",
                            details={"status": status, "body": content},
                        )

                    # Validate response body
                    try:
                        body = response.json()
                    except Exception as json_err:
                        raw = ""
                        try:
                            raw = response.text[:500]
                        except Exception:
                            pass
                        logger.error(
                            f"MediaPostReverse: Failed to parse response JSON: {json_err}, raw={raw}",
                        )
                        raise UpstreamException(
                            message=f"MediaPostReverse: Invalid JSON response",
                            details={"status": 502, "body": raw},
                        )

                    # Check for upstream error in 200 response body
                    if isinstance(body, dict) and "error" in body and "post" not in body:
                        error_info = body.get("error", "")
                        logger.error(
                            f"MediaPostReverse: Upstream returned error in 200 body: {str(error_info)[:200]}",
                        )
                        raise UpstreamException(
                            message=f"MediaPostReverse: Upstream error in response body",
                            details={"status": 502, "body": str(error_info)[:500]},
                        )

                    return response

                except UpstreamException:
                    raise
                except Exception as req_err:
                    logger.error(
                        f"MediaPostReverse: _do_request raised {type(req_err).__name__}: {req_err}",
                        exc_info=True,
                    )
                    raise UpstreamException(
                        message=f"MediaPostReverse: request error, {type(req_err).__name__}: {req_err}",
                        details={"status": 502, "error": str(req_err)},
                    )

            return await retry_on_status(_do_request)

        except Exception as e:
            # Handle upstream exception
            if isinstance(e, UpstreamException):
                status = None
                if e.details and "status" in e.details:
                    status = e.details["status"]
                else:
                    status = getattr(e, "status_code", None)
                if status == 401:
                    try:
                        await TokenService.record_fail(token, status, "media_post_auth_failed")
                    except Exception:
                        pass
                raise

            # Handle other non-upstream exceptions
            logger.error(
                f"MediaPostReverse: Media post create failed ({type(e).__name__}): {str(e)}",
                extra={"error_type": type(e).__name__},
                exc_info=True,
            )
            raise UpstreamException(
                message=f"MediaPostReverse: Media post create failed, {str(e)}",
                details={"status": 502, "error": str(e)},
            )


__all__ = ["MediaPostReverse"]
