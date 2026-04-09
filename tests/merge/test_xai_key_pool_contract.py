import asyncio
import shutil
import tomllib
import uuid
from pathlib import Path

from app.core import storage as core_storage
from app.core.exceptions import UpstreamException
from app.core.storage import LocalStorage
from app.services.grok.services.xai_key_manager import XAIKeyManager
from app.services.grok.services.xai_video import XAIVideoService


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


def test_local_storage_roundtrip_preserves_request_key_bindings(monkeypatch):
    tmp_dir = core_storage.DATA_DIR / f"tmp-config-{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "config.toml"
    monkeypatch.setattr(core_storage, "CONFIG_FILE", config_path)

    storage = LocalStorage()
    payload = {
        "xai": {
            "keys": [
                {"id": "k1", "key": "xai-key-1", "enabled": True},
            ],
            "request_key_bindings": {
                "vidreq_123": {"key_id": "k1", "key": "xai-key-1"},
            },
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


def test_xai_video_service_builds_headers_from_manager_key():
    fake_key = type("KeyRef", (), {"key": "xai-key-1"})()
    fake_manager = type("Mgr", (), {"acquire_key": staticmethod(lambda: fake_key)})()
    service = XAIVideoService(key_manager=fake_manager)
    headers = service._headers()
    assert headers["Authorization"] == "Bearer xai-key-1"


def test_app_xai_keys_page_reuses_token_layout_shell():
    html = Path("app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")

    assert '/static/admin/css/token.css' in html
    assert 'text-2xl font-semibold tracking-tight' in html
    assert 'id="loading"' in html
    assert 'id="empty-state"' in html
    assert 'modal-overlay hidden' in html
    assert 'modal-content modal-md' in html


def test_public_xai_keys_page_reuses_token_layout_shell():
    html = Path("_public/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")

    assert '/static/admin/css/token.css' in html
    assert 'text-2xl font-semibold tracking-tight' in html
    assert 'id="loading"' in html
    assert 'id="empty-state"' in html
    assert 'modal-overlay hidden' in html
    assert 'modal-content modal-md' in html


def test_xai_video_service_start_generation_falls_back_to_next_key_on_retryable_error():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-key-1", "enabled": True, "status": "active"},
                    {"id": "k2", "key": "xai-key-2", "enabled": True, "status": "active"},
                ]
            }
        }
    )
    service = XAIVideoService(key_manager=manager)
    attempted_key_ids = []

    async def fake_request_json(self, session, method, url, payload=None, **kwargs):
        key_record = kwargs.get("key_record")
        if key_record is None:
            self._headers()
            key_record = self._key_record
        attempted_key_ids.append(key_record.id)
        if key_record.id == "k1":
            raise UpstreamException(
                message="xAI video API request failed with status 429",
                details={"status": 429, "body": "rate limited"},
            )
        return {"request_id": "vidreq_123", "status": "pending"}

    original = XAIVideoService._request_json
    XAIVideoService._request_json = fake_request_json
    try:
        result = asyncio.run(
            service.start_generation(
                prompt="launch a rocket",
                model="grok-imagine-video",
                duration=10,
                aspect_ratio="16:9",
                resolution="720p",
            )
        )
    finally:
        XAIVideoService._request_json = original

    assert result["request_id"] == "vidreq_123"
    assert attempted_key_ids == ["k1", "k2"]
    assert service._key_record is not None
    assert service._key_record.id == "k2"


def test_xai_video_service_get_generation_retries_on_same_key_without_switching():
    manager = XAIKeyManager.from_config(
        {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-key-1", "enabled": True, "status": "active"},
                    {"id": "k2", "key": "xai-key-2", "enabled": True, "status": "active"},
                ]
            }
        }
    )
    bound_key = manager.list_keys()[0]
    service = XAIVideoService(key_manager=manager, key_record=bound_key)
    attempted_key_ids = []
    attempts = {"count": 0}

    async def fake_request_json(self, session, method, url, payload=None, **kwargs):
        key_record = kwargs.get("key_record") or self._key_record
        attempted_key_ids.append(key_record.id)
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise UpstreamException(
                message="xAI video API request failed with status 429",
                details={"status": 429, "body": "poll rate limited"},
            )
        return {
            "request_id": "vidreq_123",
            "status": "done",
            "video": {"url": "https://example.com/video.mp4"},
        }

    original = XAIVideoService._request_json
    XAIVideoService._request_json = fake_request_json
    try:
        result = asyncio.run(service.get_generation("vidreq_123"))
    finally:
        XAIVideoService._request_json = original

    assert result["status"] == "done"
    assert attempted_key_ids == ["k1", "k1", "k1"]
