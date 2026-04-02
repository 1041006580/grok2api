"""Helpers for parsing SSO rate-limit responses."""

from typing import Any, Mapping, Optional


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
