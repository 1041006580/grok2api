import asyncio
import time
import uuid
from typing import Optional, List, Dict, Any

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import verify_public_key, get_public_api_key, is_public_enabled
from app.core.config import get_config
from app.core.logger import logger
from app.api.v1.image import resolve_aspect_ratio
from app.services.grok.services.image import ImageGenerationService
from app.services.grok.services.model import ModelService
from app.services.grok.services.xai_image import XAIImageService
from app.services.grok.services.xai_key_manager import load_runtime_manager
from app.services.token.manager import get_token_manager

router = APIRouter()

IMAGINE_SESSION_TTL = 600
_IMAGINE_SESSIONS: dict[str, dict] = {}
_IMAGINE_SESSIONS_LOCK = asyncio.Lock()
LEGACY_IMAGINE_MODEL_ID = "grok-imagine-1.0"
XAI_IMAGE_MODEL_ID = "grok-imagine-image"
XAI_POOL_ERROR_MESSAGE = "xAI key pool is not configured with any enabled key"


async def _clean_sessions(now: float) -> None:
    expired = [
        key
        for key, info in _IMAGINE_SESSIONS.items()
        if now - float(info.get("created_at") or 0) > IMAGINE_SESSION_TTL
    ]
    for key in expired:
        _IMAGINE_SESSIONS.pop(key, None)


def _parse_sse_chunk(chunk: str) -> Optional[Dict[str, Any]]:
    if not chunk:
        return None
    event = None
    data_lines: List[str] = []
    for raw in str(chunk).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    data_str = "\n".join(data_lines)
    if data_str == "[DONE]":
        return None
    try:
        payload = orjson.loads(data_str)
    except orjson.JSONDecodeError:
        return None
    if event and isinstance(payload, dict) and "type" not in payload:
        payload["type"] = event
    return payload


def _normalize_imagine_model(value: Optional[str]) -> str:
    raw = (value or LEGACY_IMAGINE_MODEL_ID).strip()
    if raw in {LEGACY_IMAGINE_MODEL_ID, XAI_IMAGE_MODEL_ID}:
        return raw
    raise HTTPException(
        status_code=400,
        detail=f"model must be one of ['{LEGACY_IMAGINE_MODEL_ID}', '{XAI_IMAGE_MODEL_ID}']",
    )


def _ensure_xai_key_available() -> None:
    manager = load_runtime_manager()
    if manager.acquire_key() is None:
        raise HTTPException(status_code=400, detail=XAI_POOL_ERROR_MESSAGE)


async def _new_session(
    prompt: str,
    aspect_ratio: str,
    nsfw: Optional[bool],
    model: str,
) -> str:
    task_id = uuid.uuid4().hex
    now = time.time()
    async with _IMAGINE_SESSIONS_LOCK:
        await _clean_sessions(now)
        _IMAGINE_SESSIONS[task_id] = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "nsfw": nsfw,
            "model": model,
            "created_at": now,
        }
    return task_id


async def _stream_xai_imagine_images(
    prompt: str,
    aspect_ratio: str,
    *,
    model: str,
    run_id: str,
):
    images = await XAIImageService().generate(
        prompt=prompt,
        model=model,
        n=1,
        response_format="b64_json",
        aspect_ratio=aspect_ratio,
    )
    return [
        {
            "type": "image",
            "b64_json": image_b64,
            "sequence": index + 1,
            "created_at": int(time.time() * 1000),
            "aspect_ratio": aspect_ratio,
            "run_id": run_id,
        }
        for index, image_b64 in enumerate(images)
    ]


async def _get_session(task_id: str) -> Optional[dict]:
    if not task_id:
        return None
    now = time.time()
    async with _IMAGINE_SESSIONS_LOCK:
        await _clean_sessions(now)
        info = _IMAGINE_SESSIONS.get(task_id)
        if not info:
            return None
        created_at = float(info.get("created_at") or 0)
        if now - created_at > IMAGINE_SESSION_TTL:
            _IMAGINE_SESSIONS.pop(task_id, None)
            return None
        return dict(info)


async def _drop_session(task_id: str) -> None:
    if not task_id:
        return
    async with _IMAGINE_SESSIONS_LOCK:
        _IMAGINE_SESSIONS.pop(task_id, None)


