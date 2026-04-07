from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_app_key

router = APIRouter()


@router.get("/xai-keys", dependencies=[Depends(verify_app_key)])
async def list_xai_keys():
    """Placeholder route for listing xAI keys."""
    raise HTTPException(status_code=501, detail="xAI Keys admin API is not implemented yet")


@router.get("/xai-keys/{key_id}", dependencies=[Depends(verify_app_key)])
async def get_xai_key(key_id: str):
    """Placeholder route for fetching a single xAI key by ID."""
    raise HTTPException(status_code=501, detail=f"xAI Key `{key_id}` lookup is not implemented yet")
