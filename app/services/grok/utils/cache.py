"""
Cache utilities for active media storage backend (local or R2).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.core.storage import DATA_DIR
from app.services.media_storage import (
    R2MediaStorage,
    get_media_storage,
    get_media_storage_type,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


class CacheService:
    """Cache service for the current media backend."""

    def __init__(self):
        self.storage_type = get_media_storage_type()
        self.storage = get_media_storage()
        base_dir = DATA_DIR / "tmp"
        self.image_dir = base_dir / "image"
        self.video_dir = base_dir / "video"

    def _cache_dir(self, media_type: str):
        return self.image_dir if media_type == "image" else self.video_dir

    def _normalize_media_type(self, media_type: str) -> str:
        return "video" if media_type == "video" else "image"

    def _allowed_exts(self, media_type: str):
        return IMAGE_EXTS if media_type == "image" else VIDEO_EXTS

    def _is_allowed_name(self, media_type: str, name: str) -> bool:
        ext = ""
        if "." in name:
            ext = f".{name.rsplit('.', 1)[-1].lower()}"
        return ext in self._allowed_exts(media_type)

    async def get_stats(self, media_type: str = "image") -> Dict[str, Any]:
        media_type = self._normalize_media_type(media_type)
        if self.storage_type == "r2":
            return await self._get_stats_r2(media_type)
        return await asyncio.to_thread(self._get_stats_local, media_type)

    async def list_files(
        self, media_type: str = "image", page: int = 1, page_size: int = 1000
    ) -> Dict[str, Any]:
        media_type = self._normalize_media_type(media_type)
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        if self.storage_type == "r2":
            return await self._list_files_r2(media_type, page, page_size)
        return await asyncio.to_thread(self._list_files_local, media_type, page, page_size)

    async def delete_file(self, media_type: str, name: str) -> Dict[str, Any]:
        media_type = self._normalize_media_type(media_type)
        if self.storage_type == "r2":
            return await self._delete_file_r2(media_type, name)
        return await asyncio.to_thread(self._delete_file_local, media_type, name)

    async def clear(self, media_type: str = "image") -> Dict[str, Any]:
        media_type = self._normalize_media_type(media_type)
        if self.storage_type == "r2":
            return await self._clear_r2(media_type)
        return await asyncio.to_thread(self._clear_local, media_type)

    def _get_stats_local(self, media_type: str) -> Dict[str, Any]:
        cache_dir = self._cache_dir(media_type)
        if not cache_dir.exists():
            return {"count": 0, "size_mb": 0.0}
        allowed = self._allowed_exts(media_type)
        files = [
            f for f in cache_dir.glob("*") if f.is_file() and f.suffix.lower() in allowed
        ]
        total_size = sum(f.stat().st_size for f in files)
        return {"count": len(files), "size_mb": round(total_size / 1024 / 1024, 2)}

    def _list_files_local(self, media_type: str, page: int, page_size: int) -> Dict[str, Any]:
        cache_dir = self._cache_dir(media_type)
        if not cache_dir.exists():
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        allowed = self._allowed_exts(media_type)
        files = [
            f for f in cache_dir.glob("*") if f.is_file() and f.suffix.lower() in allowed
        ]
        items = []
        for f in files:
            try:
                stat = f.stat()
                items.append(
                    {
                        "name": f.name,
                        "size_bytes": stat.st_size,
                        "mtime_ms": int(stat.st_mtime * 1000),
                    }
                )
            except Exception:
                continue
        items.sort(key=lambda x: x["mtime_ms"], reverse=True)
        total = len(items)
        start = max(0, (page - 1) * page_size)
        paged = items[start : start + page_size]
        for item in paged:
            item["view_url"] = f"/v1/files/{media_type}/{item['name']}"
        return {"total": total, "page": page, "page_size": page_size, "items": paged}

    def _delete_file_local(self, media_type: str, name: str) -> Dict[str, Any]:
        cache_dir = self._cache_dir(media_type)
        file_path = cache_dir / name.replace("/", "-")
        if file_path.exists():
            try:
                file_path.unlink()
                return {"deleted": True}
            except Exception:
                pass
        return {"deleted": False}

    def _clear_local(self, media_type: str) -> Dict[str, Any]:
        cache_dir = self._cache_dir(media_type)
        if not cache_dir.exists():
            return {"count": 0, "size_mb": 0.0}
        files = list(cache_dir.glob("*"))
        total_size = sum(f.stat().st_size for f in files if f.is_file())
        count = 0
        for f in files:
            if f.is_file():
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
        return {"count": count, "size_mb": round(total_size / 1024 / 1024, 2)}

    def _r2_parts(self, media_type: str):
        if not isinstance(self.storage, R2MediaStorage):
            raise RuntimeError("R2 storage backend is not active")
        prefix = (
            f"{self.storage.key_prefix}/{media_type}/"
            if self.storage.key_prefix
            else f"{media_type}/"
        )
        return self.storage._client, self.storage.bucket, prefix

    def _r2_object_key(self, media_type: str, name: str) -> str:
        safe_name = name.replace("/", "-")
        if isinstance(self.storage, R2MediaStorage) and self.storage.key_prefix:
            return f"{self.storage.key_prefix}/{media_type}/{safe_name}"
        return f"{media_type}/{safe_name}"

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        code = (
            getattr(exc, "response", {}).get("Error", {}).get("Code", "").strip().lower()
        )
        return code in {"404", "nosuchkey", "notfound", "no_such_key"}

    async def _list_r2_objects(self, media_type: str) -> List[Dict[str, Any]]:
        client, bucket, prefix = self._r2_parts(media_type)
        objects: List[Dict[str, Any]] = []
        token = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            resp = await asyncio.to_thread(client.list_objects_v2, **kwargs)
            for obj in resp.get("Contents", []) or []:
                key = str(obj.get("Key") or "")
                if not key or key.endswith("/"):
                    continue
                raw_name = key[len(prefix) :] if key.startswith(prefix) else key
                if not raw_name:
                    continue
                name = raw_name.replace("/", "-")
                if not self._is_allowed_name(media_type, name):
                    continue
                last_modified = obj.get("LastModified")
                mtime_ms = int(last_modified.timestamp() * 1000) if last_modified else 0
                objects.append(
                    {
                        "name": name,
                        "size_bytes": int(obj.get("Size", 0) or 0),
                        "mtime_ms": mtime_ms,
                        "_key": key,
                    }
                )
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break
        return objects

    async def _get_stats_r2(self, media_type: str) -> Dict[str, Any]:
        objects = await self._list_r2_objects(media_type)
        total_size = sum(item["size_bytes"] for item in objects)
        return {"count": len(objects), "size_mb": round(total_size / 1024 / 1024, 2)}

    async def _list_files_r2(
        self, media_type: str, page: int, page_size: int
    ) -> Dict[str, Any]:
        objects = await self._list_r2_objects(media_type)
        objects.sort(key=lambda x: x["mtime_ms"], reverse=True)
        total = len(objects)
        start = max(0, (page - 1) * page_size)
        paged = objects[start : start + page_size]
        items = []
        for item in paged:
            items.append(
                {
                    "name": item["name"],
                    "size_bytes": item["size_bytes"],
                    "mtime_ms": item["mtime_ms"],
                    "view_url": f"/v1/files/{media_type}/{item['name']}",
                }
            )
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    async def _delete_file_r2(self, media_type: str, name: str) -> Dict[str, Any]:
        client, bucket, _ = self._r2_parts(media_type)
        key = self._r2_object_key(media_type, name)

        def _delete_if_exists() -> bool:
            try:
                client.head_object(Bucket=bucket, Key=key)
            except Exception as e:
                if self._is_not_found_error(e):
                    return False
                raise
            client.delete_object(Bucket=bucket, Key=key)
            return True

        deleted = await asyncio.to_thread(_delete_if_exists)
        return {"deleted": deleted}

    async def _clear_r2(self, media_type: str) -> Dict[str, Any]:
        client, bucket, _ = self._r2_parts(media_type)
        objects = await self._list_r2_objects(media_type)
        if not objects:
            return {"count": 0, "size_mb": 0.0}
        total_size = sum(item["size_bytes"] for item in objects)
        keys = [item["_key"] for item in objects if item.get("_key")]
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]

            def _delete_batch() -> int:
                result = client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
                return len(batch) - len(result.get("Errors", []) or [])

            deleted += await asyncio.to_thread(_delete_batch)
        return {"count": deleted, "size_mb": round(total_size / 1024 / 1024, 2)}


__all__ = ["CacheService"]
