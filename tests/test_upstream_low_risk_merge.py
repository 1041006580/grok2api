import pathlib
import unittest
from unittest.mock import AsyncMock, patch
import orjson
from types import SimpleNamespace

from app.services.reverse.app_chat import AppChatReverse
from app.services.reverse.utils.headers import build_sso_cookie
from app.services.reverse.utils.session import ResettableSession
from app.services.token.manager import TokenManager
from app.services.token.models import TokenInfo, TokenStatus
from app.services.token.pool import TokenPool


ROOT = pathlib.Path(__file__).resolve().parents[1]


class CustomInstructionTests(unittest.TestCase):
    def test_app_chat_payload_includes_custom_personality_when_configured(self):
        def fake_get_config(key, default=None):
            values = {
                "app.disable_memory": True,
                "app.temporary": True,
                "app.custom_instruction": "  Stay concise and factual.  ",
            }
            return values.get(key, default)

        with patch("app.services.reverse.app_chat.get_config", side_effect=fake_get_config):
            payload = AppChatReverse.build_payload(
                message="hello",
                model="grok-3",
            )

        self.assertEqual(payload["customPersonality"], "Stay concise and factual.")

    def test_app_chat_payload_omits_custom_personality_when_empty(self):
        def fake_get_config(key, default=None):
            values = {
                "app.disable_memory": True,
                "app.temporary": True,
                "app.custom_instruction": "   ",
            }
            return values.get(key, default)

        with patch("app.services.reverse.app_chat.get_config", side_effect=fake_get_config):
            payload = AppChatReverse.build_payload(
                message="hello",
                model="grok-3",
            )

        self.assertNotIn("customPersonality", payload)


class SanitizationTests(unittest.TestCase):
    def test_token_info_normalizes_copied_token_text(self):
        info = TokenInfo(token="  sso=\u200babc\u2010def\u00a0123  ")
        self.assertEqual(info.token, "abc-def123")

    def test_proxy_config_payload_sanitizes_cf_fields(self):
        from app.api.v1.admin_api.config import _sanitize_proxy_config_payload

        payload = {
            "proxy": {
                "user_agent": "  Mozilla/5.0\u200b  ",
                "cf_cookies": "  cf_clearance=ab\u200bc; foo=bar  ",
                "cf_clearance": "  ab\u200bc  ",
            }
        }

        sanitized = _sanitize_proxy_config_payload(payload)

        self.assertEqual(sanitized["proxy"]["user_agent"], "Mozilla/5.0")
        self.assertEqual(sanitized["proxy"]["cf_cookies"], "cf_clearance=abc; foo=bar")
        self.assertEqual(sanitized["proxy"]["cf_clearance"], "abc")


class StaticAssetTests(unittest.TestCase):
    def test_token_admin_page_contains_batch_enable_disable_buttons(self):
        html = (ROOT / "app/static/admin/pages/token.html").read_text(encoding="utf-8")
        self.assertIn('id="btn-batch-disable"', html)
        self.assertIn('id="btn-batch-enable"', html)

    def test_token_admin_script_contains_enable_disable_actions(self):
        js = (ROOT / "app/static/admin/js/token.js").read_text(encoding="utf-8")
        self.assertIn("async function toggleTokenEnabled(", js)
        self.assertIn("async function batchDisableTokens()", js)
        self.assertIn("async function batchEnableTokens()", js)

    def test_admin_config_script_mentions_custom_instruction(self):
        js = (ROOT / "app/static/admin/js/config.js").read_text(encoding="utf-8")
        self.assertIn('"custom_instruction"', js)

    def test_admin_config_script_mentions_cf_cookie_fields(self):
        js = (ROOT / "app/static/admin/js/config.js").read_text(encoding="utf-8")
        self.assertIn('"cf_cookies"', js)
        self.assertIn('"skip_proxy_ssl_verify"', js)


