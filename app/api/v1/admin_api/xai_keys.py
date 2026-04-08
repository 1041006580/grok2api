from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_app_key
from app.core.config import config

router = APIRouter()


def _resolve_xai_section() -> Mapping[str, Any]:
    base_config = getattr(config, "_config", None) or {}
    section = {}
    if isinstance(base_config, Mapping):
        section = base_config.get("xai", {}) or {}
    if not isinstance(section, Mapping):
        section = {}
    if not section:
        defaults = getattr(config, "_defaults", None) or {}
        if isinstance(defaults, Mapping):
            section = defaults.get("xai", {}) or {}
    return section if isinstance(section, Mapping) else {}


def _collect_keys() -> list[dict[str, Any]]:
    section = _resolve_xai_section()
    keys = section.get("keys", [])
    normalized: list[dict[str, Any]] = []
    if not isinstance(keys, list):
        return normalized
    for raw in keys:
        if isinstance(raw, Mapping):
            normalized.append(dict(raw))
        elif isinstance(raw, str):
            normalized.append({"id": raw, "key": raw, "enabled": False})
    return normalized


def _normalize_enabled(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return default


def _mask_key_value(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value)
    length = len(raw)
    if length <= 4:
        return "*" * length
    if length <= 8:
        return f"{raw[:2]}{'*' * (length - 2)}"
    return f"{raw[:4]}{'*' * 4}{raw[-4:]}"


def _format_key_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "enabled": _normalize_enabled(entry.get("enabled"), False),
        "value": _mask_key_value(entry.get("key")),
    }


def _find_key_index(keys: list[dict[str, Any]], key_id: str) -> int:
    for idx, entry in enumerate(keys):
        if entry.get("id") == key_id:
            return idx
    return -1


async def _persist_keys(keys: list[dict[str, Any]]) -> None:
    await config.update({"xai": {"keys": keys}})


@router.get("/xai-keys", dependencies=[Depends(verify_app_key)])
async def get_xai_keys():
    """Retrieve all configured xAI keys with masked values."""
    keys = _collect_keys()
    return {"status": "success", "keys": [_format_key_payload(key) for key in keys]}


@router.post("/xai-keys", dependencies=[Depends(verify_app_key)])
async def create_xai_key(data: dict[str, Any]):
    """Create a new xAI key entry."""
    key_value = str(data.get("key", "") or "").strip()
    if not key_value:
        raise HTTPException(status_code=400, detail="xAI key is required")

    keys = _collect_keys()
    key_id = str(data.get("id") or uuid4())
    if _find_key_index(keys, key_id) >= 0:
        raise HTTPException(status_code=400, detail="xAI key already exists")

    new_entry = {
        "id": key_id,
        "key": key_value,
        "name": data.get("name"),
        "enabled": _normalize_enabled(data.get("enabled"), True),
    }
    keys.append(new_entry)
    await _persist_keys(keys)
    return {"status": "success", "key": _format_key_payload(new_entry)}


@router.patch("/xai-keys/{key_id}", dependencies=[Depends(verify_app_key)])
async def update_xai_key(key_id: str, data: dict[str, Any]):
    """Update metadata or enablement for a specific xAI key."""
    keys = _collect_keys()
    idx = _find_key_index(keys, key_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="xAI key not found")

    entry = dict(keys[idx])
    if "name" in data:
        entry["name"] = data.get("name")
    if "enabled" in data:
        entry["enabled"] = _normalize_enabled(data.get("enabled"), entry.get("enabled", False))
    if "key" in data:
        key_value = str(data.get("key", "") or "").strip()
        if key_value:
            entry["key"] = key_value

    keys[idx] = entry
    await _persist_keys(keys)
    return {"status": "success", "key": _format_key_payload(entry)}


@router.delete("/xai-keys/{key_id}", dependencies=[Depends(verify_app_key)])
async def delete_xai_key(key_id: str):
    """Remove an xAI key from the pool."""
    keys = _collect_keys()
    idx = _find_key_index(keys, key_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="xAI key not found")
    keys.pop(idx)
    await _persist_keys(keys)
    return {"status": "success"}
