import asyncio
import copy
import pathlib
import unittest
from unittest.mock import AsyncMock, Mock, patch
import orjson
from types import SimpleNamespace
import json
from contextlib import asynccontextmanager

from app.core.exceptions import UpstreamException
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

    def test_app_chat_video_payload_matches_captured_browser_shape(self):
        def fake_get_config(key, default=None):
            values = {
                "app.temporary": True,
                "app.custom_instruction": "Should not be injected into video payload",
            }
            return values.get(key, default)

        with patch("app.services.reverse.app_chat.get_config", side_effect=fake_get_config):
            payload = AppChatReverse.build_video_payload(
                message="A golden retriever running on a beach at sunset --mode=custom",
                model="grok-3",
                tool_overrides={"videoGen": True},
                model_config_override={
                    "modelMap": {
                        "videoGenModelConfig": {
                            "parentPostId": "pid-1",
                            "aspectRatio": "16:9",
                            "videoLength": 6,
                            "resolutionName": "480p",
                        }
                    }
                },
            )

        self.assertEqual(
            payload,
            {
                "temporary": True,
                "modelName": "grok-3",
                "message": "A golden retriever running on a beach at sunset --mode=custom",
                "toolOverrides": {"videoGen": True},
                "enableSideBySide": True,
                "responseMetadata": {
                    "experiments": [],
                    "modelConfigOverride": {
                        "modelMap": {
                            "videoGenModelConfig": {
                                "parentPostId": "pid-1",
                                "aspectRatio": "16:9",
                                "videoLength": 6,
                                "resolutionName": "480p",
                            }
                        }
                    },
                },
            },
        )


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

    def test_build_sso_cookie_appends_explicit_extra_cookies(self):
        def fake_get_config(key, default=None):
            values = {
                "proxy.cf_cookies": "",
                "proxy.cf_clearance": "",
                "proxy.enabled": False,
            }
            return values.get(key, default)

        with patch("app.services.reverse.utils.headers.get_config", side_effect=fake_get_config):
            cookie = build_sso_cookie("sso=abc123", extra_cookies="x-userid=user-1")

        self.assertEqual(cookie, "sso=abc123; sso-rw=abc123; x-userid=user-1")


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

    def test_xai_keys_admin_page_exposes_table_and_create_action(self):
        html = (ROOT / "app/static/admin/pages/xai-keys.html").read_text(encoding="utf-8")
        js = (ROOT / "app/static/admin/js/xai-keys.js").read_text(encoding="utf-8")
        self.assertIn('id="xai-keys-table-body"', html)
        self.assertIn("async function saveXAIKey()", js)


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
        self.assertIn('cf_refresh_target_url = ""', config_text)

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


class CfRefreshTargetUrlTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_config_script_mentions_cf_refresh_target_url(self):
        js = (ROOT / "app/static/admin/js/config.js").read_text(encoding="utf-8")
        self.assertIn('"cf_refresh_target_url"', js)

    def test_cf_refresh_target_url_defaults_to_grok(self):
        from app.services.cf_refresh.config import get_target_url

        with patch("app.services.cf_refresh.config._get", side_effect=lambda key, default=None: ""):
            self.assertEqual(get_target_url(), "https://grok.com")

    async def test_solver_uses_explicit_cf_refresh_target_url(self):
        from app.services.cf_refresh.solver import solve_cf_challenge

        captured_payload = {}

        class DummyResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "status": "ok",
                        "solution": {
                            "cookies": [{"name": "cf_clearance", "value": "abc"}],
                            "userAgent": "Mozilla/5.0 Chrome/142.0.0.0",
                        },
                    }
                ).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_payload.update(json.loads(req.data.decode("utf-8")))
            return DummyResponse()

        async def passthrough(func):
            return func()

        with patch("app.services.cf_refresh.solver.get_flaresolverr_url", return_value="http://flaresolverr:8191"):
            with patch("app.services.cf_refresh.solver.get_timeout", return_value=60):
                with patch("app.services.cf_refresh.solver.get_proxy", return_value=""):
                    with patch("app.services.cf_refresh.solver.GROK_URL", "https://grok.com"):
                        with patch("app.services.cf_refresh.solver.get_target_url", return_value="https://proxy.example.com/grok"):
                            with patch("app.services.cf_refresh.solver.urllib_request.urlopen", side_effect=fake_urlopen):
                                with patch("app.services.cf_refresh.solver.asyncio.to_thread", side_effect=passthrough):
                                    result = await solve_cf_challenge()

        self.assertEqual(captured_payload["url"], "https://proxy.example.com/grok")
        self.assertEqual(result["cf_clearance"], "abc")


class CfRefreshControlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_config_notifies_cf_refresh_scheduler(self):
        from app.api.v1.admin_api.config import update_config

        with patch("app.api.v1.admin_api.config.config.update", new=AsyncMock()) as mock_update:
            with patch("app.api.v1.admin_api.config.notify_config_changed", new=AsyncMock()) as mock_notify:
                result = await update_config({"proxy": {"enabled": True}})

        self.assertEqual(result["status"], "success")
        mock_update.assert_awaited_once()
        mock_notify.assert_awaited_once()

    async def test_manual_cf_refresh_endpoint_calls_scheduler(self):
        from app.api.v1.admin_api.config import trigger_cf_refresh

        with patch("app.api.v1.admin_api.config.request_manual_refresh", new=AsyncMock(return_value=True)) as mock_refresh:
            result = await trigger_cf_refresh()

        self.assertEqual(result["status"], "success")
        mock_refresh.assert_awaited_once()

    def test_config_page_contains_manual_cf_refresh_button(self):
        html = (ROOT / "app/static/admin/pages/config.html").read_text(encoding="utf-8")
        self.assertIn('id="cf-refresh-btn"', html)

    def test_config_script_contains_manual_cf_refresh_handler(self):
        js = (ROOT / "app/static/admin/js/config.js").read_text(encoding="utf-8")
        self.assertIn("async function triggerCfRefresh()", js)


class VideoPageModelRulesTests(unittest.TestCase):
    def test_function_video_page_mentions_xai_duration_limit(self):
        html = (ROOT / "_public/static/function/pages/video.html").read_text(encoding="utf-8")
        self.assertIn("1-15s", html)
        self.assertIn("单张参考图", html)

    def test_public_video_page_mentions_xai_duration_limit(self):
        html = (ROOT / "app/static/public/pages/video.html").read_text(encoding="utf-8")
        self.assertIn("1-15s", html)
        self.assertIn("单张参考图", html)
        self.assertIn("xAI API", html)


class FunctionVideoPageModelSwitchTests(unittest.TestCase):
    def test_function_video_page_has_selector_state_markers(self):
        html = (ROOT / "_public/static/function/pages/video.html").read_text(encoding="utf-8")
        js = (ROOT / "_public/static/function/js/video.js").read_text(encoding="utf-8")

        self.assertIn('id="modelSelect"', html)
        self.assertIn("const LEGACY_VIDEO_MODEL_IDS = [", js)
        self.assertIn("const XAI_VIDEO_MODEL_ID = 'grok-imagine-video'", js)
        self.assertIn("const XAI_MAX_DURATION_SECONDS = 15", js)
        self.assertIn("modelSelect.addEventListener('change'", js)


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

    async def test_videos_route_requires_available_xai_key_pool(self):
        from app.api.v1 import video as video_module
        create_video = video_module.create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {"model": "grok-imagine-video", "prompt": "test"}

        fake_manager = SimpleNamespace(acquire_key=lambda: None)
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with self.assertRaises(Exception) as ctx:
                await create_video(FakeRequest())

        self.assertEqual(getattr(ctx.exception, "code", None), "xai_api_key_missing")

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

    async def test_videos_route_supports_xai_api_key_generation_model(self):
        from app.api.v1 import video as video_module
        create_video = video_module.create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {
                    "model": "grok-imagine-video",
                    "prompt": "launch a rocket over mars",
                    "size": "1280x720",
                    "seconds": 10,
                    "quality": "high",
                }

        fake_key = SimpleNamespace(key="xai-test-key")
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            captured_kwargs = []

            class FakeXAIVideoService:
                generate = AsyncMock(
                    return_value={
                        "url": "https://example.com/xai-generated.mp4",
                        "duration": 10,
                        "model": "grok-imagine-video",
                    }
                )

                def __init__(self, *args, **kwargs):
                    captured_kwargs.append(kwargs)

            with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                mock_generate = FakeXAIVideoService.generate
                response = await create_video(FakeRequest())

        self.assertEqual(response.status_code, 200)
        body = orjson.loads(response.body)
        self.assertEqual(body["model"], "grok-imagine-video")
        self.assertEqual(body["seconds"], "10")
        self.assertEqual(body["quality"], "high")
        self.assertEqual(body["url"], "https://example.com/xai-generated.mp4")
        self.assertIs(captured_kwargs[0]["key_manager"], fake_manager)
        self.assertIs(captured_kwargs[0]["key_record"], fake_key)
        mock_generate.assert_awaited_once_with(
            prompt="launch a rocket over mars",
            model="grok-imagine-video",
            duration=10,
            aspect_ratio="16:9",
            resolution="720p",
            image_url=None,
        )

    async def test_videos_route_passes_image_reference_to_xai_api_generation(self):
        from app.api.v1 import video as video_module
        create_video = video_module.create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {
                    "model": "grok-imagine-video",
                    "prompt": "animate the still image into a calm timelapse",
                    "image_reference": {
                        "image_url": "https://example.com/still.png",
                    },
                    "seconds": 12,
                }

        fake_key = SimpleNamespace(key="xai-test-key")
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            captured_kwargs = []

            class FakeXAIVideoService:
                generate = AsyncMock(
                    return_value={
                        "url": "https://example.com/xai-image-video.mp4",
                        "duration": 12,
                        "model": "grok-imagine-video",
                    }
                )

                def __init__(self, *args, **kwargs):
                    captured_kwargs.append(kwargs)

            with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                mock_generate = FakeXAIVideoService.generate
                response = await create_video(FakeRequest())

        self.assertEqual(response.status_code, 200)
        body = orjson.loads(response.body)
        self.assertEqual(body["url"], "https://example.com/xai-image-video.mp4")
        self.assertIs(captured_kwargs[0]["key_manager"], fake_manager)
        self.assertIs(captured_kwargs[0]["key_record"], fake_key)
        mock_generate.assert_awaited_once_with(
            prompt="animate the still image into a calm timelapse",
            model="grok-imagine-video",
            duration=12,
            aspect_ratio="3:2",
            resolution="480p",
            image_url="https://example.com/still.png",
        )

    async def test_videos_route_accepts_five_second_xai_generation(self):
        from app.api.v1 import video as video_module
        create_video = video_module.create_video

        class FakeRequest:
            headers = {"content-type": "application/json"}

            async def json(self):
                return {
                    "model": "grok-imagine-video",
                    "prompt": "a five second cinematic wave",
                    "seconds": 5,
                }

        fake_key = SimpleNamespace(key="xai-test-key")
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            captured_kwargs = []

            class FakeXAIVideoService:
                generate = AsyncMock(
                    return_value={
                        "url": "https://example.com/xai-5s.mp4",
                        "duration": 5,
                        "model": "grok-imagine-video",
                    }
                )

                def __init__(self, *args, **kwargs):
                    captured_kwargs.append(kwargs)

            with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                response = await create_video(FakeRequest())

        self.assertEqual(response.status_code, 200)
        body = orjson.loads(response.body)
        self.assertEqual(body["seconds"], "5")
        self.assertIs(captured_kwargs[0]["key_manager"], fake_manager)
        self.assertIs(captured_kwargs[0]["key_record"], fake_key)


