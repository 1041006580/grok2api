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
