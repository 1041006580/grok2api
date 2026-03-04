"""
Media storage backend abstraction.

Supports local filesystem storage (default) and Cloudflare R2 (S3-compatible).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import aiofiles

from app.core.config import get_config
from app.core.logger import logger
from app.core.storage import DATA_DIR


def _config_str(config_key: str, env_key: str, default: str = "") -> str:
    value = get_config(config_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    env_value = os.getenv(env_key, "").strip()
    return env_value or default


def _config_int(config_key: str, env_key: str, default: int) -> int:
    value = get_config(config_key)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            pass
    env_value = os.getenv(env_key, "").strip()
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    return default


def get_media_storage_type() -> str:
    storage_type = _config_str("media.storage_type", "MEDIA_STORAGE_TYPE", "local").lower()
    return "r2" if storage_type == "r2" else "local"


def get_media_cache_max_bytes() -> int:
    mb = _config_int("media.cache_max_mb", "MEDIA_CACHE_MAX_MB", 20)
    return max(0, mb) * 1024 * 1024


class BaseMediaStorage:
    async def read_bytes(self, media_type: str, name: str) -> Optional[bytes]:
        raise NotImplementedError

    async def write_bytes(
        self,
        media_type: str,
        name: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> None:
        raise NotImplementedError


class LocalMediaStorage(BaseMediaStorage):
    def __init__(self):
        self.base_dir = DATA_DIR / "tmp"
        self.image_dir = self.base_dir / "image"
        self.video_dir = self.base_dir / "video"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def _dir(self, media_type: str) -> Path:
        return self.image_dir if media_type == "image" else self.video_dir

    def _path(self, media_type: str, name: str) -> Path:
        safe_name = name.replace("/", "-")
        return self._dir(media_type) / safe_name

    async def read_bytes(self, media_type: str, name: str) -> Optional[bytes]:
        path = self._path(media_type, name)
        if not path.exists() or not path.is_file():
            return None
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def write_bytes(
        self,
        media_type: str,
        name: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> None:
        path = self._path(media_type, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        async with aiofiles.open(tmp_path, "wb") as f:
            await f.write(data)
        os.replace(tmp_path, path)


class R2MediaStorage(BaseMediaStorage):
    def __init__(self):
        endpoint = _config_str("media.r2_endpoint", "R2_ENDPOINT")
        account_id = _config_str("media.r2_account_id", "R2_ACCOUNT_ID")
        self.bucket = _config_str("media.r2_bucket", "R2_BUCKET")
        self.access_key_id = _config_str("media.r2_access_key_id", "R2_ACCESS_KEY_ID")
        self.secret_access_key = _config_str(
            "media.r2_secret_access_key", "R2_SECRET_ACCESS_KEY"
        )
        self.region = _config_str("media.r2_region", "R2_REGION", "auto")
        self.key_prefix = _config_str("media.r2_key_prefix", "R2_KEY_PREFIX", "tmp").strip(
            "/"
        )

        if not endpoint and account_id:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

        missing = []
        if not endpoint:
            missing.append("R2_ENDPOINT (or R2_ACCOUNT_ID)")
        if not self.bucket:
            missing.append("R2_BUCKET")
        if not self.access_key_id:
            missing.append("R2_ACCESS_KEY_ID")
        if not self.secret_access_key:
            missing.append("R2_SECRET_ACCESS_KEY")
        if missing:
            raise RuntimeError(
                "R2 storage is enabled but missing required configuration: "
                + ", ".join(missing)
            )

        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                "R2 storage requires boto3. Install dependency: boto3>=1.35.0"
            ) from exc

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def _key(self, media_type: str, name: str) -> str:
        safe_name = name.replace("/", "-")
        return f"{self.key_prefix}/{media_type}/{safe_name}"

    async def read_bytes(self, media_type: str, name: str) -> Optional[bytes]:
        key = self._key(media_type, name)

        def _read() -> Optional[bytes]:
            try:
                obj = self._client.get_object(Bucket=self.bucket, Key=key)
                return obj["Body"].read()
            except Exception as e:
                code = (
                    getattr(e, "response", {})
                    .get("Error", {})
                    .get("Code", "")
                    .strip()
                    .lower()
                )
                if code in {"nosuchkey", "404", "notfound"}:
                    return None
                raise

        return await asyncio.to_thread(_read)

    async def write_bytes(
        self,
        media_type: str,
        name: str,
        data: bytes,
        content_type: Optional[str] = None,
    ) -> None:
        key = self._key(media_type, name)

        def _write() -> None:
            kwargs = {"Bucket": self.bucket, "Key": key, "Body": data}
            if content_type:
                kwargs["ContentType"] = content_type
            self._client.put_object(**kwargs)

        await asyncio.to_thread(_write)


_storage: Optional[BaseMediaStorage] = None
_storage_type: Optional[str] = None


def get_media_storage() -> BaseMediaStorage:
    global _storage, _storage_type
    current_type = get_media_storage_type()

    if _storage is not None and _storage_type == current_type:
        return _storage

    if current_type == "r2":
        logger.info("Media storage: using Cloudflare R2 backend")
        _storage = R2MediaStorage()
    else:
        logger.info("Media storage: using local backend")
        _storage = LocalMediaStorage()

    _storage_type = current_type
    return _storage

