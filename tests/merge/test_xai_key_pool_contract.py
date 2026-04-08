import asyncio
import shutil
import tomllib
import uuid
from pathlib import Path

from app.core import storage as core_storage
from app.core.storage import LocalStorage
from app.services.grok.services.xai_key_manager import XAIKeyManager


def test_xai_key_manager_loads_from_xai_keys_config():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-key-1", "name": "key-1", "enabled": True},
                    {"id": "k2", "key": "xai-key-2", "name": "key-2", "enabled": False},
                ]
            }
        }
    )

    items = manager.list_keys()
    assert [item.id for item in items] == ["k1", "k2"]
    assert items[0].enabled is True
    assert items[1].enabled is False


def test_xai_key_manager_selects_only_enabled_keys():
    manager = XAIKeyManager.from_config(
        {"xai": {"keys": [{"id": "k1", "key": "xai-key-1", "enabled": False}]}}
    )
    assert manager.acquire_key() is None


def test_xai_key_manager_treats_string_false_as_disabled():
    manager = XAIKeyManager.from_config(
        {"xai": {"keys": [{"id": "k1", "key": "xai-key-1", "enabled": "false"}]}}
    )
    assert manager.list_keys()[0].enabled is False
    assert manager.acquire_key() is None


def test_xai_key_manager_does_not_select_unknown_status_keys():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {
                        "id": "k1",
                        "key": "xai-key-1",
                        "enabled": True,
                        "status": "cooldown",
                    }
                ]
            }
        }
    )

    items = manager.list_keys()
    assert items[0].status == "cooldown"
    assert manager.acquire_key() is None


def test_config_defaults_exposes_xai_keys_pool():
    with Path("config.defaults.toml").open("rb") as f:
        config = tomllib.load(f)

    assert config["xai"]["keys"] == []
    assert "api_key" not in config["xai"]


def test_local_storage_roundtrip_preserves_xai_keys(monkeypatch):
    tmp_dir = core_storage.DATA_DIR / f"tmp-config-{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "config.toml"
    monkeypatch.setattr(core_storage, "CONFIG_FILE", config_path)

    storage = LocalStorage()
    payload = {
        "xai": {
            "keys": [
                {"id": "k1", "key": "xai-key-1", "name": "key-1", "enabled": True},
                {"id": "k2", "key": "xai-key-2", "name": "key-2", "enabled": False},
            ]
        }
    }

    async def roundtrip():
        await storage.save_config(payload)
        return await storage.load_config()

    try:
        loaded = asyncio.run(roundtrip())
        assert loaded == payload
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_local_storage_roundtrip_preserves_none_metadata(monkeypatch):
    tmp_dir = core_storage.DATA_DIR / f"tmp-config-{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "config.toml"
    monkeypatch.setattr(core_storage, "CONFIG_FILE", config_path)

    storage = LocalStorage()
    payload = {
        "xai": {
            "keys": [
                {
                    "id": "k1",
                    "key": "xai-key-1",
                    "enabled": True,
                    "last_error": None,
                    "blocked_until": None,
                }
            ]
        }
    }

    async def roundtrip():
        await storage.save_config(payload)
        return await storage.load_config()

    try:
        loaded = asyncio.run(roundtrip())
        assert loaded == payload
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_local_storage_roundtrip_preserves_control_characters(monkeypatch):
    tmp_dir = core_storage.DATA_DIR / f"tmp-config-{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "config.toml"
    monkeypatch.setattr(core_storage, "CONFIG_FILE", config_path)

    storage = LocalStorage()
    payload = {
        "xai": {
            "keys": [
                {
                    "id": "k1",
                    "key": "xai-key-1",
                    "enabled": True,
                    "name": 'qa "primary" \\ node',
                    "last_error": "line1\r\nline2",
                }
            ]
        }
    }

    async def roundtrip():
        await storage.save_config(payload)
        return await storage.load_config()

    try:
        loaded = asyncio.run(roundtrip())
        assert loaded == payload
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_xai_key_manager_preserves_runtime_metadata_fields():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {
                        "id": "k1",
                        "key": "xai-key-1",
                        "enabled": True,
                        "status": "blocked",
                        "last_error": "rate limited",
                        "blocked_until": 12345,
                        "last_used_at": 67890,
                    }
                ]
            }
        }
    )

    item = manager.list_keys()[0]
    assert item.status == "blocked"
    assert item.last_error == "rate limited"
    assert item.blocked_until == 12345
    assert item.last_used_at == 67890
