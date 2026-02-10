"""
图像来源台账服务
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

import aiofiles
import orjson

from app.core.logger import logger
from app.core.storage import LocalStorage, RedisStorage, SQLStorage, get_storage


ORIGIN_GENERATED = "generated"
ORIGIN_UPLOADED = "uploaded"
ORIGIN_UNKNOWN = "unknown"

REFERENCE_BASE64 = "base64"
REFERENCE_GENERATED_URL = "generated_url"
REFERENCE_UPLOADED_URL = "uploaded_url"
REFERENCE_UNKNOWN_URL = "unknown_url"


_BASE64_CHARS_RE = re.compile(r"^[A-Za-z0-9+/=_\-\s]+$")
_ASSET_CONTENT_RE = re.compile(r"/users/[^/]+/([^/]+)/content(?:$|[/?#])", re.IGNORECASE)


def is_http_url(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_image_url(value: str) -> str:
    if not value:
        return ""

    raw = value.strip()
    if not raw:
        return ""

    if raw.startswith("data:"):
        return raw

    if raw.lower().startswith("v1/files/image/"):
        return f"/{raw}"

    if raw.startswith("/"):
        return raw.rstrip("/") if raw != "/" else "/"

    if not is_http_url(raw):
        return raw

    parsed = urlparse(raw)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def extract_asset_id_from_url(image_url: str) -> Optional[str]:
    if not image_url:
        return None
    normalized = normalize_image_url(image_url)
    if not normalized:
        return None

    path = normalized
    if is_http_url(normalized):
        path = urlparse(normalized).path

    match = _ASSET_CONTENT_RE.search(path)
    if not match:
        return None
    asset_id = (match.group(1) or "").strip()
    return asset_id or None


def looks_like_base64(value: str) -> bool:
    if not value:
        return False
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith("data:"):
        return True
    if is_http_url(raw):
        return False
    if len(raw) < 128:
        return False
    return bool(_BASE64_CHARS_RE.match(raw))


def _strip_data_prefix(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith("data:"):
        idx = raw.find(",")
        if idx >= 0:
            raw = raw[idx + 1 :]
    return "".join(raw.split())


def sha256_of_image_base64(value: str) -> Optional[str]:
    if not looks_like_base64(value):
        return None

    payload = _strip_data_prefix(value)
    if not payload:
        return None

    padding = len(payload) % 4
    if padding:
        payload = payload + ("=" * (4 - padding))

    try:
        decoded = base64.b64decode(payload, validate=False)
    except Exception:
        return None

    if not decoded:
        return None
    return hashlib.sha256(decoded).hexdigest()


def inspect_image_reference(image_ref: str) -> Dict[str, Optional[str]]:
    raw = (image_ref or "").strip()
    if not raw:
        return {
            "kind": REFERENCE_UNKNOWN_URL,
            "normalized": "",
            "asset_id": None,
        }

    if looks_like_base64(raw):
        return {
            "kind": REFERENCE_BASE64,
            "normalized": "",
            "asset_id": None,
        }

    normalized = normalize_image_url(raw)
    parsed = urlparse(normalized) if is_http_url(normalized) else None
    host = (parsed.netloc or "").lower() if parsed else ""
    path = parsed.path if parsed else normalized
    lowered_path = (path or "").lower()

    if "imagine-public.x.ai" in host or "/imagine-public/images/" in lowered_path:
        return {
            "kind": REFERENCE_GENERATED_URL,
            "normalized": normalized,
            "asset_id": None,
        }

    if "/v1/files/image/" in lowered_path or lowered_path.startswith("/v1/files/image/"):
        return {
            "kind": REFERENCE_GENERATED_URL,
            "normalized": normalized,
            "asset_id": None,
        }

    asset_id = extract_asset_id_from_url(normalized)
    if asset_id:
        return {
            "kind": REFERENCE_UPLOADED_URL,
            "normalized": normalized,
            "asset_id": asset_id,
        }

    if "assets.grok.com" in host and "/generated/" in lowered_path:
        return {
            "kind": REFERENCE_GENERATED_URL,
            "normalized": normalized,
            "asset_id": None,
        }

    return {
        "kind": REFERENCE_UNKNOWN_URL,
        "normalized": normalized,
        "asset_id": None,
    }


class ImageOriginLedger:
    LOCAL_LEDGER_FILE = Path(__file__).parent.parent.parent / "data" / "image_origin_ledger.json"
    REDIS_LEDGER_KEY = "grok2api:image-origin:entries"

    def __init__(self):
        self.storage = get_storage()
        self._sql_ready = False
        self._sql_ready_lock = asyncio.Lock()

    @staticmethod
    def _lookup_key(key_type: str, key_value: str) -> str:
        payload = f"{key_type}:{key_value}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_hash(value: str) -> str:
        return (value or "").strip().lower()

    def _iter_lookup_items(
        self,
        canonical_url: str,
        original_url: str,
        sha256_hash: str,
        asset_id: str,
    ):
        seen = set()

        for url in (canonical_url, original_url):
            normalized = normalize_image_url(url)
            if normalized and normalized not in seen:
                seen.add(normalized)
                yield "url", normalized

        normalized_hash = self._normalize_hash(sha256_hash)
        if normalized_hash and normalized_hash not in seen:
            seen.add(normalized_hash)
            yield "hash", normalized_hash

        normalized_asset_id = (asset_id or "").strip()
        if normalized_asset_id and normalized_asset_id not in seen:
            seen.add(normalized_asset_id)
            yield "asset", normalized_asset_id

    async def _ensure_sql_table(self):
        if not isinstance(self.storage, SQLStorage):
            return
        if self._sql_ready:
            return

        async with self._sql_ready_lock:
            if self._sql_ready:
                return

            from sqlalchemy import text

            await self.storage._ensure_schema()
            async with self.storage.engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS image_origin_ledger (
                            lookup_key VARCHAR(80) PRIMARY KEY,
                            key_type VARCHAR(16) NOT NULL,
                            key_value TEXT NOT NULL,
                            entry_json TEXT NOT NULL,
                            updated_at BIGINT NOT NULL
                        )
                        """
                    )
                )
                try:
                    await conn.execute(
                        text(
                            "CREATE INDEX idx_image_origin_ledger_updated_at ON image_origin_ledger (updated_at)"
                        )
                    )
                except Exception:
                    pass

            self._sql_ready = True

    async def _read_local_entries(self) -> Dict[str, Dict[str, Any]]:
        path = self.LOCAL_LEDGER_FILE
        if not path.exists():
            return {}

        try:
            async with aiofiles.open(path, "rb") as f:
                content = await f.read()
            if not content:
                return {}
            data = orjson.loads(content)
            if isinstance(data, dict):
                entries = data.get("entries")
                if isinstance(entries, dict):
                    return entries
            return {}
        except Exception as e:
            logger.warning(f"ImageOriginLedger local read failed: {e}")
            return {}

    async def _write_local_entries(self, entries: Dict[str, Dict[str, Any]]):
        path = self.LOCAL_LEDGER_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": entries,
            "updated_at": int(time.time()),
        }

        tmp_path = path.with_suffix(".tmp")
        async with aiofiles.open(tmp_path, "wb") as f:
            await f.write(orjson.dumps(payload))
        os.replace(tmp_path, path)

    async def upsert_origin(
        self,
        source_type: str,
        canonical_url: str = "",
        original_url: str = "",
        sha256_hash: str = "",
        asset_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        source = (source_type or ORIGIN_UNKNOWN).strip().lower()
        if source not in {ORIGIN_GENERATED, ORIGIN_UPLOADED, ORIGIN_UNKNOWN}:
            source = ORIGIN_UNKNOWN

        normalized_canonical = normalize_image_url(canonical_url)
        normalized_original = normalize_image_url(original_url)
        normalized_hash = self._normalize_hash(sha256_hash)
        normalized_asset_id = (asset_id or "").strip()

        if not any([normalized_canonical, normalized_original, normalized_hash, normalized_asset_id]):
            return

        base_entry: Dict[str, Any] = {
            "source_type": source,
            "canonical_url": normalized_canonical,
            "original_url": normalized_original,
            "sha256": normalized_hash,
            "asset_id": normalized_asset_id,
            "updated_at": int(time.time()),
        }
        if metadata:
            base_entry["metadata"] = metadata

        entries_to_write: Dict[str, Dict[str, Any]] = {}
        for key_type, key_value in self._iter_lookup_items(
            normalized_canonical,
            normalized_original,
            normalized_hash,
            normalized_asset_id,
        ):
            lookup_key = self._lookup_key(key_type, key_value)
            entry = dict(base_entry)
            entry["key_type"] = key_type
            entry["key_value"] = key_value
            entries_to_write[lookup_key] = entry

        if not entries_to_write:
            return

        try:
            if isinstance(self.storage, LocalStorage):
                async with self.storage.acquire_lock("image_origin_ledger", timeout=10):
                    current_entries = await self._read_local_entries()
                    current_entries.update(entries_to_write)
                    await self._write_local_entries(current_entries)
                return

            if isinstance(self.storage, RedisStorage):
                mapping = {
                    lookup_key: orjson.dumps(entry).decode("utf-8")
                    for lookup_key, entry in entries_to_write.items()
                }
                await self.storage.redis.hset(self.REDIS_LEDGER_KEY, mapping=mapping)
                return

            if isinstance(self.storage, SQLStorage):
                await self._ensure_sql_table()
                from sqlalchemy import text

                async with self.storage.async_session() as session:
                    for lookup_key, entry in entries_to_write.items():
                        await session.execute(
                            text("DELETE FROM image_origin_ledger WHERE lookup_key=:lookup_key"),
                            {"lookup_key": lookup_key},
                        )
                        await session.execute(
                            text(
                                """
                                INSERT INTO image_origin_ledger (
                                    lookup_key,
                                    key_type,
                                    key_value,
                                    entry_json,
                                    updated_at
                                ) VALUES (
                                    :lookup_key,
                                    :key_type,
                                    :key_value,
                                    :entry_json,
                                    :updated_at
                                )
                                """
                            ),
                            {
                                "lookup_key": lookup_key,
                                "key_type": entry.get("key_type", ""),
                                "key_value": entry.get("key_value", ""),
                                "entry_json": orjson.dumps(entry).decode("utf-8"),
                                "updated_at": entry.get("updated_at", int(time.time())),
                            },
                        )
                    await session.commit()
                return
        except Exception as e:
            logger.warning(f"ImageOriginLedger upsert failed: {e}")

    async def _find_by_key(self, key_type: str, key_value: str) -> Optional[Dict[str, Any]]:
        normalized_key = (key_value or "").strip()
        if not normalized_key:
            return None

        lookup_key = self._lookup_key(key_type, normalized_key)

        try:
            if isinstance(self.storage, LocalStorage):
                async with self.storage.acquire_lock("image_origin_ledger", timeout=10):
                    entries = await self._read_local_entries()
                    record = entries.get(lookup_key)
                return record if isinstance(record, dict) else None

            if isinstance(self.storage, RedisStorage):
                raw = await self.storage.redis.hget(self.REDIS_LEDGER_KEY, lookup_key)
                if not raw:
                    return None
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="ignore")
                record = orjson.loads(raw)
                return record if isinstance(record, dict) else None

            if isinstance(self.storage, SQLStorage):
                await self._ensure_sql_table()
                from sqlalchemy import text

                async with self.storage.async_session() as session:
                    result = await session.execute(
                        text("SELECT entry_json FROM image_origin_ledger WHERE lookup_key=:lookup_key"),
                        {"lookup_key": lookup_key},
                    )
                    raw = result.scalar()
                if not raw:
                    return None
                record = orjson.loads(raw)
                return record if isinstance(record, dict) else None
        except Exception as e:
            logger.warning(f"ImageOriginLedger find failed: {e}")
            return None

        return None

    async def find_by_url(self, image_url: str) -> Optional[Dict[str, Any]]:
        normalized_url = normalize_image_url(image_url)
        if not normalized_url:
            return None
        return await self._find_by_key("url", normalized_url)

    async def find_by_hash(self, sha256_hash: str) -> Optional[Dict[str, Any]]:
        normalized_hash = self._normalize_hash(sha256_hash)
        if not normalized_hash:
            return None
        return await self._find_by_key("hash", normalized_hash)

    async def find_by_asset_id(self, asset_id: str) -> Optional[Dict[str, Any]]:
        normalized_asset_id = (asset_id or "").strip()
        if not normalized_asset_id:
            return None
        return await self._find_by_key("asset", normalized_asset_id)


_IMAGE_ORIGIN_LEDGER: Optional[ImageOriginLedger] = None


def get_image_origin_ledger() -> ImageOriginLedger:
    global _IMAGE_ORIGIN_LEDGER
    if _IMAGE_ORIGIN_LEDGER is None:
        _IMAGE_ORIGIN_LEDGER = ImageOriginLedger()
    return _IMAGE_ORIGIN_LEDGER


__all__ = [
    "ORIGIN_GENERATED",
    "ORIGIN_UPLOADED",
    "ORIGIN_UNKNOWN",
    "REFERENCE_BASE64",
    "REFERENCE_GENERATED_URL",
    "REFERENCE_UPLOADED_URL",
    "REFERENCE_UNKNOWN_URL",
    "normalize_image_url",
    "extract_asset_id_from_url",
    "inspect_image_reference",
    "is_http_url",
    "looks_like_base64",
    "sha256_of_image_base64",
    "ImageOriginLedger",
    "get_image_origin_ledger",
]
