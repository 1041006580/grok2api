"""
流式响应通用工具
"""

from typing import AsyncGenerator

from app.core.config import get_config, feature_enabled
from app.core.logger import logger
from app.core.mask import mask_token_for_log
from app.services.grok.services.model import ModelService
from app.services.token import EffortType


async def wrap_stream_with_usage(
    stream: AsyncGenerator, token_mgr, token: str, model: str
) -> AsyncGenerator:
    """
    包装流式响应，在完成时记录使用并释放 inflight 标记

    Args:
        stream: 原始 AsyncGenerator
        token_mgr: TokenManager 实例
        token: Token 字符串
        model: 模型名称
    """
    success = False
    try:
        async for chunk in stream:
            yield chunk
        success = True
    finally:
        if success:
            try:
                model_info = ModelService.get(model)
                effort = (
                    EffortType.HIGH
                    if (model_info and model_info.cost.value == "high")
                    else EffortType.LOW
                )
                mode = ModelService.quota_mode_for_model(model)
                await token_mgr.consume(token, effort, mode=mode)
                logger.debug(
                    f"Stream completed, recorded usage for token {mask_token_for_log(token)} (effort={effort.value}, mode={mode})"
                )
            except Exception as e:
                logger.warning(f"Failed to record stream usage: {e}")
        # 释放 inflight 标记（对未 acquire 的 token 是 no-op）
        try:
            if feature_enabled("token.inflight_enabled", False):
                token_mgr.release_token(token)
        except Exception:
            pass


__all__ = ["wrap_stream_with_usage"]
