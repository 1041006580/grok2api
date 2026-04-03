"""
Models API 路由
"""

from fastapi import APIRouter, HTTPException

from app.services.grok.services.model import ModelService


router = APIRouter(tags=["Models"])


def _serialize_model(model):
    capabilities = []
    if model.is_image:
        capabilities.append("image_generation")
    if model.is_image_edit:
        capabilities.append("image_edit")
    if model.is_video:
        capabilities.append("video_generation")
    if not capabilities:
        capabilities.append("text_generation")

    return {
        "id": model.model_id,
        "object": "model",
        "created": 0,
        "owned_by": "grok2api@1041006580",
        "display_name": model.display_name,
        "description": model.description,
        "tier": model.tier.value,
        "cost": model.cost.value,
        "capabilities": capabilities,
    }


def _list_models_with_filter(predicate):
    data = [_serialize_model(m) for m in ModelService.list() if predicate(m)]
    return {"object": "list", "data": data}


def _get_model_or_404(model_id: str):
    model = ModelService.get(model_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model `{model_id}` not found")
    return model


@router.get("/models")
async def list_models():
    """OpenAI 兼容 models 列表接口"""
    return _list_models_with_filter(lambda _model: True)


@router.get("/models/{model_id}")
def retrieve_model(model_id: str):
    return _serialize_model(_get_model_or_404(model_id))


@router.get("/language-models")
def list_language_models():
    return _list_models_with_filter(
        lambda model: not model.is_image and not model.is_image_edit and not model.is_video
    )


@router.get("/language-models/{model_id}")
def retrieve_language_model(model_id: str):
    model = _get_model_or_404(model_id)
    if model.is_image or model.is_image_edit or model.is_video:
        raise HTTPException(status_code=404, detail=f"Language model `{model_id}` not found")
    return _serialize_model(model)


@router.get("/image-generation-models")
def list_image_generation_models():
    return _list_models_with_filter(lambda model: model.is_image)


@router.get("/image-generation-models/{model_id}")
def retrieve_image_generation_model(model_id: str):
    model = _get_model_or_404(model_id)
    if not model.is_image:
        raise HTTPException(
            status_code=404, detail=f"Image generation model `{model_id}` not found"
        )
    return _serialize_model(model)


__all__ = ["router"]
