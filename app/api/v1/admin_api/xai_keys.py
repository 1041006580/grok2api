from fastapi import APIRouter, Depends

from app.core.auth import verify_app_key

router = APIRouter()


@router.get("/xai-keys", dependencies=[Depends(verify_app_key)])
async def list_xai_keys():
    """Placeholder route for listing xAI keys."""
    return {"status": "success", "keys": []}


@router.get("/xai-keys/{key_id}", dependencies=[Depends(verify_app_key)])
async def get_xai_key(key_id: str):
    """Placeholder route for fetching a single xAI key by ID."""
    return {"status": "success", "key": {"id": key_id}}
