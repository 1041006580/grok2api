"""xAI Official API client — uses xai.keys from config.

This module talks to https://api.x.ai/v1 using API keys managed via the
admin xai-keys endpoint. It is **not** related to grok.com reverse-engineered
endpoints; xAI Official API is used for video generation when grok.com
endpoints are unavailable.
"""

from dataclasses import dataclass
from typing import Iterable

from app.platform.config.snapshot import config


@dataclass(frozen=True, slots=True)
class XAIKey:
    """An xAI Official API key entry."""

    id: str
    key: str
    name: str | None = None
    enabled: bool = False


def _parse_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return False


def load_xai_keys() -> list[XAIKey]:
    """Load all xAI keys from config — returns the full list including disabled."""
    raw = config.raw()
    xai_section = raw.get("xai", {}) or {}
    if not isinstance(xai_section, dict):
        return []
    raw_keys = xai_section.get("keys", []) or []
    if not isinstance(raw_keys, list):
        return []

    parsed: list[XAIKey] = []
    for entry in raw_keys:
        if not isinstance(entry, dict):
            continue
        kid = str(entry.get("id", "") or "").strip()
        kvalue = str(entry.get("key", "") or "").strip()
        if not kid or not kvalue:
            continue
        parsed.append(
            XAIKey(
                id=kid,
                key=kvalue,
                name=(str(entry.get("name", "") or "").strip() or None),
                enabled=_parse_enabled(entry.get("enabled", False)),
            )
        )
    return parsed


def active_xai_keys() -> list[XAIKey]:
    """Return only enabled keys."""
    return [k for k in load_xai_keys() if k.enabled]


def acquire_xai_key() -> XAIKey | None:
    """Return the first usable xAI key, or None if no key is configured/enabled."""
    keys = active_xai_keys()
    return keys[0] if keys else None


__all__ = ["XAIKey", "load_xai_keys", "active_xai_keys", "acquire_xai_key"]
