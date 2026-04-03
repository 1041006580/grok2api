from fastapi import HTTPException


def test_models_retrieve_single_model():
    from app.api.v1.models import retrieve_model

    result = retrieve_model("grok-4")

    assert result["id"] == "grok-4"
    assert result["object"] == "model"
    assert result["owned_by"] == "grok2api@1041006580"


def test_models_retrieve_missing_model_raises_404():
    from app.api.v1.models import retrieve_model

    try:
        retrieve_model("missing-model")
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 404


def test_language_models_only_return_language_capable_models():
    from app.api.v1.models import list_language_models

    result = list_language_models()

    ids = {item["id"] for item in result["data"]}
    assert "grok-4" in ids
    assert "grok-imagine-1.0" not in ids
    assert "grok-imagine-1.0-video" not in ids


def test_image_generation_models_only_return_image_generation_models():
    from app.api.v1.models import list_image_generation_models

    result = list_image_generation_models()

    ids = {item["id"] for item in result["data"]}
    assert "grok-imagine-1.0" in ids
    assert "grok-imagine-1.0-fast" in ids
    assert "grok-imagine-1.0-edit" not in ids
    assert "grok-4" not in ids
