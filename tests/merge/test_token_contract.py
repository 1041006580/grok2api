import asyncio
from unittest.mock import AsyncMock, patch


def test_usage_service_defaults_to_supported_rate_limit_model():
    from app.services.grok.batch_services.usage import UsageService

    captured = {}

    class DummyResponse:
        def json(self):
            return {"remainingTokens": 12}

    async def fake_request(session, token, model_name=""):
        captured["model_name"] = model_name
        return DummyResponse()

    with patch(
        "app.services.grok.batch_services.usage.ResettableSession",
        autospec=True,
    ) as session_cls:
        session_ctx = AsyncMock()
        session_ctx.__aenter__.return_value = object()
        session_ctx.__aexit__.return_value = None
        session_cls.return_value = session_ctx
        with patch(
            "app.services.grok.batch_services.usage.RateLimitsReverse.request",
            side_effect=fake_request,
        ):
            asyncio.run(UsageService().get("sso=test"))

    assert captured["model_name"] == "grok-4-1-thinking-1129"
