"""
Videos API route (OpenAI-compatible create endpoint).
"""

import asyncio
import base64
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import orjson
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import get_config
from app.core.exceptions import UpstreamException, ValidationException
from app.services.grok.services.model import ModelService
from app.services.grok.services.video import VideoService
from app.services.grok.services.xai_key_manager import load_runtime_manager
from app.services.grok.services.xai_video import XAIVideoService


router = APIRouter(tags=["Videos"])

VIDEO_MODEL_ID = "grok-imagine-1.0-video"
XAI_VIDEO_MODEL_ID = "grok-imagine-video"
SIZE_TO_ASPECT = {
    "1280x720": "16:9",
    "720x1280": "9:16",
    "1792x1024": "3:2",
    "1024x1792": "2:3",
    "1024x1024": "1:1",
}
QUALITY_TO_RESOLUTION = {
    "standard": "480p",
    "high": "720p",
}
MIN_SECONDS = 6
MAX_SECONDS = 30
ALLOWED_ASPECT_RATIOS = {"16:9", "9:16", "3:2", "2:3", "1:1"}
_XAI_REQUEST_KEY_TTL_SECONDS = 3600
_XAI_REQUEST_KEYS: dict[str, tuple[float, object]] = {}
_XAI_REQUEST_KEYS_LOCK = asyncio.Lock()


class VideoCreateRequest(BaseModel):
    """Supported create params only; unknown fields are ignored by design."""

    model_config = ConfigDict(extra="ignore")

    prompt: str = Field(..., description="Video prompt")
    model: Optional[str] = Field(VIDEO_MODEL_ID, description="Model id")
    size: Optional[str] = Field("1792x1024", description="Output size")
    seconds: Optional[int] = Field(6, description="Video length in seconds")
    quality: Optional[str] = Field("standard", description="Quality: standard/high")
    image_reference: Optional[Any] = Field(None, description="Structured image reference")
    input_reference: Optional[Any] = Field(None, description="Multipart input reference file")


class XAIVideoGenerationRequest(BaseModel):
    """Official-style xAI video generation request."""

    model_config = ConfigDict(extra="ignore")

    model: str = Field(XAI_VIDEO_MODEL_ID, description="Model id")
    prompt: str = Field(..., description="Video prompt")
    duration: int = Field(5, description="Duration in seconds")
    aspect_ratio: str = Field("16:9", description="Aspect ratio")
    resolution: str = Field("720p", description="Resolution")
    image: Optional[Any] = Field(None, description="Optional image reference")


def _raise_validation_error(exc: ValidationError) -> None:
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = first.get("loc", [])
        msg = first.get("msg", "Invalid request")
        code = first.get("type", "invalid_value")
        param_parts = [str(x) for x in loc if not (isinstance(x, int) or str(x).isdigit())]
        param = ".".join(param_parts) if param_parts else None
        raise ValidationException(message=msg, param=param, code=code)
    raise ValidationException(message="Invalid request", code="invalid_value")


