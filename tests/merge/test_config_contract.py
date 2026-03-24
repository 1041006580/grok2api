import asyncio
from unittest.mock import AsyncMock

from app.core.config import Config


def test_local_default_config_contract_is_preserved():
    cfg = Config()
    cfg._ensure_defaults()
    cfg._config = cfg._defaults.copy()

    assert cfg.get("app.stream") is True
    assert cfg.get("app.public_enabled") is False
    assert cfg.get("app.video_format") == "html"
    assert cfg.get("token.auto_refresh") is True
    assert cfg.get("proxy.base_proxy_url") == ""


def test_config_defaults_expose_current_local_sections():
    cfg = Config()
    cfg._ensure_defaults()

    assert "app" in cfg._defaults
    assert "proxy" in cfg._defaults
    assert "token" in cfg._defaults
    assert "video" in cfg._defaults


def test_config_ensure_loaded_is_available_and_idempotent():
    cfg = Config()
    cfg._loaded = False

    async def fake_load():
        cfg._loaded = True

    cfg.load = AsyncMock(side_effect=fake_load)

    asyncio.run(cfg.ensure_loaded())
    asyncio.run(cfg.ensure_loaded())

    cfg.load.assert_awaited_once()


def test_function_auth_aliases_exist():
    from app.core.auth import (
        get_function_api_key,
        is_function_enabled,
        verify_function_key,
    )

    assert callable(get_function_api_key)
    assert callable(is_function_enabled)
    assert callable(verify_function_key)
