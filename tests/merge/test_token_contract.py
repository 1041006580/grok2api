import asyncio
from types import SimpleNamespace
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


def test_nsfw_batch_requires_proxy_or_cf_clearance_before_accept_tos():
    from app.core.config import config
    from app.services.grok.batch_services.nsfw import NSFWService

    config._config = {
        "nsfw": {"batch_size": 1, "concurrent": 1},
        "proxy": {
            "base_proxy_url": "",
            "reverse_base_url": "",
            "cf_clearance": "",
            "cf_cookies": "",
            "browser": "chrome136",
        },
    }

    mgr = AsyncMock()
    mgr.record_fail = AsyncMock()
    mgr.add_tag = AsyncMock()

    with patch(
        "app.services.reverse.accept_tos.AcceptTosReverse.request",
        new_callable=AsyncMock,
        side_effect=AssertionError("AcceptTosReverse should not run without proxy/cf_clearance"),
    ) as accept_tos:
        results = asyncio.run(NSFWService.batch(["sso=test-token"], mgr))

    result = results["sso=test-token"]

    accept_tos.assert_not_awaited()
    assert result["ok"] is True
    assert result["data"]["success"] is False
    assert result["data"]["http_status"] == 400
    assert "cf_clearance" in result["data"]["error"]


def test_nsfw_batch_matches_browser_flow_without_accept_tos():
    from app.core.config import config
    from app.services.grok.batch_services.nsfw import NSFWService
    from app.services.reverse.utils.grpc import GrpcStatus

    config._config = {
        "nsfw": {"batch_size": 1, "concurrent": 1, "timeout": 60},
        "proxy": {
            "base_proxy_url": "",
            "reverse_base_url": "",
            "cf_clearance": "cf-token",
            "cf_cookies": "",
            "browser": "chrome136",
            "user_agent": "Mozilla/5.0",
        },
        "retry": {
            "max_retry": 3,
            "retry_status_codes": [401, 429, 403],
            "retry_budget": 60,
            "retry_backoff_base": 0.5,
            "retry_backoff_factor": 2.0,
            "retry_backoff_max": 20.0,
        },
    }

    mgr = AsyncMock()
    mgr.record_fail = AsyncMock()
    mgr.add_tag = AsyncMock()

    with patch(
        "app.services.reverse.accept_tos.AcceptTosReverse.request",
        new_callable=AsyncMock,
        side_effect=AssertionError(
            "AcceptTosReverse should not run when matching browser NSFW flow"
        ),
    ) as accept_tos:
        with patch(
            "app.services.grok.batch_services.nsfw.SetBirthReverse.request",
            new_callable=AsyncMock,
        ) as set_birth:
            with patch(
                "app.services.grok.batch_services.nsfw.NsfwMgmtReverse.request",
                new_callable=AsyncMock,
                return_value=GrpcStatus(code=0, message=""),
            ) as nsfw_mgmt:
                results = asyncio.run(NSFWService.batch(["sso=test-token"], mgr))

    result = results["sso=test-token"]

    accept_tos.assert_not_awaited()
    set_birth.assert_awaited_once()
    nsfw_mgmt.assert_awaited_once()
    mgr.add_tag.assert_awaited_once_with("sso=test-token", "nsfw")
    assert result["ok"] is True
    assert result["data"]["success"] is True
    assert result["data"]["http_status"] == 200


def test_nsfw_batch_uses_token_note_as_extra_cookie_context():
    from app.core.config import config
    from app.services.grok.batch_services.nsfw import NSFWService
    from app.services.reverse.utils.grpc import GrpcStatus
    from app.services.token.models import TokenInfo
    from app.services.token.pool import TokenPool

    config._config = {
        "nsfw": {"batch_size": 1, "concurrent": 1, "timeout": 60},
        "proxy": {
            "base_proxy_url": "",
            "reverse_base_url": "",
            "cf_clearance": "cf-token",
            "cf_cookies": "",
            "browser": "chrome136",
            "user_agent": "Mozilla/5.0",
        },
        "retry": {
            "max_retry": 3,
            "retry_status_codes": [401, 429, 403],
            "retry_budget": 60,
            "retry_backoff_base": 0.5,
            "retry_backoff_factor": 2.0,
            "retry_backoff_max": 20.0,
        },
    }

    pool = TokenPool("ssoSuper")
    pool.add(TokenInfo(token="test-token", quota=10, note="x-userid=user-1"))
    mgr = SimpleNamespace(
        pools={"ssoSuper": pool},
        record_fail=AsyncMock(),
        add_tag=AsyncMock(),
    )

    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = AsyncMock()
    session_ctx.__aexit__.return_value = None

    set_birth_calls = []
    nsfw_calls = []

    async def fake_set_birth(session, token, extra_cookies=None):
        set_birth_calls.append(extra_cookies)

    async def fake_nsfw_mgmt(session, token, extra_cookies=None):
        nsfw_calls.append(extra_cookies)
        return GrpcStatus(code=0, message="")

    with patch(
        "app.services.grok.batch_services.nsfw.ResettableSession",
        autospec=True,
        return_value=session_ctx,
    ):
        with patch(
            "app.services.grok.batch_services.nsfw.SetBirthReverse.request",
            new=AsyncMock(side_effect=fake_set_birth),
        ):
            with patch(
                "app.services.grok.batch_services.nsfw.NsfwMgmtReverse.request",
                new=AsyncMock(side_effect=fake_nsfw_mgmt),
            ):
                results = asyncio.run(NSFWService.batch(["test-token"], mgr))

    assert results["test-token"]["data"]["success"] is True
    assert set_birth_calls == ["x-userid=user-1"]
    assert nsfw_calls == ["x-userid=user-1"]