def _extract_video_url(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        return ""

    md_match = re.search(r"\[video\]\(([^)\s]+)\)", content)
    if md_match:
        return md_match.group(1).strip()

    html_match = re.search(r"""<source[^>]+src=["']([^"']+)["']""", content)
    if html_match:
        return html_match.group(1).strip()

    url_match = re.search(r"""https?://[^\s"'<>]+""", content)
    if url_match:
        return url_match.group(0).strip().rstrip(".,)")

    return ""


def _normalize_model(model: Optional[str]) -> str:
    requested = (model or VIDEO_MODEL_ID).strip()
    if requested == XAI_VIDEO_MODEL_ID:
        return requested
    if requested != VIDEO_MODEL_ID:
        raise ValidationException(
            message=(
                f"model must be one of ['{VIDEO_MODEL_ID}', '{XAI_VIDEO_MODEL_ID}']"
            ),
            param="model",
            code="model_not_supported",
        )
    model_info = ModelService.get(requested)
    if not model_info or not model_info.is_video:
        raise ValidationException(
            message=f"The model `{requested}` is not supported for video generation.",
            param="model",
            code="model_not_supported",
        )
    return requested


def _normalize_size(size: Optional[str]) -> Tuple[str, str]:
    value = (size or "1792x1024").strip()
    aspect_ratio = SIZE_TO_ASPECT.get(value)
    if not aspect_ratio:
        raise ValidationException(
            message=f"size must be one of {sorted(SIZE_TO_ASPECT.keys())}",
            param="size",
            code="invalid_size",
        )
    return value, aspect_ratio


def _normalize_quality(quality: Optional[str]) -> Tuple[str, str]:
    value = (quality or "standard").strip().lower()
    resolution = QUALITY_TO_RESOLUTION.get(value)
    if not resolution:
        raise ValidationException(
            message=f"quality must be one of {sorted(QUALITY_TO_RESOLUTION.keys())}",
            param="quality",
            code="invalid_quality",
        )
    return value, resolution


def _normalize_ratio(aspect_ratio: Optional[str]) -> str:
    value = str(aspect_ratio or "16:9").strip()
    if value not in ALLOWED_ASPECT_RATIOS:
        raise ValidationException(
            message=f"aspect_ratio must be one of {sorted(ALLOWED_ASPECT_RATIOS)}",
            param="aspect_ratio",
            code="invalid_aspect_ratio",
        )
    return value


def _normalize_seconds(seconds: Optional[int], *, model: str) -> int:
    value = int(seconds or 6)
    min_seconds = 1 if model == XAI_VIDEO_MODEL_ID else MIN_SECONDS
    max_seconds = 15 if model == XAI_VIDEO_MODEL_ID else MAX_SECONDS
    if value < min_seconds or value > max_seconds:
        raise ValidationException(
            message=f"seconds must be between {min_seconds} and {max_seconds}",
            param="seconds",
            code="invalid_seconds",
        )
    return value


def _select_xai_key_manager_and_record():
    manager = load_runtime_manager()
    key_record = manager.acquire_key()
    if not key_record:
        raise ValidationException(
            message="xAI key pool is not configured with any enabled key",
            param="model",
            code="xai_api_key_missing",
        )
    return manager, key_record


async def _remember_xai_request_key(request_id: str, key_record: object) -> None:
    request_id = str(request_id or "").strip()
    if not request_id:
        return
    now = time.monotonic()
    async with _XAI_REQUEST_KEYS_LOCK:
        expired = [
            key
            for key, (created_at, _) in _XAI_REQUEST_KEYS.items()
            if now - created_at > _XAI_REQUEST_KEY_TTL_SECONDS
        ]
        for key in expired:
            _XAI_REQUEST_KEYS.pop(key, None)
        _XAI_REQUEST_KEYS[request_id] = (now, key_record)


async def _get_bound_xai_request_key(request_id: str):
    request_id = str(request_id or "").strip()
    now = time.monotonic()
    async with _XAI_REQUEST_KEYS_LOCK:
        record = _XAI_REQUEST_KEYS.get(request_id)
        if not record:
            return None
        created_at, key_record = record
        if now - created_at > _XAI_REQUEST_KEY_TTL_SECONDS:
            _XAI_REQUEST_KEYS.pop(request_id, None)
            return None
        return key_record


def _validate_reference_value(value: str, param: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    if candidate.startswith("data:"):
        return candidate
    raise ValidationException(
        message=f"{param} must be a URL or data URI",
        param=param,
        code="invalid_reference",
    )


def _parse_image_reference(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped[0] in {"{", "["}:
            try:
                value = orjson.loads(stripped)
            except orjson.JSONDecodeError:
                return _validate_reference_value(stripped, "image_reference")
        else:
            return _validate_reference_value(stripped, "image_reference")

    if not isinstance(value, dict):
        raise ValidationException(
            message=(
                "image_reference must be an object with exactly one of "
                "`image_url` or `file_id`"
            ),
            param="image_reference",
            code="invalid_reference",
        )

    image_url = value.get("image_url")
    file_id = value.get("file_id")
    image_url = image_url.strip() if isinstance(image_url, str) else ""
    file_id = file_id.strip() if isinstance(file_id, str) else ""

    has_image_url = bool(image_url)
    has_file_id = bool(file_id)
    if has_image_url == has_file_id:
        raise ValidationException(
            message="image_reference requires exactly one of image_url or file_id",
            param="image_reference",
            code="invalid_reference",
        )

    if has_file_id:
        raise ValidationException(
            message=(
                "image_reference.file_id is not supported in current reverse pipeline; "
                "please use image_reference.image_url or multipart input_reference"
            ),
            param="image_reference.file_id",
            code="unsupported_reference",
        )

    return _validate_reference_value(image_url, "image_reference.image_url")


def _parse_xai_image_reference(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None

    if isinstance(value, str):
        return _validate_reference_value(value, "image.url")

    if not isinstance(value, dict):
        raise ValidationException(
            message="image must be an object with a `url` field or a direct URL string",
            param="image",
            code="invalid_reference",
        )

    url = value.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValidationException(
            message="image.url is required when image is provided",
            param="image.url",
            code="invalid_reference",
        )
    return _validate_reference_value(url, "image.url")


async def _upload_to_data_uri(file: UploadFile, param: str) -> str:
    payload = await file.read()
    if not payload:
        raise ValidationException(
            message=f"{param} upload is empty",
            param=param,
            code="empty_file",
        )
    content_type = (file.content_type or "application/octet-stream").strip()
    encoded = base64.b64encode(payload).decode()
    return f"data:{content_type};base64,{encoded}"


async def _build_references_for_json(payload: BaseModel) -> List[str]:
    references: List[str] = []
    parsed_image_ref = _parse_image_reference(getattr(payload, "image_reference", None))
    if parsed_image_ref:
        references.append(parsed_image_ref)
    if getattr(payload, "input_reference", None) not in (None, ""):
        raise ValidationException(
            message="input_reference must be uploaded as multipart/form-data file",
            param="input_reference",
            code="invalid_reference",
        )
    return references


async def _build_payload_and_references_for_form(
    *,
    schema: type[BaseModel],
    prompt: Optional[str],
    model: Optional[str],
    size: Optional[str],
    seconds: Optional[int],
    quality: Optional[str],
    image_reference: Optional[str],
    input_reference: Optional[UploadFile],
) -> Tuple[BaseModel, List[str]]:
    try:
        payload = schema.model_validate(
            {
                "prompt": prompt,
                "model": model,
                "size": size,
                "seconds": seconds,
                "quality": quality,
                "image_reference": image_reference,
                "input_reference": None,
            }
        )
    except ValidationError as exc:
        _raise_validation_error(exc)

    references: List[str] = []
    if isinstance(input_reference, (UploadFile, StarletteUploadFile)):
        references.append(await _upload_to_data_uri(input_reference, "input_reference"))
    elif input_reference not in (None, ""):
        raise ValidationException(
            message="input_reference must be a file in multipart/form-data",
            param="input_reference",
            code="invalid_reference",
        )

    parsed_image_ref = _parse_image_reference(payload.image_reference)
    if parsed_image_ref:
        references.append(parsed_image_ref)
    return payload, references


def _multipart_create_schema(default_seconds: int) -> Dict[str, Any]:
    return {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "default": VIDEO_MODEL_ID},
            "size": {"type": "string", "default": "1792x1024"},
            "seconds": {"type": "integer", "default": default_seconds},
            "quality": {"type": "string", "default": "standard"},
            "image_reference": {
                "type": "string",
                "description": "JSON string for image_reference object",
            },
            "input_reference": {"type": "string", "format": "binary"},
        },
    }


def _build_create_response(
    *,
    model: str,
    prompt: str,
    size: str,
    seconds: int,
    quality: str,
    url: str,
) -> Dict[str, Any]:
    ts = int(time.time())
    return {
        "id": f"video_{uuid.uuid4().hex[:24]}",
        "object": "video",
        "created_at": ts,
        "completed_at": ts,
        "status": "completed",
        "model": model,
        "prompt": prompt,
        "size": size,
        "seconds": str(seconds),
        "quality": quality,
        "url": url,
    }


async def _create_video_from_payload(payload: BaseModel, references: List[str]) -> JSONResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise ValidationException(
            message="prompt is required",
            param="prompt",
            code="invalid_request_error",
        )

    model = _normalize_model(payload.model)
    size, aspect_ratio = _normalize_size(payload.size)
    quality, resolution = _normalize_quality(payload.quality)
    seconds = _normalize_seconds(payload.seconds, model=model)

    if model == XAI_VIDEO_MODEL_ID:
        if seconds > 15:
            raise ValidationException(
                message="seconds must be between 1 and 15 for model `grok-imagine-video`",
                param="seconds",
                code="invalid_seconds",
            )
        if len(references) > 1:
            raise ValidationException(
                message="`grok-imagine-video` supports at most one image reference",
                param="image_reference",
                code="invalid_reference",
            )
        manager, key_record = _select_xai_key_manager_and_record()
        service = XAIVideoService(key_manager=manager, key_record=key_record)
        direct_result = await service.generate(
            prompt=prompt,
            model=model,
            duration=seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            image_url=references[0] if references else None,
        )
        return JSONResponse(
            content=_build_create_response(
                model=model,
                prompt=prompt,
                size=size,
                seconds=int(direct_result.get("duration") or seconds),
                quality=quality,
                url=str(direct_result["url"]),
            )
        )

    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for ref in references:
        content.append({"type": "image_url", "image_url": {"url": ref}})

    result = await VideoService.completions(
        model=model,
        messages=[{"role": "user", "content": content}],
        stream=False,
        reasoning_effort=None,
        aspect_ratio=aspect_ratio,
        video_length=seconds,
        resolution=resolution,
        preset="custom",
    )

    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices:
        raise UpstreamException("Video generation failed: empty result")

    msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    rendered = msg.get("content", "") if isinstance(msg, dict) else ""
    video_url = _extract_video_url(rendered)
    if not video_url:
        raise UpstreamException("Video generation failed: missing video URL")

    return JSONResponse(
        content=_build_create_response(
            model=model,
            prompt=prompt,
            size=size,
            seconds=seconds,
            quality=quality,
            url=video_url,
        )
    )


@router.post(
    "/videos",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": VideoCreateRequest.model_json_schema()},
                "multipart/form-data": {"schema": _multipart_create_schema(6)},
            },
        }
    },
)
async def create_video(request: Request):
    """
    Videos create endpoint.
    Supports JSON and multipart/form-data using reverse-supported params only.
    """
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except ValueError:
            raise ValidationException(
                message=(
                    "Invalid JSON in request body. Please check for trailing commas or syntax errors."
                ),
                param="body",
                code="json_invalid",
            )
        if not isinstance(raw, dict):
            raise ValidationException(
                message="Request body must be a JSON object",
                param="body",
                code="invalid_request_error",
            )
        try:
            payload = VideoCreateRequest.model_validate(raw)
        except ValidationError as exc:
            _raise_validation_error(exc)
        references = await _build_references_for_json(payload)
        return await _create_video_from_payload(payload, references)

    form = await request.form()
    payload, references = await _build_payload_and_references_for_form(
        schema=VideoCreateRequest,
        prompt=form.get("prompt"),
        model=form.get("model"),
        size=form.get("size"),
        seconds=form.get("seconds"),
        quality=form.get("quality"),
        image_reference=form.get("image_reference"),
        input_reference=form.get("input_reference"),
    )
    return await _create_video_from_payload(payload, references)


