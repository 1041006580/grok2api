"""
Batch NSFW service.
"""

import asyncio
import re
from typing import Callable, Awaitable, Dict, Any, Optional

from app.core.logger import logger
from app.core.config import get_config
from app.core.exceptions import UpstreamException
from app.services.reverse.nsfw_mgmt import NsfwMgmtReverse
from app.services.reverse.set_birth import SetBirthReverse
from app.services.reverse.utils.headers import build_headers
from app.services.reverse.utils.session import ResettableSession
from app.services.reverse.utils.urls import resolve_api_url
from app.core.batch import run_batch


_NSFW_SEMAPHORE = None
_NSFW_SEM_VALUE = None
NSFW_SETTINGS_PAGE_URL = "https://grok.com/?_s=data"
_X_USERID_RE = re.compile(r"x-userid=([^;,\s]+)")
_USER_ID_RE = re.compile(r'userId(?:\\")?\s*:\s*(?:\\")?([0-9a-fA-F-]{36})')


def _get_nsfw_semaphore() -> asyncio.Semaphore:
    value = max(1, int(get_config("nsfw.concurrent")))
    global _NSFW_SEMAPHORE, _NSFW_SEM_VALUE
    if _NSFW_SEMAPHORE is None or value != _NSFW_SEM_VALUE:
        _NSFW_SEM_VALUE = value
        _NSFW_SEMAPHORE = asyncio.Semaphore(value)
    return _NSFW_SEMAPHORE


def _get_nsfw_prerequisite_error() -> Optional[str]:
    base_proxy = str(get_config("proxy.base_proxy_url") or "").strip()
    reverse_base = str(get_config("proxy.reverse_base_url") or "").strip()
    cf_clearance = str(get_config("proxy.cf_clearance") or "").strip()
    cf_cookies = str(get_config("proxy.cf_cookies") or "").strip()

    if base_proxy or reverse_base or cf_clearance or cf_cookies:
        return None

    return (
        "NSFW enable requires a proxy/reverse proxy or Cloudflare cookies "
        "(proxy.base_proxy_url, proxy.reverse_base_url, proxy.cf_clearance, or proxy.cf_cookies)."
    )


def _find_token_info(mgr, token: str):
    pools = getattr(mgr, "pools", None)
    if not isinstance(pools, dict):
        return None
    raw_token = token[4:] if token.startswith("sso=") else token
    for pool in pools.values():
        getter = getattr(pool, "get", None)
        if not callable(getter):
            continue
        token_info = getter(raw_token)
        if token_info:
            return token_info
    return None


def _extra_cookies_from_token_info(token_info: Any) -> str:
    if not token_info:
        return ""
    note = getattr(token_info, "note", "") or ""
    if not isinstance(note, str):
        note = str(note)
    note = note.strip()
    if not note:
        return ""
    lowered = note.lower()
    if lowered.startswith("cookie:") or lowered.startswith("cookies:"):
        return note.split(":", 1)[1].strip()
    if note.startswith("x-userid="):
        return note
    return ""


def _extract_x_userid_cookie(headers: Any) -> str:
    if not headers:
        return ""

    cookie_values = []
    get_list = getattr(headers, "get_list", None)
    if callable(get_list):
        try:
            cookie_values.extend(str(v) for v in get_list("set-cookie"))
        except Exception:
            pass

    items = getattr(headers, "items", None)
    if callable(items):
        try:
            for key, value in items():
                if str(key).lower() == "set-cookie":
                    cookie_values.append(str(value))
        except Exception:
            pass

    joined = "\n".join(cookie_values)
    match = _X_USERID_RE.search(joined)
    if not match:
        return ""
    return f"x-userid={match.group(1)}"


def _extract_x_userid_from_body(response: Any) -> str:
    body_text = ""
    text = getattr(response, "text", None)
    if isinstance(text, str):
        body_text = text
    elif isinstance(text, bytes):
        body_text = text.decode("utf-8", errors="ignore")
    else:
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            body_text = content.decode("utf-8", errors="ignore")

    if not body_text:
        return ""

    match = _USER_ID_RE.search(body_text)
    if not match:
        return ""
    return f"x-userid={match.group(1)}"