async def _drop_sessions(task_ids: List[str]) -> int:
    if not task_ids:
        return 0
    removed = 0
    async with _IMAGINE_SESSIONS_LOCK:
        for task_id in task_ids:
            if task_id and task_id in _IMAGINE_SESSIONS:
                _IMAGINE_SESSIONS.pop(task_id, None)
                removed += 1
    return removed


@router.websocket("/imagine/ws")
async def public_imagine_ws(websocket: WebSocket):
    session_id = None
    session_model = LEGACY_IMAGINE_MODEL_ID
    task_id = websocket.query_params.get("task_id")
    if task_id:
        info = await _get_session(task_id)
        if info:
            session_id = task_id
            session_model = _normalize_imagine_model(info.get("model"))

    ok = True
    if session_id is None:
        public_key = get_public_api_key()
        public_enabled = is_public_enabled()
        if not public_key:
            ok = public_enabled
        else:
            key = websocket.query_params.get("public_key")
            ok = key == public_key

    if not ok:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    stop_event = asyncio.Event()
    run_task: Optional[asyncio.Task] = None

    async def _send(payload: dict) -> bool:
        try:
            await websocket.send_text(orjson.dumps(payload).decode())
            return True
        except Exception:
            return False

    async def _stop_run():
        nonlocal run_task
        stop_event.set()
        if run_task and not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except Exception:
                pass
        run_task = None
        stop_event.clear()

    async def _run(
        prompt: str,
        aspect_ratio: str,
        nsfw: Optional[bool],
        model_id: str,
    ):
        model_info = ModelService.get(model_id)
        if model_id == XAI_IMAGE_MODEL_ID:
            run_id = uuid.uuid4().hex
            await _send(
                {
                    "type": "status",
                    "status": "running",
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "run_id": run_id,
                }
            )
            try:
                for payload in await _stream_xai_imagine_images(
                    prompt,
                    aspect_ratio,
                    model=model_id,
                    run_id=run_id,
                ):
                    if stop_event.is_set():
                        break
                    await _send(payload)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"xAI imagine stream error: {e}")
                await _send(
                    {
                        "type": "error",
                        "message": str(e),
                        "code": "internal_error",
                    }
                )
            await _send({"type": "status", "status": "stopped", "run_id": run_id})
            return

        if not model_info or not model_info.is_image:
            await _send(
                {
                    "type": "error",
                    "message": "Image model is not available.",
                    "code": "model_not_supported",
                }
            )
            return

        token_mgr = await get_token_manager()
        run_id = uuid.uuid4().hex

        await _send(
            {
                "type": "status",
                "status": "running",
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "run_id": run_id,
            }
        )

        while not stop_event.is_set():
            try:
                await token_mgr.reload_if_stale()
                token = None
                for pool_name in ModelService.pool_candidates_for_model(
                    model_info.model_id
                ):
                    token = token_mgr.get_token(pool_name)
                    if token:
                        break

                if not token:
                    await _send(
                        {
                            "type": "error",
                            "message": "No available tokens. Please try again later.",
                            "code": "rate_limit_exceeded",
                        }
                    )
                    await asyncio.sleep(2)
                    continue

                result = await ImageGenerationService().generate(
                    token_mgr=token_mgr,
                    token=token,
                    model_info=model_info,
                    prompt=prompt,
                    n=int(get_config("image.default_image_count") or 4),
                    response_format="b64_json",
                    size="1024x1024",
                    aspect_ratio=aspect_ratio,
                    stream=True,
                    enable_nsfw=nsfw,
                )
                if result.stream:
                    async for chunk in result.data:
                        payload = _parse_sse_chunk(chunk)
                        if not payload:
                            continue
                        if isinstance(payload, dict):
                            payload.setdefault("run_id", run_id)
                        await _send(payload)
                else:
                    images = [img for img in result.data if img and img != "error"]
                    if images:
                        for img_b64 in images:
                            await _send(
                                {
                                    "type": "image",
                                    "b64_json": img_b64,
                                    "created_at": int(time.time() * 1000),
                                    "aspect_ratio": aspect_ratio,
                                    "run_id": run_id,
                                }
                            )
                    else:
                        await _send(
                            {
                                "type": "error",
                                "message": "Image generation returned empty data.",
                                "code": "empty_image",
                            }
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Imagine stream error: {e}")
                await _send(
                    {
                        "type": "error",
                        "message": str(e),
                        "code": "internal_error",
                    }
                )
                await asyncio.sleep(1.5)

        await _send({"type": "status", "status": "stopped", "run_id": run_id})

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except (RuntimeError, WebSocketDisconnect):
                break

            try:
                payload = orjson.loads(raw)
            except Exception:
                await _send(
                    {
                        "type": "error",
                        "message": "Invalid message format.",
                        "code": "invalid_payload",
                    }
                )
                continue

            action = payload.get("type")
            if action == "start":
                prompt = str(payload.get("prompt") or "").strip()
                if not prompt:
                    await _send(
                        {
                            "type": "error",
                            "message": "Prompt cannot be empty.",
                            "code": "invalid_prompt",
                        }
                    )
                    continue
                aspect_ratio = resolve_aspect_ratio(
                    str(payload.get("aspect_ratio") or "2:3").strip() or "2:3"
                )
                nsfw = payload.get("nsfw")
                if nsfw is not None:
                    nsfw = bool(nsfw)
                try:
                    model_id = _normalize_imagine_model(
                        payload.get("model") or session_model
                    )
                except HTTPException:
                    await _send(
                        {
                            "type": "error",
                            "message": "Image model is not available.",
                            "code": "model_not_supported",
                        }
                    )
                    continue
                await _stop_run()
                run_task = asyncio.create_task(
                    _run(prompt, aspect_ratio, nsfw, model_id)
                )
            elif action == "stop":
                await _stop_run()
            else:
                await _send(
                    {
                        "type": "error",
                        "message": "Unknown action.",
                        "code": "invalid_action",
                    }
                )

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected by client")
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        await _stop_run()

        try:
            from starlette.websockets import WebSocketState
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000, reason="Server closing connection")
        except Exception as e:
            logger.debug(f"WebSocket close ignored: {e}")
        if session_id:
            await _drop_session(session_id)


