import asyncio
from unittest.mock import AsyncMock, patch

import orjson


def test_proxy_pool_helpers_exist():
    from app.core.proxy_pool import (
        build_http_proxies,
        get_current_proxy_from,
        rotate_proxy,
        should_rotate_proxy,
    )

    assert callable(build_http_proxies)
    assert callable(get_current_proxy_from)
    assert callable(rotate_proxy)
    assert callable(should_rotate_proxy)


def test_app_chat_payload_enables_420_flag():
    from app.services.reverse.app_chat import AppChatReverse

    with patch("app.services.reverse.app_chat.get_config", return_value=True):
        payload = AppChatReverse.build_payload(
            message="hello",
            model="grok-420",
        )

    assert payload["enable420"] is True


def test_rate_limits_request_uses_proxy_pool_and_supported_default_model():
    from app.services.reverse.rate_limits import RateLimitsReverse

    captured = {}

    class DummyResponse:
        status_code = 200
        text = ""

    class DummySession:
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return DummyResponse()

    async def passthrough(func, *args, **kwargs):
        return await func()

    def fake_get_config(key, default=None):
        values = {
            "usage.timeout": 30,
            "proxy.browser": "",
            "proxy.base_proxy_url": "",
        }
        return values.get(key, default)

    with patch(
        "app.services.reverse.rate_limits.get_current_proxy_from",
        return_value=("proxy.base_proxy_url", "http://proxy.example:8080"),
        create=True,
    ):
        with patch(
            "app.services.reverse.rate_limits.build_http_proxies",
            return_value={
                "http": "http://proxy.example:8080",
                "https": "http://proxy.example:8080",
            },
            create=True,
        ):
            with patch(
                "app.services.reverse.rate_limits.build_headers",
                return_value={"Cookie": "sso=test"},
            ):
                with patch(
                    "app.services.reverse.rate_limits.get_config",
                    side_effect=fake_get_config,
                ):
                    with patch(
                        "app.services.reverse.rate_limits.retry_on_status",
                        side_effect=passthrough,
                    ):
                        asyncio.run(
                            RateLimitsReverse.request(
                                DummySession(),
                                "sso=test",
                            )
                        )

    assert orjson.loads(captured["kwargs"]["data"])["modelName"] == "grok-4-1-thinking-1129"
    assert captured["kwargs"]["proxies"] == {
        "http": "http://proxy.example:8080",
        "https": "http://proxy.example:8080",
    }
