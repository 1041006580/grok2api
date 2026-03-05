"""
Mask helpers for sensitive strings in logs.
"""

from __future__ import annotations


def mask_token_for_log(token: str, prefix: int = 8, suffix: int = 8) -> str:
    """
    Return a stable masked token string in `prefix...suffix` format.

    Notes:
    - Accepts token with or without `sso=` prefix.
    - Keeps output short while still distinguishable in logs.
    """
    raw = str(token or "")
    if raw.startswith("sso="):
        raw = raw[4:]
    if not raw:
        return ""

    keep_prefix = max(1, int(prefix))
    keep_suffix = max(1, int(suffix))

    if len(raw) <= keep_prefix + keep_suffix:
        # Ensure we still hide middle part for short strings.
        if len(raw) <= 4:
            return "*" * len(raw)
        head = raw[: max(2, len(raw) // 2)]
        tail = raw[-2:]
        return f"{head}...{tail}"

    return f"{raw[:keep_prefix]}...{raw[-keep_suffix:]}"


__all__ = ["mask_token_for_log"]