@router.get("/imagine/sse")
async def public_imagine_sse(
    request: Request,
    task_id: str = Query(""),
    prompt: str = Query(""),
    aspect_ratio: str = Query("16:9"),
):
    """Imagine 图片瀑布流（SSE 兜底）"""
    session = None
    if task_id:
        session = await _get_session(task_id)
        if not session:
            raise HTTPException(status_code=404, detail="Task not found")
    else:
        public_key = get_public_api_key()
        public_enabled = is_public_enabled()
        if not public_key:
            if not public_enabled:
                raise HTTPException(status_code=401, detail="Public access is disabled")
        else:
            key = request.query_params.get("public_key")
            if key != public_key:
                raise HTTPException(status_code=401, detail="Invalid authentication token")

    if session:
        prompt = str(session.get("prompt") or "").strip()
        ratio = str(session.get("aspect_ratio") or "2:3").strip() or "2:3"
        nsfw = session.get("nsfw")
        model_id = _normalize_imagine_model(session.get("model"))
    else:
        prompt = (prompt or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")
        ratio = str(aspect_ratio or "2:3").strip() or "2:3"
        ratio = resolve_aspect_ratio(ratio)
        nsfw = request.query_params.get("nsfw")
        if nsfw is not None:
            nsfw = str(nsfw).lower() in ("1", "true", "yes", "on")
        model_id = LEGACY_IMAGINE_MODEL_ID

    async def event_stream():
        try:
            sequence = 0
            run_id = uuid.uuid4().hex

            yield (
                f"data: {orjson.dumps({'type': 'status', 'status': 'running', 'prompt': prompt, 'aspect_ratio': ratio, 'run_id': run_id}).decode()}\n\n"
            )

            if model_id == XAI_IMAGE_MODEL_ID:
                try:
                    for payload in await _stream_xai_imagine_images(
                        prompt,
                        ratio,
                        model=model_id,
                        run_id=run_id,
                    ):
                        yield f"data: {orjson.dumps(payload).decode()}\n\n"
                except Exception as e:
                    logger.warning(f"xAI imagine SSE error: {e}")
                    yield (
                        f"data: {orjson.dumps({'type': 'error', 'message': str(e), 'code': 'internal_error'}).decode()}\n\n"
                    )
                yield (
                    f"data: {orjson.dumps({'type': 'status', 'status': 'stopped', 'run_id': run_id}).decode()}\n\n"
                )
                return

            model_info = ModelService.get(model_id)
            if not model_info or not model_info.is_image:
                yield (
                    f"data: {orjson.dumps({'type': 'error', 'message': 'Image model is not available.', 'code': 'model_not_supported'}).decode()}\n\n"
                )
                return

            token_mgr = await get_token_manager()

            while True:
                if await request.is_disconnected():
                    break
                if task_id:
                    session_alive = await _get_session(task_id)
                    if not session_alive:
                        break

                try:
                    await token_mgr.reload_if_stale()
                    token = None
                    for pool_name in ModelService.pool_candidates_for_model(
                        model_info.model_id
                    ):
                        token = token_mgr.get_token(pool_name)
                        if token:
                            break

                    if not token:
                        yield (
                            f"data: {orjson.dumps({'type': 'error', 'message': 'No available tokens. Please try again later.', 'code': 'rate_limit_exceeded'}).decode()}\n\n"
                        )
                        await asyncio.sleep(2)
                        continue

                    result = await ImageGenerationService().generate(
                        token_mgr=token_mgr,
                        token=token,
                        model_info=model_info,
                        prompt=prompt,
                        n=int(get_config("image.default_image_count") or 4),
                        response_format="b64_json",
                        size="1024x1024",
                        aspect_ratio=ratio,
                        stream=True,
                        enable_nsfw=nsfw,
                    )
                    if result.stream:
                        async for chunk in result.data:
                            payload = _parse_sse_chunk(chunk)
                            if not payload:
                                continue
                            if isinstance(payload, dict):
                                payload.setdefault("run_id", run_id)
                            yield f"data: {orjson.dumps(payload).decode()}\n\n"
                    else:
                        images = [img for img in result.data if img and img != "error"]
                        if images:
                            for img_b64 in images:
                                sequence += 1
                                payload = {
                                    "type": "image",
                                    "b64_json": img_b64,
                                    "sequence": sequence,
                                    "created_at": int(time.time() * 1000),
                                    "aspect_ratio": ratio,
                                    "run_id": run_id,
                                }
                                yield f"data: {orjson.dumps(payload).decode()}\n\n"
                        else:
                            yield (
                                f"data: {orjson.dumps({'type': 'error', 'message': 'Image generation returned empty data.', 'code': 'empty_image'}).decode()}\n\n"
                            )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Imagine SSE error: {e}")
                    yield (
                        f"data: {orjson.dumps({'type': 'error', 'message': str(e), 'code': 'internal_error'}).decode()}\n\n"
                    )
                    await asyncio.sleep(1.5)

            yield (
                f"data: {orjson.dumps({'type': 'status', 'status': 'stopped', 'run_id': run_id}).decode()}\n\n"
            )
        finally:
            if task_id:
                await _drop_session(task_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/imagine/config")
async def public_imagine_config():
    return {
        "final_min_bytes": int(get_config("image.final_min_bytes") or 0),
        "medium_min_bytes": int(get_config("image.medium_min_bytes") or 0),
        "nsfw": bool(get_config("image.nsfw")),
    }


class ImagineStartRequest(BaseModel):
    prompt: str
    model: Optional[str] = LEGACY_IMAGINE_MODEL_ID
    aspect_ratio: Optional[str] = "16:9"
    nsfw: Optional[bool] = None


@router.post("/imagine/start", dependencies=[Depends(verify_public_key)])
async def public_imagine_start(data: ImagineStartRequest):
    prompt = (data.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    model = _normalize_imagine_model(data.model)
    if model == XAI_IMAGE_MODEL_ID:
        _ensure_xai_key_available()
    ratio = resolve_aspect_ratio(str(data.aspect_ratio or "2:3").strip() or "2:3")
    task_id = await _new_session(prompt, ratio, data.nsfw, model)
    return {"task_id": task_id, "aspect_ratio": ratio, "model": model}


class ImagineStopRequest(BaseModel):
    task_ids: List[str]


@router.post("/imagine/stop", dependencies=[Depends(verify_public_key)])
async def public_imagine_stop(data: ImagineStopRequest):
    removed = await _drop_sessions(data.task_ids or [])
    return {"status": "success", "removed": removed}
