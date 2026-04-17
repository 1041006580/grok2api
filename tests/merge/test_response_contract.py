import asyncio
from unittest.mock import AsyncMock, patch


def test_responses_route_uses_xai_direct_service_for_multi_agent_model():
    from app.api.v1 import response as response_module

    async def scenario():
        fake_service = type(
            "FakeXAIResponsesService",
            (),
            {
                "__init__": lambda self, *args, **kwargs: None,
                "create_response": AsyncMock(
                    return_value={
                        "id": "resp_xai",
                        "object": "response",
                        "model": "grok-4.20-multi-agent",
                        "output": [],
                    }
                ),
            },
        )
        with patch.object(response_module, "XAIResponsesService", fake_service, create=True):
            response = await response_module.create_response(
                response_module.ResponseCreateRequest(
                    model="grok-4.20-multi-agent",
                    input="Research this topic",
                    stream=False,
                    reasoning={"effort": "high"},
                    tools=[{"type": "web_search"}],
                )
            )
        return fake_service.create_response, response

    mock_create, response = asyncio.run(scenario())

    mock_create.assert_awaited_once()
    payload = mock_create.await_args.args[0]
    assert payload["model"] == "grok-4.20-multi-agent"
    assert payload["input"] == "Research this topic"
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["tools"] == [{"type": "web_search"}]
    assert payload["include"] == ["verbose_streaming"]
    assert response.status_code == 200
    assert b"resp_xai" in response.body


def test_responses_route_streams_xai_direct_service_for_multi_agent_model():
    from app.api.v1 import response as response_module

    async def scenario():
        async def fake_stream():
            yield "event: response.created\ndata: {}\n\n"
            yield "data: [DONE]\n\n"

        fake_service = type(
            "FakeXAIResponsesService",
            (),
            {
                "__init__": lambda self, *args, **kwargs: None,
                "create_response": AsyncMock(return_value=fake_stream()),
            },
        )
        with patch.object(response_module, "XAIResponsesService", fake_service, create=True):
            response = await response_module.create_response(
                response_module.ResponseCreateRequest(
                    model="grok-4.20-multi-agent",
                    input="Research this topic",
                    stream=True,
                )
            )
        return fake_service.create_response, response

    mock_create, response = asyncio.run(scenario())

    mock_create.assert_awaited_once()
    payload = mock_create.await_args.args[0]
    assert payload["include"] == ["verbose_streaming"]
    assert response.media_type == "text/event-stream"
