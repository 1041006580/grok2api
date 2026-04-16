import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_validate_request_accepts_official_xai_direct_chat_model():
    from app.api.v1.chat import ChatCompletionRequest, validate_request

    request = ChatCompletionRequest(
        model="grok-4.20-multi-agent",
        messages=[{"role": "user", "content": "hello"}],
        stream=False,
    )

    validate_request(request)


def test_chat_completions_routes_official_xai_model_to_direct_service():
    from app.api.v1 import chat as chat_module

    async def scenario():
        fake_service = type(
            "FakeXAIChatService",
            (),
            {
                "__init__": lambda self, *args, **kwargs: None,
                "create_chat_completion": AsyncMock(
                    return_value={
                        "id": "chatcmpl_xai",
                        "object": "chat.completion",
                        "created": 123,
                        "model": "grok-4.20-multi-agent",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "hello from xai"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ),
            },
        )
        http_request = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        with patch.object(chat_module, "XAIChatService", fake_service, create=True):
            with patch.object(
                chat_module,
                "_select_xai_key_manager_and_record",
                return_value=(SimpleNamespace(), SimpleNamespace(key="dummy001", id="k1")),
                create=True,
            ):
                with patch.object(chat_module, "_log_request", new=AsyncMock()):
                        response = await chat_module.chat_completions(
                            chat_module.ChatCompletionRequest(
                                model="grok-4.20-multi-agent",
                                messages=[{"role": "user", "content": "hello"}],
                                stream=False,
                            ),
                        http_request=http_request,
                    )
        return fake_service.create_chat_completion, response

    mock_create, response = asyncio.run(scenario())

    mock_create.assert_awaited_once()
    assert response.status_code == 200
    assert b"chatcmpl_xai" in response.body


def test_chat_completions_deferred_xai_chat_remembers_binding():
    from app.api.v1 import chat as chat_module

    async def scenario():
        fake_key = SimpleNamespace(key="dummy001", id="k1")
        fake_manager = SimpleNamespace(acquire_key=lambda: fake_key)
        fake_service = type(
            "FakeXAIChatService",
            (),
            {
                "create_chat_completion": AsyncMock(
                    return_value={"request_id": "chatreq_123"}
                ),
                "__init__": lambda self, *args, **kwargs: None,
            },
        )
        http_request = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        remember = AsyncMock()
        with patch.object(chat_module, "XAIChatService", fake_service, create=True):
            with patch.object(chat_module, "_select_xai_key_manager_and_record", return_value=(fake_manager, fake_key), create=True):
                with patch.object(chat_module, "_remember_xai_chat_request_key", new=remember, create=True):
                    with patch.object(chat_module, "_log_request", new=AsyncMock()):
                            response = await chat_module.chat_completions(
                                chat_module.ChatCompletionRequest(
                                    model="grok-4.20-multi-agent",
                                    messages=[{"role": "user", "content": "hello"}],
                                    stream=False,
                                    deferred=True,
                            ),
                            http_request=http_request,
                        )
        return fake_service.create_chat_completion, remember, response

    mock_create, remember, response = asyncio.run(scenario())

    mock_create.assert_awaited_once()
    remember.assert_awaited_once()
    assert remember.await_args.args[0] == "chatreq_123"
    assert getattr(remember.await_args.args[1], "id", "") == "k1"


def test_get_deferred_chat_completion_route_uses_bound_key_and_returns_accepted():
    from app.api.v1 import chat as chat_module

    async def scenario():
        fake_service = type(
            "FakeXAIChatService",
            (),
            {
                "get_deferred_completion": AsyncMock(return_value=(202, None)),
                "__init__": lambda self, *args, **kwargs: None,
            },
        )
        with patch.object(chat_module, "XAIChatService", fake_service, create=True):
            with patch.object(
                chat_module,
                "_get_bound_xai_chat_key",
                new=AsyncMock(return_value={"key_record": SimpleNamespace(key="dummy001", id="k1")}),
                create=True,
            ):
                response = await chat_module.get_deferred_chat_completion("chatreq_123")
        return fake_service.get_deferred_completion, response

    mock_get, response = asyncio.run(scenario())

    mock_get.assert_awaited_once_with("chatreq_123")
    assert response.status_code == 202
