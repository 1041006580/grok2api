"""
Grok 模型管理服务
"""

from enum import Enum
from typing import Optional, Tuple, List
from pydantic import BaseModel, Field

from app.core.exceptions import ValidationException


class Tier(str, Enum):
    """模型档位"""

    BASIC = "basic"
    SUPER = "super"
    HEAVY = "heavy"


class Cost(str, Enum):
    """计费类型"""

    LOW = "low"
    HIGH = "high"


class ModelInfo(BaseModel):
    """模型信息"""

    model_id: str
    grok_model: str
    model_mode: str
    tier: Tier = Field(default=Tier.BASIC)
    cost: Cost = Field(default=Cost.LOW)
    display_name: str
    description: str = ""
    is_image: bool = False
    is_image_edit: bool = False
    is_video: bool = False


class ModelService:
    """模型管理服务"""

    MODELS = [
        ModelInfo(
            model_id="grok-3",
            grok_model="grok-3",
            model_mode="MODEL_MODE_GROK_3",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-3",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-3-mini",
            grok_model="grok-3",
            model_mode="MODEL_MODE_GROK_3_MINI_THINKING",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-3-MINI",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-3-thinking",
            grok_model="grok-3",
            model_mode="MODEL_MODE_GROK_3_THINKING",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-3-THINKING",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4",
            grok_model="grok-4",
            model_mode="MODEL_MODE_GROK_4",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4-thinking",
            grok_model="grok-4",
            model_mode="MODEL_MODE_GROK_4_THINKING",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4-THINKING",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4-heavy",
            grok_model="grok-4",
            model_mode="MODEL_MODE_HEAVY",
            tier=Tier.HEAVY,
            cost=Cost.HIGH,
            display_name="GROK-4-HEAVY",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.1-mini",
            grok_model="grok-4-1-thinking-1129",
            model_mode="MODEL_MODE_GROK_4_1_MINI_THINKING",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4.1-MINI",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.1-fast",
            grok_model="grok-4-1-thinking-1129",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4.1-FAST",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.1-expert",
            grok_model="grok-4-1-thinking-1129",
            model_mode="MODEL_MODE_EXPERT",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="GROK-4.1-EXPERT",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.1-thinking",
            grok_model="grok-4-1-thinking-1129",
            model_mode="MODEL_MODE_GROK_4_1_THINKING",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="GROK-4.1-THINKING",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.1-non-thinking-companion",
            grok_model="grok-4-1-non-thinking-companion",
            model_mode="MODEL_MODE_UNKNOWN",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4.1-NON-THINKING-COMPANION",
            description="Companion model",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.20-beta",
            grok_model="grok-420",
            model_mode="MODEL_MODE_GROK_420",
            tier=Tier.BASIC,
            cost=Cost.LOW,
            display_name="GROK-4.20-BETA",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-4.3-beta",
            grok_model="grok-420-computer-use-sa",
            model_mode="grok-420-computer-use-sa",
            tier=Tier.SUPER,
            cost=Cost.LOW,
            display_name="GROK-4.3-BETA",
            description="Grok 4.3 Beta (Super+ only)",
            is_image=False,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-imagine-1.0-fast",
            grok_model="grok-3",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="Grok Image Fast",
            description="Imagine waterfall image generation model for chat completions",
            is_image=True,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-imagine-1.0",
            grok_model="grok-3",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="Grok Image",
            description="Image generation model",
            is_image=True,
            is_image_edit=False,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-imagine-1.0-edit",
            grok_model="imagine-image-edit",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="Grok Image Edit",
            description="Image edit model",
            is_image=False,
            is_image_edit=True,
            is_video=False,
        ),
        ModelInfo(
            model_id="grok-imagine-1.0-video",
            grok_model="grok-3",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.BASIC,
            cost=Cost.HIGH,
            display_name="Grok Video",
            description="Video generation model",
            is_image=False,
            is_image_edit=False,
            is_video=True,
        ),
        ModelInfo(
            model_id="grok-imagine-1.0-video-super",
            grok_model="grok-3",
            model_mode="MODEL_MODE_FAST",
            tier=Tier.SUPER,
            cost=Cost.HIGH,
            display_name="Grok Video Super",
            description="Video generation model (10-15s, super token)",
            is_image=False,
            is_image_edit=False,
            is_video=True,
        ),
    ]

    _map = {m.model_id: m for m in MODELS}

    @classmethod
    def get(cls, model_id: str) -> Optional[ModelInfo]:
        """获取模型信息"""
        return cls._map.get(model_id)

    @classmethod
    def list(cls) -> list[ModelInfo]:
        """获取所有模型"""
        return list(cls._map.values())

    @classmethod
    def valid(cls, model_id: str) -> bool:
        """模型是否有效"""
        return model_id in cls._map

    @classmethod
    def to_grok(cls, model_id: str) -> Tuple[str, str]:
        """转换为 Grok 参数"""
        model = cls.get(model_id)
        if not model:
            raise ValidationException(f"Invalid model ID: {model_id}")
        return model.grok_model, model.model_mode

    @classmethod
    def rate_limit_model_name(cls, model_id: str) -> str:
        """Resolve the modelName used for /rest/rate-limits."""
        model = cls.get(model_id)
        if not model:
            raise ValidationException(f"Invalid model ID: {model_id}")
        return model.grok_model

    @classmethod
    def quota_mode_for_model(cls, model_id: str) -> str:
        """
        Resolve the quota bucket (mode) charged by Grok upstream for this model.

        Returned values match POOL_SYNC_MODES entries:
        - "fast"    : MODEL_MODE_FAST and basic-tier default
        - "expert"  : MODEL_MODE_EXPERT
        - "heavy"   : MODEL_MODE_HEAVY
        - "auto"    : default for super/heavy-tier non-fast/expert/heavy models
        - "grok-420-computer-use-sa" : the Grok 4.3 Beta dedicated bucket
        """
        model = cls.get(model_id)
        if not model:
            return "fast"

        # Grok 4.3 Beta has its own dedicated bucket
        if model.grok_model == "grok-420-computer-use-sa":
            return "grok-420-computer-use-sa"

        mode = (model.model_mode or "").upper()
        if "HEAVY" in mode:
            return "heavy"
        if "EXPERT" in mode:
            return "expert"
        if "FAST" in mode:
            return "fast"

        # Non-fast/expert/heavy models on super/heavy tier consume the "auto" bucket
        if model.tier in {Tier.SUPER, Tier.HEAVY}:
            return "auto"
        # Basic tier only tracks "fast" in POOL_SYNC_MODES
        return "fast"

    @classmethod
    def pool_for_model(cls, model_id: str) -> str:
        """根据模型选择 Token 池"""
        model = cls.get(model_id)
        if model and model.tier == Tier.HEAVY:
            return "ssoHeavy"
        if model and model.tier == Tier.SUPER:
            return "ssoSuper"
        return "ssoBasic"

    @classmethod
    def pool_candidates_for_model(cls, model_id: str) -> List[str]:
        """按优先级返回可用 Token 池列表"""
        model = cls.get(model_id)
        if model and model.tier == Tier.HEAVY:
            return ["ssoHeavy"]
        if model and model.tier == Tier.SUPER:
            return ["ssoSuper", "ssoHeavy"]
        return ["ssoBasic", "ssoSuper", "ssoHeavy"]


__all__ = ["ModelService"]
