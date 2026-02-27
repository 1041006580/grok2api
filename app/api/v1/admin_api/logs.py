"""Admin logs API routes."""

from fastapi import APIRouter, Depends

from app.core.auth import verify_app_key

router = APIRouter()


@router.get("/logs", dependencies=[Depends(verify_app_key)])
async def get_logs_api(limit: int = 1000):
    """获取请求日志"""
    from app.services.request_logger import request_logger

    logs = await request_logger.get_logs(limit)
    return {"status": "success", "logs": logs, "total": len(logs)}


@router.post("/logs/clear", dependencies=[Depends(verify_app_key)])
async def clear_logs_api():
    """清空请求日志"""
    from app.services.request_logger import request_logger

    await request_logger.clear_logs()
    return {"status": "success", "message": "日志已清空"}
