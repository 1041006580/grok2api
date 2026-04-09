import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


def test_video_extend_service_requires_reference_id():
    from app.core.exceptions import ValidationException
    from app.services.grok.services.video_extend import VideoExtendService

    with pytest.raises(ValidationException) as exc_info:
        asyncio.run(
            VideoExtendService.extend(
                prompt="extend this clip",
                reference_id="",
                start_time=0,
            )
        )

    assert exc_info.value.code == "invalid_request_error"


def test_function_video_start_accepts_30_second_payload():
    from app.api.v1.function.video import VideoStartRequest, function_video_start

    result = asyncio.run(
        function_video_start(
            VideoStartRequest(
                prompt="make a cinematic clip",
                aspect_ratio="3:2",
                video_length=30,
                resolution_name="480p",
                preset="normal",
            )
        )
    )

    assert "task_id" in result
    assert result["aspect_ratio"] == "3:2"


async def _collect_streaming_body(response) -> str:
    chunks = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode())
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


def _manager_with_xai_key():
    return SimpleNamespace(
        acquire_key=lambda: SimpleNamespace(key="xai-test-key", id="fake-xai-id")
    )


def test_function_video_start_accepts_five_second_xai_payload():
    from app.api.v1.function.video import VideoStartRequest, function_video_start

    with patch(
        "app.api.v1.function.video.load_runtime_manager",
        return_value=_manager_with_xai_key(),
        create=True,
    ):
        result = asyncio.run(
            function_video_start(
                VideoStartRequest(
                    prompt="make a five second cinematic clip",
                    model="grok-imagine-video",
                    aspect_ratio="16:9",
                    video_length=5,
                    resolution_name="480p",
                    preset="normal",
                )
            )
        )

    assert "task_id" in result
    assert result["aspect_ratio"] == "16:9"


def test_public_video_start_accepts_five_second_xai_payload():
    from app.api.v1.public_api.video import VideoStartRequest, public_video_start

    with patch(
        "app.api.v1.public_api.video.load_runtime_manager",
        return_value=_manager_with_xai_key(),
        create=True,
    ):
        result = asyncio.run(
            public_video_start(
                VideoStartRequest(
                    prompt="make a five second public clip",
                    model="grok-imagine-video",
                    aspect_ratio="16:9",
                    video_length=5,
                    resolution_name="480p",
                    preset="normal",
                )
            )
        )

    assert "task_id" in result
    assert result["aspect_ratio"] == "16:9"