class ComposeConfigTests(unittest.TestCase):
    def test_docker_compose_supports_env_passthrough_for_ports_and_storage_url(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('${HOST_PORT:-8000}:${SERVER_PORT:-8000}', compose)
        self.assertIn('SERVER_STORAGE_URL: ${SERVER_STORAGE_URL:-}', compose)


class ProxyConfigTests(unittest.TestCase):
    def test_config_defaults_exposes_cf_cookie_and_proxy_ssl_toggle(self):
        config_text = (ROOT / "config.defaults.toml").read_text(encoding="utf-8")
        self.assertIn('cf_cookies = ""', config_text)
        self.assertIn('skip_proxy_ssl_verify = false', config_text)

    def test_build_sso_cookie_prefers_cf_cookies_when_present(self):
        def fake_get_config(key, default=None):
            values = {
                "proxy.cf_cookies": "cf_clearance=abc; foo=bar",
                "proxy.cf_clearance": "xyz",
                "proxy.enabled": True,
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.headers.get_config", side_effect=fake_get_config):
            cookie = build_sso_cookie("sso=test-token")

        self.assertIn("sso=test-token", cookie)
        self.assertIn("cf_clearance=abc; foo=bar", cookie)
        self.assertNotIn("cf_clearance=xyz", cookie)

    def test_build_sso_cookie_sanitizes_and_overrides_cf_clearance_when_manual_mode(self):
        def fake_get_config(key, default=None):
            values = {
                "proxy.cf_cookies": "foo=bar; cf_clearance=ol\u200bd",
                "proxy.cf_clearance": " ne\u200bw ",
                "proxy.enabled": False,
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.headers.get_config", side_effect=fake_get_config):
            cookie = build_sso_cookie("sso=to\u200bken")

        self.assertIn("sso=token", cookie)
        self.assertIn("foo=bar; cf_clearance=new", cookie)
        self.assertNotIn("old", cookie)

    def test_build_headers_sanitizes_user_agent_and_origin(self):
        from app.services.reverse.utils.headers import build_headers

        def fake_get_config(key, default=None):
            values = {
                "proxy.user_agent": "  Mozilla/5.0\u200b  ",
                "proxy.browser": "chrome136",
                "proxy.cf_cookies": "",
                "proxy.cf_clearance": "",
                "proxy.enabled": False,
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.headers.get_config", side_effect=fake_get_config):
            headers = build_headers(
                "sso=test",
                content_type="application/json",
                origin=" https://grok.com\u200b ",
                referer=" https://grok.com/\u200b ",
            )

        self.assertEqual(headers["User-Agent"], "Mozilla/5.0")
        self.assertEqual(headers["Origin"], "https://grok.com")
        self.assertEqual(headers["Referer"], "https://grok.com/")


class ResettableSessionTests(unittest.TestCase):
    def test_session_uses_reset_status_codes_from_config(self):
        created_kwargs = {}

        class DummySession:
            def __init__(self, **kwargs):
                created_kwargs.update(kwargs)

        def fake_get_config(key, default=None):
            values = {
                "proxy.browser": "",
                "retry.reset_session_status_codes": [403, 429, 502],
                "proxy.skip_proxy_ssl_verify": False,
                "proxy.base_proxy_url": "",
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.session.AsyncSession", DummySession):
            with patch("app.services.reverse.utils.session.get_config", side_effect=fake_get_config):
                session = ResettableSession()

        self.assertEqual(session._reset_on_status, {403, 429, 502})
        self.assertEqual(created_kwargs, {})

    def test_session_adds_proxy_ssl_bypass_only_when_enabled_with_proxy(self):
        created_kwargs = {}

        class DummySession:
            def __init__(self, **kwargs):
                created_kwargs.update(kwargs)

        def fake_get_config(key, default=None):
            values = {
                "proxy.browser": "",
                "retry.reset_session_status_codes": [403],
                "proxy.skip_proxy_ssl_verify": True,
                "proxy.base_proxy_url": "https://proxy.example.com:8443",
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.session.AsyncSession", DummySession):
            with patch("app.services.reverse.utils.session.get_config", side_effect=fake_get_config):
                ResettableSession()

        curl_options = created_kwargs.get("curl_options")
        self.assertIsInstance(curl_options, dict)
        self.assertTrue(curl_options)


class VideosApiTests(unittest.IsolatedAsyncioTestCase):
    def test_app_registers_videos_route(self):
        from main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/v1/videos", paths)

    async def test_videos_route_rejects_invalid_model(self):
        from app.api.v1.video import create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {
                    "model": "grok-3",
                    "prompt": "test prompt",
                }

        with self.assertRaises(Exception) as ctx:
            await create_video(FakeRequest())

        exc = ctx.exception
        self.assertEqual(getattr(exc, "status_code", None), 400)
        self.assertEqual(getattr(exc, "code", None), "model_not_supported")

    async def test_videos_route_returns_openai_compatible_payload(self):
        from app.api.v1.video import create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {
                    "model": "grok-imagine-1.0-video",
                    "prompt": "rainy cyberpunk street",
                    "size": "1792x1024",
                    "seconds": 18,
                    "quality": "standard",
                }

        class DummyModelInfo:
            is_video = True

        with patch("app.api.v1.video.ModelService.get", return_value=DummyModelInfo()):
            with patch(
                "app.api.v1.video.VideoService.completions",
                new=AsyncMock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": "https://example.com/generated.mp4"
                                }
                            }
                        ]
                    }
                ),
            ):
                response = await create_video(FakeRequest())

        self.assertEqual(response.status_code, 200)
        body = orjson.loads(response.body)
        self.assertEqual(body["object"], "video")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["model"], "grok-imagine-1.0-video")
        self.assertEqual(body["seconds"], "18")
        self.assertEqual(body["url"], "https://example.com/generated.mp4")


class VideoAutoExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_completions_auto_extends_long_non_stream_video(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "low"

        class DummyTier:
            value = "basic"

        class DummyModelInfo:
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoBasic",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )

        collect_results = [
            {
                "choices": [
                    {
                        "message": {
                            "content": "https://assets.grok.com/generated/round-one/generated_video.mp4"
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "https://assets.grok.com/generated/round-two/generated_video.mp4"
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": "https://assets.grok.com/generated/round-three/generated_video.mp4"
                        }
                    }
                ]
            },
        ]

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoBasic"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch("app.services.grok.services.video.get_config", side_effect=lambda key, default=None: {"retry.max_retry": 3, "app.stream": False, "app.thinking": True}.get(key, default)):
                        with patch.object(VideoService, "generate", new=AsyncMock(return_value="round-1-stream")) as mock_generate:
                            with patch.object(VideoService, "generate_extension", new=AsyncMock(side_effect=["round-2-stream", "round-3-stream"])) as mock_extend:
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=collect_results)):
                                    result = await VideoService.completions(
                                        model="grok-imagine-1.0-video",
                                        messages=[{"role": "user", "content": "make a long clip"}],
                                        stream=False,
                                        aspect_ratio="3:2",
                                        video_length=18,
                                        resolution="480p",
                                        preset="custom",
                                    )

        self.assertEqual(mock_generate.await_count, 1)
        self.assertEqual(mock_extend.await_count, 2)
        self.assertEqual(
            result["choices"][0]["message"]["content"],
            "https://assets.grok.com/generated/round-three/generated_video.mp4",
        )


class AuthFailureDetectionTests(unittest.TestCase):
    def test_explicit_auth_error_is_recognized(self):
        from app.core.exceptions import UpstreamException
        from app.services.grok.utils.retry import explicit_auth_failure

        err = UpstreamException(
            "auth failed",
            details={
                "status": 401,
                "body": '{"error":"unauthenticated"}',
            },
        )

        self.assertTrue(explicit_auth_failure(err))

    def test_cloudflare_or_html_401_is_not_recognized_as_auth_failure(self):
        from app.core.exceptions import UpstreamException
        from app.services.grok.utils.retry import explicit_auth_failure

        err = UpstreamException(
            "blocked",
            details={
                "status": 401,
                "body": "<html>challenge-platform cloudflare</html>",
            },
        )

        self.assertFalse(explicit_auth_failure(err))

    def test_non_401_is_not_recognized_as_auth_failure(self):
        from app.core.exceptions import UpstreamException
        from app.services.grok.utils.retry import explicit_auth_failure

        err = UpstreamException(
            "forbidden",
            details={
                "status": 403,
                "body": '{"error":"unauthenticated"}',
            },
        )

        self.assertFalse(explicit_auth_failure(err))


class TokenRefreshBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = TokenManager()
        pool = TokenPool("ssoBasic")
        pool.add(TokenInfo(token="token-1", quota=10))
        self.manager.pools = {"ssoBasic": pool}
        self.manager.initialized = True
        self.manager._schedule_save = lambda: None
        self.manager._save = AsyncMock()

    async def test_sync_usage_skips_local_fallback_for_confirmed_auth_failure(self):
        from app.core.exceptions import UpstreamException

        token = self.manager.pools["ssoBasic"].get("token-1")
        self.assertIsNotNone(token)

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(
                side_effect=UpstreamException(
                    "auth failed",
                    details={"status": 401, "body": '{"error":"unauthenticated"}'},
                )
            ),
        ):
            self.manager.consume = AsyncMock(return_value=True)
            result = await self.manager.sync_usage("token-1", consume_on_fail=True)

        self.assertFalse(result)
        self.manager.consume.assert_not_awaited()
        self.assertEqual(token.fail_count, 1)

    async def test_refresh_cooling_token_does_not_expire_on_unconfirmed_401(self):
        from app.core.exceptions import UpstreamException

        token = self.manager.pools["ssoBasic"].get("token-1")
        token.status = TokenStatus.COOLING
        token.quota = 0

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(
                side_effect=UpstreamException(
                    "blocked",
                    details={"status": 401, "body": "<html>challenge-platform</html>"},
                )
            ),
        ):
            with patch("app.services.token.manager.get_config", side_effect=lambda key, default=None: default):
                result = await self.manager.refresh_cooling_tokens()

        self.assertEqual(result["expired"], 0)
        self.assertEqual(token.status, TokenStatus.COOLING)

    async def test_refresh_cooling_token_expires_on_confirmed_auth_failure(self):
        from app.core.exceptions import UpstreamException

        token = self.manager.pools["ssoBasic"].get("token-1")
        token.status = TokenStatus.COOLING
        token.quota = 0

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(
                side_effect=UpstreamException(
                    "auth failed",
                    details={"status": 401, "body": '{"error":"bad-credentials"}'},
                )
            ),
        ):
            with patch("app.services.token.manager.get_config", side_effect=lambda key, default=None: default):
                result = await self.manager.refresh_cooling_tokens()

        self.assertEqual(result["expired"], 1)
        self.assertEqual(token.status, TokenStatus.EXPIRED)


if __name__ == "__main__":
    unittest.main()
