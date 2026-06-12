"""Admin XAI keys management — CRUD for x.ai API keys.

XAI keys are stored in persistent config (TOML or SQL backend) under:
  config["xai"]["keys"] = [{id, key, name, enabled}, ...]
"""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.platform.config.snapshot import config

router = APIRouter(tags=["Admin - XAI Keys"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_key(value: str) -> str:
    """Mask API key for display."""
    if not value:
        return ""
    length = len(value)
    if length <= 4:
        return "*" * length
    if length <= 8:
        return f"{value[:2]}{'*' * (length - 2)}"
    return f"{value[:4]}{'*' * 4}{value[-4:]}"


def _load_keys() -> list[dict[str, Any]]:
    """Load xai.keys from config."""
    raw = config.raw()
    xai_section = raw.get("xai", {}) or {}
    if not isinstance(xai_section, dict):
        return []
    keys = xai_section.get("keys", [])
    return keys if isinstance(keys, list) else []


async def _save_keys(keys: list[dict[str, Any]]) -> None:
    """Persist xai.keys to config storage."""
    await config.update({"xai": {"keys": keys}})
    await config.load()


def _find_key_index(keys: list[dict[str, Any]], key_id: str) -> int:
    """Return index of key with id==key_id, or -1."""
    for idx, entry in enumerate(keys):
        if entry.get("id") == key_id:
            return idx
    return -1


def _format_key(entry: dict[str, Any]) -> dict[str, Any]:
    """Format key entry for response (mask value)."""
    return {
        "id": entry.get("id"),
        "name": entry.get("name"),
        "enabled": bool(entry.get("enabled", False)),
        "value": _mask_key(entry.get("key", "")),
    }

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateKeyRequest(BaseModel):
    key: str
    name: str | None = None
    enabled: bool = True


class UpdateKeyRequest(BaseModel):
    key: str | None = None
    name: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/xai-keys")
async def list_xai_keys():
    """List all XAI keys (masked)."""
    keys = _load_keys()
    return {"status": "success", "keys": [_format_key(k) for k in keys]}


@router.post("/xai-keys")
async def create_xai_key(req: CreateKeyRequest):
    """Create a new XAI key."""
    if not req.key.strip():
        raise HTTPException(status_code=400, detail="key is required")

    keys = _load_keys()
    key_id = str(uuid4())
    entry = {
        "id": key_id,
        "key": req.key.strip(),
        "name": req.name.strip() if req.name else None,
        "enabled": req.enabled,
    }
    keys.append(entry)
    await _save_keys(keys)
    return {"status": "success", "key": _format_key(entry)}


@router.patch("/xai-keys/{key_id}")
async def update_xai_key(key_id: str, req: UpdateKeyRequest):
    """Update an XAI key."""
    keys = _load_keys()
    idx = _find_key_index(keys, key_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="xAI key not found")

    entry = dict(keys[idx])
    if req.key is not None:
        if not req.key.strip():
            raise HTTPException(status_code=400, detail="key cannot be empty")
        entry["key"] = req.key.strip()
    if req.name is not None:
        entry["name"] = req.name.strip() if req.name else None
    if req.enabled is not None:
        entry["enabled"] = req.enabled

    keys[idx] = entry
    await _save_keys(keys)
    return {"status": "success", "key": _format_key(entry)}


@router.delete("/xai-keys/{key_id}")
async def delete_xai_key(key_id: str):
    """Delete an XAI key."""
    keys = _load_keys()
    idx = _find_key_index(keys, key_id)
    if idx < 0:
        raise HTTPException(status_code=404, detail="xAI key not found")

    keys.pop(idx)
    await _save_keys(keys)
    return {"status": "success"}
