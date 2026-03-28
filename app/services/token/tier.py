"""Helpers for classifying SSO token tier from rate-limit responses."""

from typing import Any, Mapping, Optional

HEAVY_THRESHOLD = 41
SUPER_THRESHOLD = 9


def extract_remaining_quota(result: Mapping[str, Any] | None) -> Optional[int]:
    """Extract remaining quota from a /rest/rate-limits payload."""
    if not isinstance(result, Mapping):
        return None

    for key in ("remainingQueries", "remainingTokens"):
        value = result.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    limits = result.get("limits") or result.get("rateLimits")
    if isinstance(limits, Mapping):
        for key in ("remainingQueries", "remainingTokens"):
            value = limits.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

    return None


def classify_remaining_tier(remaining: Optional[int]) -> str:
    """Match the upstream sso-tier-checker thresholds."""
    if remaining is None:
        return "unknown"
    if remaining >= HEAVY_THRESHOLD:
        return "heavy"
    if remaining >= SUPER_THRESHOLD:
        return "super"
    return "basic"

