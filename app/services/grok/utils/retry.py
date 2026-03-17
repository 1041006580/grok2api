"""
Retry helpers for token switching.
"""

from typing import Optional, Set

from app.core.exceptions import UpstreamException
from app.services.grok.services.model import ModelService


async def pick_token(
    token_mgr,
    model_id: str,
    tried: Set[str],
    preferred: Optional[str] = None,
    prefer_tags: Optional[Set[str]] = None,
) -> Optional[str]:
    if preferred and preferred not in tried:
        return preferred

    token = None
    for pool_name in ModelService.pool_candidates_for_model(model_id):
        token = token_mgr.get_token(pool_name, exclude=tried, prefer_tags=prefer_tags)
        if token:
            break

    if not token:
        result = await token_mgr.refresh_cooling_tokens(model_id)
        if result.get("recovered", 0) > 0:
            for pool_name in ModelService.pool_candidates_for_model(model_id):
                token = token_mgr.get_token(pool_name, exclude=tried, prefer_tags=prefer_tags)
                if token:
                    break

    return token


def rate_limited(error: Exception) -> bool:
    if not isinstance(error, UpstreamException):
        return False
    status = error.details.get("status") if error.details else None
    code = error.details.get("error_code") if error.details else None
    return status == 429 or code == "rate_limit_exceeded"


def should_cool_token_on_rate_limit(model_id: str, error: Exception) -> bool:
    """Whether a 429 for this model should move the token into COOLING."""
    if not rate_limited(error):
        return False

    model = ModelService.get(model_id)
    if model and model.grok_model == "grok-420":
        return False

    return True


def transient_upstream(error: Exception) -> bool:
    """Whether error is likely transient and safe to retry with another token."""
    if not isinstance(error, UpstreamException):
        return False
    details = error.details or {}
    status = details.get("status")
    err = str(details.get("error") or error).lower()
    transient_status = {408, 500, 502, 503, 504}
    if status in transient_status:
        return True
    timeout_markers = (
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
        "http2",
    )
    return any(marker in err for marker in timeout_markers)


def explicit_auth_failure(error: Exception) -> bool:
    """Whether a 401 clearly indicates token authentication failure."""
    if not isinstance(error, UpstreamException):
        return False

    details = error.details or {}
    status = details.get("status")
    if status != 401:
        return False

    body = str(details.get("body") or details.get("error") or "").lower()
    if not body:
        return False

    # Cloudflare / HTML challenge pages should not expire tokens.
    cloudflare_markers = (
        "challenge-platform",
        "cloudflare",
        "<html",
        "<!doctype html",
    )
    if any(marker in body for marker in cloudflare_markers):
        return False

    auth_error_markers = (
        "unauthorized",
        "not logged in",
        "unauthenticated",
        "bad-credentials",
        "invalid_api_key",
        "invalid token",
        "auth_failed",
    )
    return any(marker in body for marker in auth_error_markers)


__all__ = [
    "explicit_auth_failure",
    "pick_token",
    "rate_limited",
    "should_cool_token_on_rate_limit",
    "transient_upstream",
]