class ChatVideoValidationTests(unittest.TestCase):
    def test_chat_video_non_stream_accepts_18_seconds(self):
        from app.api.v1.chat import ChatCompletionRequest, validate_request

        request = ChatCompletionRequest(
            model="grok-imagine-1.0-video",
            messages=[{"role": "user", "content": "make a longer clip"}],
            stream=False,
            video_config={"video_length": 18},
        )

        validate_request(request)
        self.assertEqual(request.video_config.video_length, 18)

    def test_chat_video_stream_rejects_18_seconds(self):
        from app.api.v1.chat import ChatCompletionRequest, validate_request

        request = ChatCompletionRequest(
            model="grok-imagine-1.0-video",
            messages=[{"role": "user", "content": "make a longer clip"}],
            stream=True,
            video_config={"video_length": 18},
        )

        with self.assertRaises(Exception) as ctx:
            validate_request(request)

        exc = ctx.exception
        self.assertEqual(getattr(exc, "status_code", None), 400)
        self.assertEqual(getattr(exc, "code", None), "invalid_video_length")
        self.assertEqual(
            getattr(exc, "message", ""),
            "Streaming video_length must be 6, 10, or 15 seconds",
        )


class VideoAutoExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def _run_video_completion_case(
        self,
        *,
        pool_name: str,
        target_length: int,
        tier_value: str,
        expected_extension_calls: int,
    ):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "low"

        class DummyTier:
            value = tier_value

        class DummyModelInfo:
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: pool_name,
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
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoBasic", "ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch("app.services.grok.services.video.get_config", side_effect=lambda key, default=None: {"retry.max_retry": 3, "app.stream": False, "app.thinking": True, "video.concurrent": 1}.get(key, default)):
                        with patch.object(VideoService, "generate", new=AsyncMock(return_value="round-1-stream")) as mock_generate:
                            with patch.object(VideoService, "generate_extension", new=AsyncMock(side_effect=["round-2-stream", "round-3-stream"])) as mock_extend:
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=collect_results)):
                                    result = await VideoService.completions(
                                        model="grok-imagine-1.0-video",
                                        messages=[{"role": "user", "content": "make a clip"}],
                                        stream=False,
                                        aspect_ratio="3:2",
                                        video_length=target_length,
                                        resolution="480p",
                                        preset="custom",
                                    )

        self.assertEqual(mock_generate.await_count, 1)
        self.assertEqual(mock_extend.await_count, expected_extension_calls)
        return result

    async def test_video_completions_auto_extends_long_non_stream_video(self):
        result = await self._run_video_completion_case(
            pool_name="ssoBasic",
            target_length=18,
            tier_value="basic",
            expected_extension_calls=2,
        )
        self.assertEqual(
            result["choices"][0]["message"]["content"],
            "https://assets.grok.com/generated/round-three/generated_video.mp4",
        )

    async def test_basic_token_extends_for_10_seconds(self):
        await self._run_video_completion_case(
            pool_name="ssoBasic",
            target_length=10,
            tier_value="basic",
            expected_extension_calls=1,
        )

    async def test_basic_token_extends_for_15_seconds(self):
        await self._run_video_completion_case(
            pool_name="ssoBasic",
            target_length=15,
            tier_value="basic",
            expected_extension_calls=2,
        )

    async def test_super_token_does_not_extend_for_15_seconds(self):
        await self._run_video_completion_case(
            pool_name="ssoSuper",
            target_length=15,
            tier_value="super",
            expected_extension_calls=0,
        )

    async def test_video_super_defaults_to_15_seconds(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "high"

        class DummyTier:
            value = "super"

        class DummyModelInfo:
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoSuper",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch("app.services.grok.services.video.get_config", side_effect=lambda key, default=None: {"retry.max_retry": 3, "app.stream": False, "app.thinking": True}.get(key, default)):
                        with patch.object(VideoService, "generate", new=AsyncMock(return_value="round-1-stream")) as mock_generate:
                            with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(return_value={
                                "choices": [{"message": {"content": "https://assets.grok.com/generated/final/generated_video.mp4"}}]
                            })):
                                await VideoService.completions(
                                    model="grok-imagine-1.0-video-super",
                                    messages=[{"role": "user", "content": "make a super clip"}],
                                    stream=False,
                                    aspect_ratio="16:9",
                                    video_length=6,
                                    resolution="480p",
                                    preset="custom",
                                )

        called = mock_generate.await_args
        self.assertIsNotNone(called)
        self.assertEqual(called.args[3], 15)

    async def test_video_super_falls_back_to_10_second_rounds_when_upstream_rejects_15_seconds(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "high"

        class DummyTier:
            value = "super"

        class DummyModelInfo:
            grok_model = "grok-3"
            model_mode = "MODEL_MODE_FAST"
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoSuper",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )

        generate_lengths = []
        extension_calls = []
        collect_results = [
            {
                "choices": [{"message": {"content": "round 1"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-one-post/generated_video.mp4",
                "post_id": "round-one-post",
            },
            {
                "choices": [{"message": {"content": "round 2"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-two-post/generated_video.mp4",
                "post_id": "round-two-post",
            },
        ]

        async def fake_generate(
            self,
            token,
            prompt,
            aspect_ratio="3:2",
            video_length=6,
            resolution_name="480p",
            preset="normal",
            grok_model="grok-3",
            model_mode=None,
            extra_cookies=None,
        ):
            generate_lengths.append(video_length)
            if video_length == 15:
                raise UpstreamException(
                    message="AppChatReverse: Chat failed, 400",
                    details={
                        "status": 400,
                        "body": (
                            '{"error":{"code":3,"message":"Video duration must be between 1 and 10 seconds, got 15","details":[]}}'
                        ),
                    },
                )
            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_generate_extension(
            self,
            token,
            prompt,
            parent_post_id,
            original_post_id,
            start_time,
            aspect_ratio="3:2",
            video_length=6,
            resolution_name="480p",
            preset="normal",
            grok_model="grok-3",
            model_mode=None,
            extra_cookies=None,
        ):
            extension_calls.append(
                {
                    "parent_post_id": parent_post_id,
                    "original_post_id": original_post_id,
                    "start_time": start_time,
                    "video_length": video_length,
                }
            )
            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_collect(stream):
            async for _ in stream:
                pass
            return collect_results.pop(0)

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch(
                        "app.services.grok.services.video.get_config",
                        side_effect=lambda key, default=None: {
                            "retry.max_retry": 3,
                            "app.stream": False,
                            "app.thinking": True,
                            "video.concurrent": 1,
                        }.get(key, default),
                    ):
                        with patch.object(VideoService, "generate", new=fake_generate):
                            with patch.object(VideoService, "generate_extension", new=fake_generate_extension):
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=fake_collect)):
                                    result = await VideoService.completions(
                                        model="grok-imagine-1.0-video-super",
                                        messages=[{"role": "user", "content": "make a 15 second clip"}],
                                        stream=False,
                                        aspect_ratio="16:9",
                                        video_length=15,
                                        resolution="720p",
                                        preset="custom",
                                    )

        self.assertEqual(generate_lengths, [15, 10])
        self.assertEqual(
            extension_calls,
            [
                {
                    "parent_post_id": "round-one-post",
                    "original_post_id": "round-one-post",
                    "start_time": 5.0,
                    "video_length": 10,
                }
            ],
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "round 2")
        fake_mgr.consume.assert_awaited_once()

    async def test_video_super_falls_back_to_10_second_rounds_when_upstream_returns_empty_400_body(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "high"

        class DummyTier:
            value = "super"

        class DummyModelInfo:
            grok_model = "grok-3"
            model_mode = "MODEL_MODE_FAST"
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoSuper",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )

        generate_lengths = []
        extension_calls = []
        collect_results = [
            {
                "choices": [{"message": {"content": "round 1"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-one-post/generated_video.mp4",
                "post_id": "round-one-post",
            },
            {
                "choices": [{"message": {"content": "round 2"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-two-post/generated_video.mp4",
                "post_id": "round-two-post",
            },
        ]

        async def fake_generate(
            self,
            token,
            prompt,
            aspect_ratio="3:2",
            video_length=6,
            resolution_name="480p",
            preset="normal",
            grok_model="grok-3",
            model_mode=None,
            extra_cookies=None,
        ):
            generate_lengths.append(video_length)
            if video_length == 15:
                raise UpstreamException(
                    message="AppChatReverse: Chat failed, 400",
                    details={"status": 400, "body": ""},
                )

            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_generate_extension(
            self,
            token,
            prompt,
            parent_post_id,
            original_post_id,
            start_time,
            aspect_ratio="3:2",
            video_length=6,
            resolution_name="480p",
            preset="normal",
            grok_model="grok-3",
            model_mode=None,
            extra_cookies=None,
        ):
            extension_calls.append(
                {
                    "parent_post_id": parent_post_id,
                    "original_post_id": original_post_id,
                    "start_time": start_time,
                    "video_length": video_length,
                }
            )

            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_collect(stream):
            async for _ in stream:
                pass
            return collect_results.pop(0)

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch(
                        "app.services.grok.services.video.get_config",
                        side_effect=lambda key, default=None: {
                            "retry.max_retry": 3,
                            "app.stream": False,
                            "app.thinking": True,
                            "video.concurrent": 1,
                        }.get(key, default),
                    ):
                        with patch.object(VideoService, "generate", new=fake_generate):
                            with patch.object(VideoService, "generate_extension", new=fake_generate_extension):
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=fake_collect)):
                                    result = await VideoService.completions(
                                        model="grok-imagine-1.0-video-super",
                                        messages=[{"role": "user", "content": "make a 15 second clip"}],
                                        stream=False,
                                        aspect_ratio="16:9",
                                        video_length=15,
                                        resolution="720p",
                                        preset="custom",
                                    )

        self.assertEqual(generate_lengths, [15, 10])
        self.assertEqual(
            extension_calls,
            [
                {
                    "parent_post_id": "round-one-post",
                    "original_post_id": "round-one-post",
                    "start_time": 5.0,
                    "video_length": 10,
                }
            ],
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "round 2")
        fake_mgr.consume.assert_awaited_once()

    async def test_video_super_uses_browser_like_payload_for_initial_and_extension_requests(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "high"

        class DummyTier:
            value = "super"

        class DummyModelInfo:
            grok_model = "grok-3"
            model_mode = "MODEL_MODE_FAST"
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10)
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoSuper",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )
        captured_requests = []
        collect_results = [
            {
                "choices": [{"message": {"content": "round 1"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-one-post/generated_video.mp4",
                "post_id": "round-one-post",
            },
            {
                "choices": [{"message": {"content": "round 2"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-two-post/generated_video.mp4",
                "post_id": "round-two-post",
            },
        ]

        async def fake_request(session, token, message, model, mode=None, **kwargs):
            captured_requests.append(
                {
                    "model": model,
                    "mode": mode,
                    "payload_override": kwargs.get("payload_override"),
                    "referer_override": kwargs.get("referer_override"),
                }
            )

            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_collect(stream):
            async for _ in stream:
                pass
            return collect_results.pop(0)

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch(
                        "app.services.grok.services.video.get_config",
                        side_effect=lambda key, default=None: {
                            "retry.max_retry": 3,
                            "app.stream": False,
                            "app.thinking": True,
                            "video.concurrent": 1,
                        }.get(key, default),
                    ):
                        with patch.object(VideoService, "create_post", new=AsyncMock(return_value="root-post")):
                            with patch("app.services.grok.services.video.AppChatReverse.request", new=AsyncMock(side_effect=fake_request)):
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=fake_collect)):
                                    await VideoService.completions(
                                        model="grok-imagine-1.0-video-super",
                                        messages=[{"role": "user", "content": "make an 18 second clip"}],
                                        stream=False,
                                        aspect_ratio="16:9",
                                        video_length=18,
                                        resolution="720p",
                                        preset="custom",
                                    )

        self.assertEqual(
            captured_requests,
            [
                {
                    "model": "grok-3",
                    "mode": None,
                    "referer_override": "https://grok.com/imagine",
                    "payload_override": {
                        "temporary": True,
                        "modelName": "grok-3",
                        "message": "make an 18 second clip --mode=custom",
                        "toolOverrides": {"videoGen": True},
                        "enableSideBySide": True,
                        "responseMetadata": {
                            "experiments": [],
                            "modelConfigOverride": {
                                "modelMap": {
                                    "videoGenModelConfig": {
                                        "aspectRatio": "16:9",
                                        "parentPostId": "root-post",
                                        "resolutionName": "720p",
                                        "videoLength": 15,
                                    }
                                }
                            },
                        },
                    },
                },
                {
                    "model": "grok-3",
                    "mode": None,
                    "referer_override": "https://grok.com/imagine",
                    "payload_override": {
                        "temporary": True,
                        "modelName": "grok-3",
                        "message": "make an 18 second clip --mode=custom",
                        "toolOverrides": {"videoGen": True},
                        "enableSideBySide": True,
                        "responseMetadata": {
                            "experiments": [],
                            "modelConfigOverride": {
                                "modelMap": {
                                    "videoGenModelConfig": {
                                        "isVideoExtension": True,
                                        "videoExtensionStartTime": 3.0,
                                        "extendPostId": "round-one-post",
                                        "stitchWithExtendPostId": True,
                                        "originalPrompt": "make an 18 second clip",
                                        "originalPostId": "round-one-post",
                                        "originalRefType": "ORIGINAL_REF_TYPE_VIDEO_EXTENSION",
                                        "mode": "custom",
                                        "aspectRatio": "16:9",
                                        "videoLength": 15,
                                        "resolutionName": "720p",
                                        "parentPostId": "round-one-post",
                                        "isVideoEdit": False,
                                    }
                                }
                            },
                        },
                    },
                },
            ],
        )

    async def test_video_auto_extension_uses_raw_video_metadata_for_post_id(self):
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
                "choices": [{"message": {"content": "rendered html without post id"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-one-post/generated_video.mp4",
                "post_id": "round-one-post",
            },
            {
                "choices": [{"message": {"content": "final rendered html"}}],
                "raw_video_url": "https://assets.grok.com/users/u/round-two-post/generated_video.mp4",
                "post_id": "round-two-post",
            },
        ]

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoBasic", "ssoSuper"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch("app.services.grok.services.video.get_config", side_effect=lambda key, default=None: {"retry.max_retry": 3, "app.stream": False, "app.thinking": True}.get(key, default)):
                        with patch.object(VideoService, "generate", new=AsyncMock(return_value="round-1-stream")):
                            with patch.object(VideoService, "generate_extension", new=AsyncMock(return_value="round-2-stream")) as mock_extend:
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=collect_results)):
                                    result = await VideoService.completions(
                                        model="grok-imagine-1.0-video",
                                        messages=[{"role": "user", "content": "make a 10 second clip"}],
                                        stream=False,
                                        aspect_ratio="3:2",
                                        video_length=10,
                                        resolution="480p",
                                        preset="custom",
                                    )

        self.assertEqual(mock_extend.await_count, 1)
        self.assertEqual(
            result["choices"][0]["message"]["content"],
            "final rendered html",
        )

    async def test_video_request_uses_token_note_as_extra_cookie_context(self):
        from app.services.grok.services.video import VideoService

        class DummyCost:
            value = "high"

        class DummyTier:
            value = "basic"

        class DummyModelInfo:
            grok_model = "grok-3"
            model_mode = "MODEL_MODE_FAST"
            cost = DummyCost()
            tier = DummyTier()

        token_info = TokenInfo(token="token-1", quota=10, note="x-userid=user-1")
        fake_mgr = SimpleNamespace(
            get_token_for_video=lambda **kwargs: token_info,
            get_pool_name_for_token=lambda token: "ssoBasic",
            consume=AsyncMock(return_value=True),
            mark_rate_limited=AsyncMock(return_value=True),
            reload_if_stale=AsyncMock(return_value=None),
        )
        media_post_calls = []
        app_chat_calls = []

        class DummyMediaResponse:
            def json(self):
                return {"post": {"id": "root-post"}}

        async def fake_media_post(
            session,
            token,
            mediaType,
            mediaUrl,
            prompt="",
            extra_cookies=None,
            referer_override=None,
        ):
            media_post_calls.append(
                {
                    "token": token,
                    "mediaType": mediaType,
                    "mediaUrl": mediaUrl,
                    "prompt": prompt,
                    "extra_cookies": extra_cookies,
                    "referer_override": referer_override,
                }
            )
            return DummyMediaResponse()

        async def fake_app_chat(session, token, message, model, mode=None, **kwargs):
            app_chat_calls.append(
                {
                    "token": token,
                    "message": message,
                    "model": model,
                    "mode": mode,
                    "extra_cookies": kwargs.get("extra_cookies"),
                    "referer_override": kwargs.get("referer_override"),
                }
            )

            async def _stream():
                yield "data: stub"

            return _stream()

        async def fake_collect(stream):
            async for _ in stream:
                pass
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch("app.services.grok.services.video.get_token_manager", new=AsyncMock(return_value=fake_mgr)):
            with patch("app.services.grok.services.video.ModelService.pool_candidates_for_model", return_value=["ssoBasic"]):
                with patch("app.services.grok.services.video.ModelService.get", return_value=DummyModelInfo()):
                    with patch(
                        "app.services.grok.services.video.get_config",
                        side_effect=lambda key, default=None: {
                            "retry.max_retry": 3,
                            "app.stream": False,
                            "app.thinking": True,
                            "video.concurrent": 1,
                        }.get(key, default),
                    ):
                        with patch("app.services.grok.services.video.MediaPostReverse.request", new=AsyncMock(side_effect=fake_media_post)):
                            with patch("app.services.grok.services.video.AppChatReverse.request", new=AsyncMock(side_effect=fake_app_chat)):
                                with patch("app.services.grok.services.video.VideoCollectProcessor.process", new=AsyncMock(side_effect=fake_collect)):
                                    await VideoService.completions(
                                        model="grok-imagine-1.0-video",
                                        messages=[{"role": "user", "content": "make a clip"}],
                                        stream=False,
                                        aspect_ratio="16:9",
                                        video_length=6,
                                        resolution="480p",
                                        preset="custom",
                                    )

        self.assertEqual(
            media_post_calls,
            [
                {
                    "token": "token-1",
                    "mediaType": "MEDIA_POST_TYPE_VIDEO",
                    "mediaUrl": "",
                    "prompt": "make a clip",
                    "extra_cookies": "x-userid=user-1",
                    "referer_override": "https://grok.com/imagine",
                }
            ],
        )
        self.assertEqual(
            app_chat_calls,
            [
                {
                    "token": "token-1",
                    "message": "make a clip --mode=custom",
                    "model": "grok-3",
                    "mode": None,
                    "extra_cookies": "x-userid=user-1",
                    "referer_override": "https://grok.com/imagine",
                }
            ],
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

    async def test_sync_usage_keeps_basic_pool_when_rate_limit_window_matches_basic(self):
        token = self.manager.pools["ssoBasic"].get("token-1")
        self.assertIsNotNone(token)

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(
                return_value={"remainingQueries": 12, "windowSizeSeconds": 72000}
            ),
        ):
            result = await self.manager.sync_usage("token-1", consume_on_fail=False)

        self.assertTrue(result)
        kept = self.manager.pools["ssoBasic"].get("token-1")
        self.assertIsNotNone(kept)
        self.assertIsNone(self.manager.pools.get("ssoSuper", TokenPool("ssoSuper")).get("token-1"))
        self.assertEqual(kept.quota, 12)


class TokenTierDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_auto_detects_super_tokens_from_short_window(self):
        self.manager = TokenManager()
        self.manager.pools = {}
        self.manager.initialized = True
        self.manager._schedule_save = lambda: None
        self.manager._save = AsyncMock()

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(
                return_value={"remainingQueries": 12, "windowSizeSeconds": 7200}
            ),
        ):
            added = await self.manager.add("token-super", pool_name="ssoBasic")

        self.assertTrue(added)
        self.assertIsNone(self.manager.pools.get("ssoBasic", TokenPool("ssoBasic")).get("token-super"))
        token = self.manager.pools["ssoSuper"].get("token-super")
        self.assertIsNotNone(token)
        self.assertEqual(token.quota, 12)

    async def test_add_keeps_basic_pool_when_only_remaining_queries_are_present(self):
        self.manager = TokenManager()
        self.manager.pools = {}
        self.manager.initialized = True
        self.manager._schedule_save = lambda: None
        self.manager._save = AsyncMock()

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(return_value={"remainingQueries": 12}),
        ):
            added = await self.manager.add("token-basic", pool_name="ssoBasic")

        self.assertTrue(added)
        token = self.manager.pools["ssoBasic"].get("token-basic")
        self.assertIsNotNone(token)
        self.assertEqual(token.quota, 12)

    async def test_add_falls_back_to_requested_pool_when_detection_fails(self):
        self.manager = TokenManager()
        self.manager.pools = {}
        self.manager.initialized = True
        self.manager._schedule_save = lambda: None
        self.manager._save = AsyncMock()

        with patch(
            "app.services.token.manager.UsageService.get",
            new=AsyncMock(side_effect=UpstreamException("boom", details={"status": 502})),
        ):
            added = await self.manager.add("token-basic", pool_name="ssoBasic")

        self.assertTrue(added)
        token = self.manager.pools["ssoBasic"].get("token-basic")
        self.assertIsNotNone(token)
        self.assertEqual(token.quota, 80)


class XAIKeysAdminApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_xai_keys_get_returns_masked_keys(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                yield

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", {"xai": {"keys": []}}, create=True):
                with patch.object(module, "get_storage", return_value=DummyStorage()):
                    response = await module.get_xai_keys()

        self.assertIn("keys", response)
        self.assertEqual(response["keys"][0]["id"], "k1")
        self.assertEqual(response["keys"][0]["name"], "primary")
        self.assertTrue(response["keys"][0]["enabled"])
        self.assertEqual(response["keys"][0]["value"], "xai-****5678")

    async def test_admin_xai_keys_patch_can_toggle_enabled(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        payload = await module.update_xai_key("k1", {"enabled": False})

        self.assertEqual(payload["status"], "success")
        self.assertFalse(state["xai"]["keys"][0]["enabled"])

    async def test_admin_xai_keys_patch_can_update_name(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        payload = await module.update_xai_key("k1", {"name": "secondary"})

        self.assertEqual(payload["status"], "success")
        self.assertEqual(state["xai"]["keys"][0]["name"], "secondary")

    async def test_admin_xai_keys_create_and_delete_roundtrip(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {"xai": {"keys": []}}
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        created = await module.create_xai_key(
                            {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                        )
                        deleted = await module.delete_xai_key("k1")

        self.assertEqual(created["status"], "success")
        self.assertEqual(created["key"]["value"], "xai-****5678")
        self.assertEqual(deleted["status"], "success")
        self.assertEqual(state["xai"]["keys"], [])

    async def test_admin_xai_keys_rejects_invalid_create_payload_types(self):
        from app.api.v1.admin_api import xai_keys as module

        with self.assertRaises(Exception) as ctx:
            await module.create_xai_key({"key": {"bad": 1}, "enabled": 0})

        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

        with self.assertRaises(Exception) as ctx_unknown:
            await module.create_xai_key({"key": "xai-secret-12345678", "enbaled": False})

        self.assertEqual(getattr(ctx_unknown.exception, "status_code", None), 400)

        with self.assertRaises(Exception) as ctx_none:
            await module.create_xai_key({"key": "xai-secret-12345678", "enabled": None})

        self.assertEqual(getattr(ctx_none.exception, "status_code", None), 400)

    async def test_admin_xai_keys_rejects_invalid_update_payload_types(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        with self.assertRaises(Exception) as ctx:
                            await module.update_xai_key("k1", {"name": {"bad": 1}})

        self.assertEqual(getattr(ctx.exception, "status_code", None), 400)

    async def test_admin_xai_keys_get_prefers_latest_persisted_state(self):
        from app.api.v1.admin_api import xai_keys as module

        persisted_state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        stale_local_state = {"xai": {"keys": []}}

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                yield

            async def load_config(self):
                return copy.deepcopy(persisted_state)

        with patch.object(module.config, "_config", stale_local_state, create=True):
            with patch.object(module.config, "_defaults", {"xai": {"keys": []}}, create=True):
                with patch.object(module, "get_storage", return_value=DummyStorage()):
                    payload = await module.get_xai_keys()

        self.assertEqual(payload["keys"][0]["id"], "k1")

    async def test_admin_xai_keys_rejects_empty_or_unknown_patch_payload(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        with self.assertRaises(Exception) as ctx_empty:
                            await module.update_xai_key("k1", {})
                        with self.assertRaises(Exception) as ctx_unknown:
                            await module.update_xai_key("k1", {"enbaled": False})
                        with self.assertRaises(Exception) as ctx_none:
                            await module.update_xai_key("k1", {"enabled": None})

        self.assertEqual(getattr(ctx_empty.exception, "status_code", None), 400)
        self.assertEqual(getattr(ctx_unknown.exception, "status_code", None), 400)
        self.assertEqual(getattr(ctx_none.exception, "status_code", None), 400)

    async def test_admin_xai_keys_concurrent_creates_preserve_all_entries(self):
        from app.api.v1.admin_api import xai_keys as module

        state = {"xai": {"keys": []}}
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                state.clear()
                state.update(data)

            async def load_config(self):
                return copy.deepcopy(state)

        with patch.object(module.config, "_config", state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        await asyncio.gather(
                            module.create_xai_key(
                                {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                            ),
                            module.create_xai_key(
                                {"id": "k2", "key": "xai-secret-87654321", "name": "secondary", "enabled": True}
                            ),
                        )

        ids = sorted(item["id"] for item in state["xai"]["keys"])
        self.assertEqual(ids, ["k1", "k2"])

    async def test_admin_xai_keys_create_uses_latest_persisted_state_over_stale_local_config(self):
        from app.api.v1.admin_api import xai_keys as module

        persisted_state = {
            "xai": {
                "keys": [
                    {"id": "k1", "key": "xai-secret-12345678", "name": "primary", "enabled": True}
                ]
            }
        }
        stale_local_state = {"xai": {"keys": []}}
        defaults = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class DummyStorage:
            @asynccontextmanager
            async def acquire_lock(self, *_args, **_kwargs):
                async with lock:
                    yield

            async def save_config(self, data):
                persisted_state.clear()
                persisted_state.update(copy.deepcopy(data))

            async def load_config(self):
                return copy.deepcopy(persisted_state)

        with patch.object(module.config, "_config", stale_local_state, create=True):
            with patch.object(module.config, "_defaults", defaults, create=True):
                with patch.object(module.config, "_ensure_defaults", Mock(return_value=None)):
                    with patch.object(module, "get_storage", return_value=DummyStorage()):
                        await module.create_xai_key(
                            {"id": "k2", "key": "xai-secret-87654321", "name": "secondary", "enabled": True}
                        )

        ids = sorted(item["id"] for item in persisted_state["xai"]["keys"])
        self.assertEqual(ids, ["k1", "k2"])


def test_admin_api_router_includes_xai_keys_module():
    from app.api.v1.admin import router as admin_router
    from app.api.v1.admin_api import router as admin_api_router

    admin_paths = {route.path for route in admin_router.routes}
    admin_api_paths = {route.path for route in admin_api_router.routes}

    assert "/xai-keys" in admin_paths
    assert "/xai-keys/{key_id}" in admin_paths
    assert "/xai-keys" in admin_api_paths
    assert "/xai-keys/{key_id}" in admin_api_paths


if __name__ == "__main__":
    unittest.main()
