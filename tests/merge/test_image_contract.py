import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import orjson


def test_images_route_supports_xai_api_key_generation_model():
    from app.api.v1 import image as image_module

    class FakeRequest:
        prompt = "draw a futuristic city"
        model = "grok-imagine-image"
        n = 1
        size = "1280x720"
        quality = "standard"
        response_format = "b64_json"
        style = None
        stream = False

    fake_key = SimpleNamespace(key="dummy001", id="k1")
    fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)

    captured_kwargs = []

    class FakeXAIImageService:
        def __init__(self, *args, **kwargs):
            captured_kwargs.append(kwargs)

        async def generate(self, **kwargs):
            captured_kwargs.append(kwargs)
            return ["ZmFrZS1pbWFnZS1iYXNlNjQ="]

    async def scenario():
        with patch.object(image_module, "load_runtime_manager", return_value=fake_manager, create=True):
            with patch.object(image_module, "XAIImageService", FakeXAIImageService, create=True):
                return await image_module.create_image(FakeRequest())

    response = asyncio.run(scenario())
    body = orjson.loads(response.body)

    assert body["data"][0]["b64_json"] == "ZmFrZS1pbWFnZS1iYXNlNjQ="
    assert captured_kwargs[0]["key_manager"] is fake_manager
    assert captured_kwargs[0]["key_record"] is fake_key
    assert captured_kwargs[1] == {
        "prompt": "draw a futuristic city",
        "model": "grok-imagine-image",
        "n": 1,
        "response_format": "b64_json",
        "aspect_ratio": "16:9",
    }