def test_nsfw_batch_fetches_x_userid_cookie_when_note_missing():
    from app.core.config import config
    from app.services.grok.batch_services.nsfw import NSFWService
    from app.services.reverse.utils.grpc import GrpcStatus
    from app.services.token.models import TokenInfo
    from app.services.token.pool import TokenPool

    config._config = {
        "nsfw": {"batch_size": 1, "concurrent": 1, "timeout": 60},
        "proxy": {
            "base_proxy_url": "",
            "reverse_base_url": "",
            "cf_clearance": "cf-token",
            "cf_cookies": "",
            "browser": "chrome136",
            "user_agent": "Mozilla/5.0",
        },
        "retry": {
            "max_retry": 3,
            "retry_status_codes": [401, 429, 403],
            "retry_budget": 60,
            "retry_backoff_base": 0.5,
            "retry_backoff_factor": 2.0,
            "retry_backoff_max": 20.0,
        },
    }

    pool = TokenPool("ssoSuper")
    pool.add(TokenInfo(token="test-token", quota=10, note=""))
    mgr = SimpleNamespace(
        pools={"ssoSuper": pool},
        record_fail=AsyncMock(),
        add_tag=AsyncMock(),
    )

    fake_session = AsyncMock()
    fake_session.get.return_value = SimpleNamespace(
        status_code=200,
        headers={"set-cookie": "x-userid=user-42; Path=/; Domain=.grok.com; Secure; SameSite=none"},
    )
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = fake_session
    session_ctx.__aexit__.return_value = None

    set_birth_calls = []
    nsfw_calls = []

    async def fake_set_birth(session, token, extra_cookies=None):
        set_birth_calls.append(extra_cookies)

    async def fake_nsfw_mgmt(session, token, extra_cookies=None):
        nsfw_calls.append(extra_cookies)
        return GrpcStatus(code=0, message="")

    with patch(
        "app.services.grok.batch_services.nsfw.ResettableSession",
        autospec=True,
        return_value=session_ctx,
    ):
        with patch(
            "app.services.grok.batch_services.nsfw.SetBirthReverse.request",
            new=AsyncMock(side_effect=fake_set_birth),
        ):
            with patch(
                "app.services.grok.batch_services.nsfw.NsfwMgmtReverse.request",
                new=AsyncMock(side_effect=fake_nsfw_mgmt),
            ):
                results = asyncio.run(NSFWService.batch(["test-token"], mgr))

    assert results["test-token"]["data"]["success"] is True
    assert set_birth_calls == ["x-userid=user-42"]
    assert nsfw_calls == ["x-userid=user-42"]
    assert pool.get("test-token").note == "x-userid=user-42"


def test_set_birth_reverse_uses_browser_adult_payload():
    from app.core.config import config
    from app.services.reverse.set_birth import SetBirthReverse

    captured = {}

    class DummyResponse:
        status_code = 200

    async def fake_post(url, *, headers, json, timeout, proxies, impersonate):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        captured["proxies"] = proxies
        captured["impersonate"] = impersonate
        return DummyResponse()

    session = AsyncMock()
    session.post.side_effect = fake_post

    config._config = {
        "nsfw": {"timeout": 60},
        "proxy": {
            "base_proxy_url": "",
            "browser": "chrome136",
            "user_agent": "Mozilla/5.0",
            "cf_clearance": "cf-token",
            "cf_cookies": "",
        },
        "retry": {
            "max_retry": 3,
            "retry_status_codes": [401, 429, 403],
            "retry_budget": 60,
            "retry_backoff_base": 0.5,
            "retry_backoff_factor": 2.0,
            "retry_backoff_max": 20.0,
        },
    }

    asyncio.run(SetBirthReverse.request(session, "sso=test-token"))

    assert captured["url"] == "https://grok.com/rest/auth/set-birth-date"
    assert captured["headers"]["Referer"] == "https://grok.com/?_s=data"
    assert captured["json"] == {"birthDate": "2001-01-01T16:00:00.000Z"}


def test_set_birth_reverse_includes_extra_cookies_when_provided():
    from app.core.config import config
    from app.services.reverse.set_birth import SetBirthReverse

    captured = {}

    class DummyResponse:
        status_code = 200

    async def fake_post(url, *, headers, json, timeout, proxies, impersonate):
        captured["headers"] = headers
        return DummyResponse()

    session = AsyncMock()
    session.post.side_effect = fake_post

    config._config = {
        "nsfw": {"timeout": 60},
        "proxy": {
            "base_proxy_url": "",
            "browser": "chrome136",
            "user_agent": "Mozilla/5.0",
            "cf_clearance": "cf-token",
            "cf_cookies": "",
        },
        "retry": {
            "max_retry": 3,
            "retry_status_codes": [401, 429, 403],
            "retry_budget": 60,
            "retry_backoff_base": 0.5,
            "retry_backoff_factor": 2.0,
            "retry_backoff_max": 20.0,
        },
    }

    asyncio.run(
        SetBirthReverse.request(
            session,
            "sso=test-token",
            extra_cookies="x-userid=user-99",
        )
    )

    assert "x-userid=user-99" in captured["headers"]["Cookie"]