@router.post("/videos/generations")
async def create_xai_video_generation(request: XAIVideoGenerationRequest):
    """Official-style xAI video generation start endpoint."""
    manager, key_record = _select_xai_key_manager_and_record()
    model = _normalize_model(request.model)
    if model != XAI_VIDEO_MODEL_ID:
        raise ValidationException(
            message=f"model must be `{XAI_VIDEO_MODEL_ID}` for /videos/generations",
            param="model",
            code="model_not_supported",
        )

    prompt = (request.prompt or "").strip()
    if not prompt:
        raise ValidationException(
            message="prompt is required",
            param="prompt",
            code="invalid_request_error",
        )

    duration = _normalize_seconds(request.duration, model=model)
    aspect_ratio = _normalize_ratio(request.aspect_ratio)
    resolution = str(request.resolution or "720p").strip()
    if resolution not in {"480p", "720p"}:
        raise ValidationException(
            message="resolution must be one of ['480p', '720p']",
            param="resolution",
            code="invalid_resolution",
        )
    image_url = _parse_xai_image_reference(request.image)

    service = XAIVideoService(key_manager=manager, key_record=key_record)
    result = await service.start_generation(
        prompt=prompt,
        model=model,
        duration=duration,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        image_url=image_url,
    )
    await _remember_xai_request_key(result.get("request_id", ""), key_record)
    return result


@router.get("/videos/{request_id}")
async def get_xai_video_generation(request_id: str):
    """Official-style xAI video generation status endpoint."""
    key_record = await _get_bound_xai_request_key(request_id)
    if not key_record:
        raise ValidationException(
            message="request_id is not available for current xAI key session",
            param="request_id",
            code="invalid_request_error",
        )
    manager = load_runtime_manager()
    service = XAIVideoService(key_manager=manager, key_record=key_record)
    return await service.get_generation(request_id)


__all__ = [
    "router",
    "create_video",
    "create_xai_video_generation",
    "get_xai_video_generation",
    "XAIVideoGenerationRequest",
]