def test_function_video_start_rejects_xai_when_pool_empty():
    from app.api.v1.function import video as video_module

    async def scenario():
        fake_manager = SimpleNamespace(acquire_key=lambda: None)
        request = video_module.VideoStartRequest(
            prompt="pool check",
            model="grok-imagine-video",
            aspect_ratio="16:9",
            video_length=5,
            resolution_name="480p",
            preset="normal",
        )
        with patch.object(
            video_module,
            "load_runtime_manager",
            return_value=fake_manager,
            create=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await video_module.function_video_start(request)
        return exc_info.value

    exc = asyncio.run(scenario())
    assert exc.status_code == 503
    assert "xAI key pool" in str(exc.detail)


def test_public_video_start_rejects_xai_when_pool_empty():
    from app.api.v1.public_api import video as video_module

    async def scenario():
        fake_manager = SimpleNamespace(acquire_key=lambda: None)
        request = video_module.VideoStartRequest(
            prompt="pool check",
            model="grok-imagine-video",
            aspect_ratio="16:9",
            video_length=5,
            resolution_name="480p",
            preset="normal",
        )
        with patch.object(
            video_module,
            "load_runtime_manager",
            return_value=fake_manager,
            create=True,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await video_module.public_video_start(request)
        return exc_info.value

    exc = asyncio.run(scenario())
    assert exc.status_code == 503
    assert "xAI key pool" in str(exc.detail)


def test_function_video_sse_uses_selected_super_model():
    from app.api.v1.function import video as video_module

    async def scenario():
        started = await video_module.function_video_start(
            video_module.VideoStartRequest(
                prompt="render with super tier",
                model="grok-imagine-1.0-video-super",
                aspect_ratio="16:9",
                video_length=15,
                resolution_name="720p",
                preset="custom",
            )
        )
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

        async def fake_stream():
            yield (
                'data: {"choices":[{"delta":{"content":"https://example.com/super.mp4"}}]}\n\n'
            )
            yield "data: [DONE]\n\n"

        mock_completions = AsyncMock(return_value=fake_stream())
        with patch(
            "app.api.v1.function.video.ModelService.get",
            return_value=SimpleNamespace(is_video=True),
        ):
            with patch(
                "app.api.v1.function.video.VideoService.completions",
                new=mock_completions,
            ):
                response = await video_module.function_video_sse(
                    request=request,
                    task_id=started["task_id"],
                )
                body = await _collect_streaming_body(response)
        return mock_completions, body

    mock_completions, body = asyncio.run(scenario())

    mock_completions.assert_awaited_once()
    args = mock_completions.await_args.args
    assert args[0] == "grok-imagine-1.0-video-super"
    assert "https://example.com/super.mp4" in body


def test_function_video_sse_uses_xai_service_for_xai_model():
    from app.api.v1.function import video as video_module

    async def scenario():
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

        fake_service = type(
            "FakeXAIVideoService",
            (),
            {
                "generate": AsyncMock(
                    return_value={
                        "url": "https://example.com/function-xai.mp4",
                        "duration": 10,
                        "model": "grok-imagine-video",
                    }
                )
            },
        )

        legacy_completions = AsyncMock(side_effect=AssertionError("legacy path should not run"))
        fake_manager = _manager_with_xai_key()
        with patch.object(
            video_module,
            "load_runtime_manager",
            return_value=fake_manager,
            create=True,
        ):
            started = await video_module.function_video_start(
                video_module.VideoStartRequest(
                    prompt="make a short function xai clip",
                    model="grok-imagine-video",
                    aspect_ratio="16:9",
                    video_length=10,
                    resolution_name="720p",
                    preset="normal",
                )
            )
            with patch.object(
                video_module,
                "XAIVideoService",
                fake_service,
                create=True,
            ):
                with patch(
                    "app.api.v1.function.video.VideoService.completions",
                    new=legacy_completions,
                ):
                    response = await video_module.function_video_sse(
                        request=request,
                        task_id=started["task_id"],
                    )
                    body = await _collect_streaming_body(response)
        return fake_service.generate, legacy_completions, body

    mock_generate, legacy_completions, body = asyncio.run(scenario())

    mock_generate.assert_awaited_once_with(
        prompt="make a short function xai clip",
        model="grok-imagine-video",
        duration=10,
        aspect_ratio="16:9",
        resolution="720p",
        image_url=None,
    )
    legacy_completions.assert_not_called()
    assert "https://example.com/function-xai.mp4" in body
    assert "[DONE]" in body


def test_public_video_sse_uses_xai_service_for_xai_model():
    from app.api.v1.public_api import video as video_module

    async def scenario():
        request = SimpleNamespace(
            headers={},
            query_params={},
            is_disconnected=AsyncMock(return_value=False),
        )

        fake_service = type(
            "FakeXAIVideoService",
            (),
            {
                "generate": AsyncMock(
                    return_value={
                        "url": "https://example.com/xai-page.mp4",
                        "duration": 10,
                        "model": "grok-imagine-video",
                    }
                )
            },
        )

        legacy_completions = AsyncMock(side_effect=AssertionError("legacy path should not run"))
        fake_manager = _manager_with_xai_key()
        with patch.object(
            video_module,
            "load_runtime_manager",
            return_value=fake_manager,
            create=True,
        ):
            started = await video_module.public_video_start(
                video_module.VideoStartRequest(
                    prompt="make a short xai clip",
                    model="grok-imagine-video",
                    aspect_ratio="16:9",
                    video_length=10,
                    resolution_name="720p",
                    preset="normal",
                )
            )
            with patch("app.api.v1.public_api.video.get_public_api_key", return_value=""):
                with patch("app.api.v1.public_api.video.is_public_enabled", return_value=True):
                    with patch.object(
                        video_module,
                        "XAIVideoService",
                        fake_service,
                        create=True,
                    ):
                        with patch(
                            "app.api.v1.public_api.video.VideoService.completions",
                            new=legacy_completions,
                        ):
                            response = await video_module.public_video_sse(
                                request=request,
                                task_id=started["task_id"],
                            )
                            body = await _collect_streaming_body(response)
        return fake_service.generate, legacy_completions, body

    mock_generate, legacy_completions, body = asyncio.run(scenario())

    mock_generate.assert_awaited_once_with(
        prompt="make a short xai clip",
        model="grok-imagine-video",
        duration=10,
        aspect_ratio="16:9",
        resolution="720p",
        image_url=None,
    )
    legacy_completions.assert_not_called()
    assert "https://example.com/xai-page.mp4" in body
    assert "[DONE]" in body


def test_official_xai_video_generation_start_returns_request_id():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_key = SimpleNamespace(key="xai-test-key")
        fake_key.id = "k1"
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        captured_kwargs = []
        state = {"xai": {"keys": [{"id": "k1", "key": "xai-test-key", "enabled": True}]}}
        lock = asyncio.Lock()

        class FakeXAIVideoService:
            start_generation = AsyncMock(
                return_value={"request_id": "vidreq_123", "status": "pending"}
            )

            def __init__(self, *args, **kwargs):
                captured_kwargs.append(kwargs)

        class DummyStorage:
            async def load_config(self):
                return state

            async def save_config(self, data):
                state.clear()
                state.update(data)

            class _Lock:
                async def __aenter__(self):
                    await lock.acquire()

                async def __aexit__(self, exc_type, exc, tb):
                    lock.release()

            def acquire_lock(self, *_args, **_kwargs):
                return self._Lock()

        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with patch.object(video_module, "get_storage", return_value=DummyStorage()):
                with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                    result = await video_module.create_xai_video_generation(
                        video_module.XAIVideoGenerationRequest(
                            model="grok-imagine-video",
                            prompt="launch a rocket over mars",
                            duration=10,
                            aspect_ratio="16:9",
                            resolution="720p",
                        )
                    )
        return FakeXAIVideoService.start_generation, result, captured_kwargs, fake_key, fake_manager, state

    mock_start, result, captured_kwargs, fake_key, fake_manager, state = asyncio.run(scenario())

    mock_start.assert_awaited_once_with(
        prompt="launch a rocket over mars",
        model="grok-imagine-video",
        duration=10,
        aspect_ratio="16:9",
        resolution="720p",
        image_url=None,
    )
    assert captured_kwargs[0]["key_manager"] is fake_manager
    assert captured_kwargs[0]["key_record"] is fake_key
    assert result["request_id"] == "vidreq_123"
    assert result["status"] == "pending"
    assert state["xai"]["request_key_bindings"]["vidreq_123"]["key_id"] == "k1"
    assert state["xai"]["request_key_bindings"]["vidreq_123"]["key"] == "xai-test-key"


def test_official_xai_video_generation_start_still_returns_request_id_when_binding_persistence_fails():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_key = SimpleNamespace(key="xai-test-key", id="k1")
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        lock = asyncio.Lock()

        class FakeXAIVideoService:
            start_generation = AsyncMock(
                return_value={"request_id": "vidreq_123", "status": "pending"}
            )

            def __init__(self, *args, **kwargs):
                pass

        class DummyStorage:
            async def load_config(self):
                return {"xai": {"keys": [{"id": "k1", "key": "xai-test-key", "enabled": True}]}}

            async def save_config(self, data):
                raise RuntimeError("persist failed")

            class _Lock:
                async def __aenter__(self):
                    await lock.acquire()

                async def __aexit__(self, exc_type, exc, tb):
                    lock.release()

            def acquire_lock(self, *_args, **_kwargs):
                return self._Lock()

        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with patch.object(video_module, "get_storage", return_value=DummyStorage()):
                with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                    return await video_module.create_xai_video_generation(
                        video_module.XAIVideoGenerationRequest(
                            model="grok-imagine-video",
                            prompt="launch a rocket over mars",
                            duration=10,
                            aspect_ratio="16:9",
                            resolution="720p",
                        )
                    )

    result = asyncio.run(scenario())
    assert result["request_id"] == "vidreq_123"


def test_official_xai_video_generation_recovers_when_binding_persistence_fails():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_key = SimpleNamespace(key="xai-test-key", id="k1", enabled=True, status="active")
        fake_manager = SimpleNamespace(
            acquire_key=lambda: fake_key,
            list_keys=lambda: [fake_key],
        )
        state = {"xai": {"keys": [{"id": "k1", "key": "xai-test-key", "enabled": True}]}}
        lock = asyncio.Lock()

        class FakeXAIVideoService:
            start_generation = AsyncMock(
                return_value={"request_id": "vidreq_123", "status": "pending"}
            )
            get_generation = AsyncMock(
                return_value={
                    "request_id": "vidreq_123",
                    "status": "done",
                    "video": {"url": "https://example.com/video.mp4"},
                }
            )

            def __init__(self, *args, **kwargs):
                pass

        class DummyStorage:
            def __init__(self):
                self.save_calls = 0

            async def load_config(self):
                return state

            async def save_config(self, data):
                self.save_calls += 1
                if self.save_calls == 1:
                    raise RuntimeError("persist failed")
                state.clear()
                state.update(data)

            class _Lock:
                def __init__(self, lock_obj):
                    self._lock = lock_obj

                async def __aenter__(self):
                    await self._lock.acquire()

                async def __aexit__(self, exc_type, exc, tb):
                    self._lock.release()

            def acquire_lock(self, *_args, **_kwargs):
                return self._Lock(lock)

        storage = DummyStorage()
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with patch.object(video_module, "get_storage", return_value=storage):
                with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                    started = await video_module.create_xai_video_generation(
                        video_module.XAIVideoGenerationRequest(
                            model="grok-imagine-video",
                            prompt="launch a rocket over mars",
                            duration=10,
                            aspect_ratio="16:9",
                            resolution="720p",
                        )
                    )
                    result = await video_module.get_xai_video_generation(started["request_id"])
        return started, result, storage, state

    started, result, storage, state = asyncio.run(scenario())
    assert started["request_id"] == "vidreq_123"
    assert result["status"] == "done"
    assert storage.save_calls >= 2
    assert state["xai"]["request_key_bindings"]["vidreq_123"]["result"]["status"] == "done"


def test_official_xai_video_generation_status_returns_upstream_payload():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_key = SimpleNamespace(key="xai-test-key")
        fake_key.id = "k1"
        fake_manager = SimpleNamespace(
            acquire_key=lambda: SimpleNamespace(key="other-key"),
            list_keys=lambda: [fake_key],
        )
        captured_kwargs = []
        state = {
            "xai": {
                "keys": [{"id": "k1", "key": "rotated-key", "enabled": True}],
                "request_key_bindings": {"vidreq_123": {"key_id": "k1", "key": "xai-test-key"}},
            }
        }
        lock = asyncio.Lock()

        class FakeXAIVideoService:
            get_generation = AsyncMock(
                return_value={
                    "request_id": "vidreq_123",
                    "status": "done",
                    "video": {"url": "https://example.com/video.mp4"},
                }
            )

            def __init__(self, *args, **kwargs):
                captured_kwargs.append(kwargs)

        class DummyStorage:
            async def load_config(self):
                return state

            async def save_config(self, data):
                state.clear()
                state.update(data)

            class _Lock:
                async def __aenter__(self):
                    await lock.acquire()

                async def __aexit__(self, exc_type, exc, tb):
                    lock.release()

            def acquire_lock(self, *_args, **_kwargs):
                return self._Lock()

        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with patch.object(video_module, "get_storage", return_value=DummyStorage()):
                with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                    result = await video_module.get_xai_video_generation("vidreq_123")
                    second = await video_module.get_xai_video_generation("vidreq_123")
        return FakeXAIVideoService.get_generation, result, second, captured_kwargs, fake_key, fake_manager, state

    mock_get, result, second, captured_kwargs, fake_key, fake_manager, state = asyncio.run(scenario())

    assert mock_get.await_count == 1
    assert captured_kwargs[0]["key_manager"] is fake_manager
    assert captured_kwargs[0]["key_record"].id == fake_key.id
    assert captured_kwargs[0]["key_record"].key == fake_key.key
    assert result["request_id"] == "vidreq_123"
    assert result["status"] == "done"
    assert result["video"]["url"] == "https://example.com/video.mp4"
    assert second["request_id"] == "vidreq_123"
    assert "key" not in state["xai"]["request_key_bindings"]["vidreq_123"]
    assert state["xai"]["request_key_bindings"]["vidreq_123"]["result"]["status"] == "done"


def test_official_xai_video_generation_status_falls_back_to_single_active_key_when_binding_missing():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_key = SimpleNamespace(key="xai-test-key", id="k1", enabled=True, status="active")
        fake_manager = SimpleNamespace(
            acquire_key=lambda: fake_key,
            list_keys=lambda: [fake_key],
        )
        captured_kwargs = []
        state = {"xai": {"keys": []}}
        lock = asyncio.Lock()

        class FakeXAIVideoService:
            get_generation = AsyncMock(
                return_value={
                    "request_id": "vidreq_123",
                    "status": "pending",
                    "video": {"url": "https://example.com/video.mp4"},
                }
            )

            def __init__(self, *args, **kwargs):
                captured_kwargs.append(kwargs)

        class DummyStorage:
            async def load_config(self):
                return state

            async def save_config(self, data):
                state.clear()
                state.update(data)

            class _Lock:
                async def __aenter__(self):
                    await lock.acquire()

                async def __aexit__(self, exc_type, exc, tb):
                    lock.release()

            def acquire_lock(self, *_args, **_kwargs):
                return self._Lock()

        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with patch.object(video_module, "get_storage", return_value=DummyStorage()):
                with patch.object(video_module, "XAIVideoService", FakeXAIVideoService, create=True):
                    result = await video_module.get_xai_video_generation("vidreq_123")
        return result, captured_kwargs, fake_key, fake_manager

    result, captured_kwargs, fake_key, fake_manager = asyncio.run(scenario())
    assert result["request_id"] == "vidreq_123"
    assert captured_kwargs[0]["key_manager"] is fake_manager
    assert captured_kwargs[0]["key_record"] is fake_key


def test_official_xai_video_generation_start_rejects_empty_pool():
    from app.api.v1 import video as video_module

    async def scenario():
        fake_manager = SimpleNamespace(acquire_key=lambda: None)
        with patch.object(video_module, "load_runtime_manager", return_value=fake_manager):
            with pytest.raises(Exception) as exc_info:
                await video_module.create_xai_video_generation(
                    video_module.XAIVideoGenerationRequest(
                        model="grok-imagine-video",
                        prompt="launch a rocket over mars",
                        duration=10,
                        aspect_ratio="16:9",
                        resolution="720p",
                    )
                )
        return exc_info.value

    exc = asyncio.run(scenario())
    assert getattr(exc, "code", None) == "xai_api_key_missing"
