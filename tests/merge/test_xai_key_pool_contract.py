from pathlib import Path

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


def test_config_defaults_exposes_xai_keys_pool():
    config_text = Path("config.defaults.toml").read_text(encoding="utf-8")
    xai_section = config_text.split("[xai]", 1)[1].split("[voice]", 1)[0]
    assert "keys = []" in config_text
    assert "api_key =" not in xai_section