async def _resolve_nsfw_extra_cookies(session, token: str, mgr) -> str:
    token_info = _find_token_info(mgr, token)
    extra_cookies = _extra_cookies_from_token_info(token_info)
    if extra_cookies:
        return extra_cookies

    base_proxy = get_config("proxy.base_proxy_url")
    proxies = {"http": base_proxy, "https": base_proxy} if base_proxy else None
    browser = get_config("proxy.browser")
    timeout = get_config("nsfw.timeout")
    headers = build_headers(
        cookie_token=token,
        origin="https://grok.com",
        referer="https://grok.com/?_s=data",
    )
    headers["Accept"] = (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    )
    headers["Sec-Fetch-Dest"] = "document"
    headers["Cache-Control"] = "no-cache"
    headers["Pragma"] = "no-cache"

    try:
        response = await session.get(
            resolve_api_url(NSFW_SETTINGS_PAGE_URL),
            headers=headers,
            timeout=timeout,
            proxies=proxies,
            impersonate=browser,
        )
    except Exception as exc:
        logger.warning(f"NSFW x-userid probe failed: {exc}")
        return ""

    if response.status_code != 200:
        logger.warning(
            f"NSFW x-userid probe returned {response.status_code}",
            extra={"error_type": "UpstreamException"},
        )
        return ""

    extra_cookies = _extract_x_userid_cookie(getattr(response, "headers", None))
    if not extra_cookies:
        extra_cookies = _extract_x_userid_from_body(response)
    if extra_cookies and token_info and not getattr(token_info, "note", ""):
        token_info.note = extra_cookies
    return extra_cookies


class NSFWService:
    """NSFW 模式服务"""
    @staticmethod
    async def batch(
        tokens: list[str],
        mgr,
        *,
        on_item: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Batch enable NSFW."""
        batch_size = get_config("nsfw.batch_size")
        prerequisite_error = _get_nsfw_prerequisite_error()
        if prerequisite_error:
            logger.warning(prerequisite_error)

        async def _enable(token: str):
            try:
                if prerequisite_error:
                    return {
                        "success": False,
                        "http_status": 400,
                        "error": prerequisite_error,
                    }

                browser = get_config("proxy.browser")
                async with ResettableSession(impersonate=browser) as session:
                    extra_cookies = await _resolve_nsfw_extra_cookies(session, token, mgr)

                    async def _record_fail(err: UpstreamException, reason: str):
                        status = None
                        if err.details and "status" in err.details:
                            status = err.details["status"]
                        else:
                            status = getattr(err, "status_code", None)
                        if status == 401:
                            await mgr.record_fail(token, status, reason)
                        return status or 0

                    try:
                        async with _get_nsfw_semaphore():
                            await SetBirthReverse.request(
                                session,
                                token,
                                extra_cookies=extra_cookies,
                            )
                    except UpstreamException as e:
                        status = await _record_fail(e, "set_birth_auth_failed")
                        return {
                            "success": False,
                            "http_status": status,
                            "error": f"Set birth date failed: {str(e)}",
                        }

                    try:
                        async with _get_nsfw_semaphore():
                            grpc_status = await NsfwMgmtReverse.request(
                                session,
                                token,
                                extra_cookies=extra_cookies,
                            )
                        success = grpc_status.code in (-1, 0)
                    except UpstreamException as e:
                        status = await _record_fail(e, "nsfw_mgmt_auth_failed")
                        return {
                            "success": False,
                            "http_status": status,
                            "error": f"NSFW enable failed: {str(e)}",
                        }
                    if success:
                        await mgr.add_tag(token, "nsfw")
                    return {
                        "success": success,
                        "http_status": 200,
                        "grpc_status": grpc_status.code,
                        "grpc_message": grpc_status.message or None,
                        "error": None,
                    }
            except Exception as e:
                logger.error(f"NSFW enable failed: {e}")
                return {"success": False, "http_status": 0, "error": str(e)[:100]}

        return await run_batch(
            tokens,
            _enable,
            batch_size=batch_size,
            on_item=on_item,
            should_cancel=should_cancel,
        )


__all__ = ["NSFWService"]
