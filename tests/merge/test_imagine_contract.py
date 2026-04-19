import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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
        acquire_key=lambda: SimpleNamespace(key="dummy001", id="fake-xai-id")
    )


def test_public_imagine_start_accepts_xai_image_model():
    from app.api.v1.public_api import imagine as imagine_module

    async def scenario():
        with patch.object(
            imagine_module,
            "load_runtime_manager",
            return_value=_manager_with_xai_key(),
            create=True,
        ):
            started = await imagine_module.public_imagine_start(
                imagine_module.ImagineStartRequest(
                    prompt="make an xai image",
                    model="grok-imagine-image",
                    aspect_ratio="16:9",
                    nsfw=False,
                )
            )
            session = await imagine_module._get_session(started["task_id"])
        return started, session

    started, session = asyncio.run(scenario())

    assert started["task_id"]
    assert session["model"] == "grok-imagine-image"


def test_public_imagine_sse_uses_xai_service_for_xai_model():
    from app.api.v1.public_api import imagine as imagine_module

    async def scenario():
        request = SimpleNamespace(
            headers={},
            query_params={},
            is_disconnected=AsyncMock(return_value=False),
        )

        fake_service = type(
            "FakeXAIImageService",
            (),
            {
                "generate": AsyncMock(return_value=["ZmFrZS1pbWFnZS1iYXNlNjQ="]),
            },
        )

        with patch.object(
            imagine_module,
            "load_runtime_manager",
            return_value=_manager_with_xai_key(),
            create=True,
        ):
            started = await imagine_module.public_imagine_start(
                imagine_module.ImagineStartRequest(
                    prompt="make an xai image",
                    model="grok-imagine-image",
                    aspect_ratio="16:9",
                    nsfw=False,
                )
            )
            with patch("app.api.v1.public_api.imagine.get_public_api_key", return_value=""):
                with patch("app.api.v1.public_api.imagine.is_public_enabled", return_value=True):
                    with patch.object(
                        imagine_module,
                        "XAIImageService",
                        fake_service,
                        create=True,
                    ):
                        response = await imagine_module.public_imagine_sse(
                            request=request,
                            task_id=started["task_id"],
                        )
                        body = await _collect_streaming_body(response)
        return body, fake_service.generate

    body, mock_generate = asyncio.run(scenario())

    mock_generate.assert_awaited_once_with(
        prompt="make an xai image",
        model="grok-imagine-image",
        n=1,
        response_format="b64_json",
        aspect_ratio="16:9",
    )
    assert '"type":"image"' in body
    assert "ZmFrZS1pbWFnZS1iYXNlNjQ=" in body
