"""
Token 数据模型

额度规则:
- Basic 新号默认 80 配额
- Super 新号默认 140 配额
- 重置后恢复默认值
- lowEffort 扣 1，highEffort 扣 4
"""

from enum import Enum
from typing import Optional, List, Dict
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


# 默认配额
BASIC__DEFAULT_QUOTA = 80
SUPER_DEFAULT_QUOTA = 140
HEAVY_DEFAULT_QUOTA = 150

# 失败阈值
FAIL_THRESHOLD = 5


class TokenStatus(str, Enum):
    """Token 状态"""

    ACTIVE = "active"
    DISABLED = "disabled"
    EXPIRED = "expired"
    COOLING = "cooling"


class EffortType(str, Enum):
    """请求消耗类型"""

    LOW = "low"  # 扣 1
    HIGH = "high"  # 扣 4


class ModeId(str, Enum):
    """Grok rate-limits API 的 modelName 枚举"""

    AUTO = "auto"
    FAST = "fast"
    EXPERT = "expert"
    HEAVY = "heavy"
    GROK_4_3 = "grok-420-computer-use-sa"


# 池层级 -> 主 mode 映射（用于 legacy quota 字段双写）
PRIMARY_MODE_BY_TIER: Dict[str, str] = {
    "basic": ModeId.FAST.value,
    "super": ModeId.AUTO.value,
    "heavy": ModeId.AUTO.value,
}


EFFORT_COST = {
    EffortType.LOW: 1,
    EffortType.HIGH: 4,
}


class QuotaWindow(BaseModel):
    """单个 mode 的配额窗口"""

    remaining: int = 0
    total: int = 0
    window_seconds: int = 7200
    last_sync_at: Optional[int] = None


class TokenInfo(BaseModel):
    """Token 信息"""

    token: str
    status: TokenStatus = TokenStatus.ACTIVE
    quota: int = BASIC__DEFAULT_QUOTA

    # 消耗记录（本地累加，不依赖 API 返回值）
    # 仅在 consumed_mode_enabled=true 时使用
    consumed: int = 0

    # 多 mode 配额窗口（可选，multi_mode_quota_enabled=true 时使用）
    quotas: Optional[Dict[str, QuotaWindow]] = None

    # 统计
    created_at: int = Field(
        default_factory=lambda: int(datetime.now().timestamp() * 1000)
    )
    last_used_at: Optional[int] = None
    use_count: int = 0

    # 失败追踪
    fail_count: int = 0
    last_fail_at: Optional[int] = None
    last_fail_reason: Optional[str] = None

    # 冷却管理
    last_sync_at: Optional[int] = None  # 上次同步时间

    # 扩展
    tags: List[str] = Field(default_factory=list)
    note: str = ""
    last_asset_clear_at: Optional[int] = None

    @field_validator("token", mode="before")
    @classmethod
    def _normalize_token(cls, value):
        """Normalize copied tokens to avoid unicode punctuation and hidden spaces."""
        if value is None:
            raise ValueError("token cannot be empty")
        token = str(value)
        token = token.translate(
            str.maketrans(
                {
                    "\u2010": "-",
                    "\u2011": "-",
                    "\u2012": "-",
                    "\u2013": "-",
                    "\u2014": "-",
                    "\u2212": "-",
                    "\u00a0": " ",
                    "\u2007": " ",
                    "\u202f": " ",
                    "\u200b": "",
                    "\u200c": "",
                    "\u200d": "",
                    "\ufeff": "",
                }
            )
        )
        token = "".join(token.split())
        if token.startswith("sso="):
            token = token[4:]
        token = token.encode("ascii", errors="ignore").decode("ascii")
        if not token:
            raise ValueError("token cannot be empty")
        return token

    def is_available(self) -> bool:
        """检查是否可用（仅由显式状态控制）"""
        return self.status == TokenStatus.ACTIVE

    def consume(self, effort: EffortType = EffortType.LOW) -> int:
        """
        消耗配额（默认：扣减 quota）

        Args:
            effort: LOW 扣 1 配额并计 1 次，HIGH 扣 4 配额并计 4 次

        Returns:
            实际扣除的配额
        """
        cost = EFFORT_COST[effort]
        actual_cost = min(cost, self.quota)

        self.last_used_at = int(datetime.now().timestamp() * 1000)
        self.consumed += cost
        self.use_count += actual_cost
        self.quota = max(0, self.quota - actual_cost)

        return actual_cost

    def consume_with_consumed(self, effort: EffortType = EffortType.LOW) -> int:
        """
        消耗配额（consumed 模式：累加 consumed 而非扣减 quota）

        Args:
            effort: LOW 计 1 次，HIGH 计 4 次

        Returns:
            实际计入的消耗次数
        """
        cost = EFFORT_COST[effort]

        self.consumed += cost
        self.last_used_at = int(datetime.now().timestamp() * 1000)
        self.use_count += 1

        if self.status == TokenStatus.COOLING:
            self.status = TokenStatus.ACTIVE

        return cost

    def update_quota(self, new_quota: int):
        """
        更新配额（用于 API 同步 - 默认模式）

        Args:
            new_quota: 新的配额值
        """
        self.quota = max(0, new_quota)

        if self.quota > 0 and self.status in [
            TokenStatus.COOLING,
            TokenStatus.EXPIRED,
        ]:
            self.status = TokenStatus.ACTIVE

    def update_quota_with_consumed(self, new_quota: int):
        """
        更新配额（consumed 模式）

        Args:
            new_quota: 新的配额值
        """
        self.quota = max(0, new_quota)

    def reset(self, default_quota: Optional[int] = None):
        """重置配额到默认值"""
        quota = BASIC__DEFAULT_QUOTA if default_quota is None else default_quota
        self.quota = max(0, int(quota))
        self.status = TokenStatus.ACTIVE
        self.fail_count = 0
        self.last_fail_reason = None
        self.consumed = 0

    def record_fail(
        self,
        status_code: int = 401,
        reason: str = "",
        threshold: Optional[int] = None,
    ):
        """记录失败，达到阈值后自动标记为 expired"""
        # 仅 401 计入失败
        if status_code != 401:
            return

        self.fail_count += 1
        self.last_fail_at = int(datetime.now().timestamp() * 1000)
        self.last_fail_reason = reason

        limit = FAIL_THRESHOLD if threshold is None else threshold
        if self.fail_count >= limit:
            self.status = TokenStatus.EXPIRED

    def record_success(self, is_usage: bool = True):
        """记录成功，清空失败计数并根据配额更新状态"""
        self.fail_count = 0
        self.last_fail_at = None
        self.last_fail_reason = None

        if is_usage:
            self.use_count += 1
            self.last_used_at = int(datetime.now().timestamp() * 1000)

    def need_refresh(self, interval_hours: int = 8) -> bool:
        """检查是否需要刷新配额"""
        if self.status != TokenStatus.COOLING:
            return False

        if self.last_sync_at is None:
            return True

        now = int(datetime.now().timestamp() * 1000)
        interval_ms = interval_hours * 3600 * 1000
        return (now - self.last_sync_at) >= interval_ms

    def mark_synced(self):
        """标记已同步"""
        self.last_sync_at = int(datetime.now().timestamp() * 1000)

    def should_cool_down(self, remaining_tokens: int, threshold: int = 10) -> bool:
        """根据 Rate Limits 返回值判断是否应该冷却"""
        if remaining_tokens <= threshold:
            self.status = TokenStatus.COOLING
            return True
        return False

    def get_effective_quota(self, mode: str = "fast") -> int:
        """获取指定 mode 的剩余配额；若 quotas 不存在则 fallback 到 legacy quota"""
        if self.quotas and mode in self.quotas:
            return self.quotas[mode].remaining
        return self.quota

    def ensure_quotas(self, pool_tier: str = "basic"):
        """懒初始化 quotas 字典（从 legacy quota 字段迁移）。

        Args:
            pool_tier: 池层级 (basic/super/heavy)，决定 primary mode
        """
        if self.quotas is not None:
            return
        primary_mode = PRIMARY_MODE_BY_TIER.get(pool_tier, ModeId.FAST.value)
        self.quotas = {
            primary_mode: QuotaWindow(
                remaining=self.quota,
                total=self.quota,
                last_sync_at=self.last_sync_at,
            )
        }

    def sync_legacy_quota(self, pool_tier: str = "basic"):
        """从 quotas 中的 primary mode 同步到 legacy quota 字段（双写保持一致）"""
        if not self.quotas:
            return
        primary_mode = PRIMARY_MODE_BY_TIER.get(pool_tier, ModeId.FAST.value)
        if primary_mode in self.quotas:
            self.quota = max(0, self.quotas[primary_mode].remaining)


class TokenPoolStats(BaseModel):
    """Token 池统计"""

    total: int = 0
    active: int = 0
    disabled: int = 0
    expired: int = 0
    cooling: int = 0
    total_quota: int = 0
    avg_quota: float = 0.0
    total_consumed: int = 0
    avg_consumed: float = 0.0
    total_inflight: int = 0


__all__ = [
    "TokenStatus",
    "TokenInfo",
    "TokenPoolStats",
    "EffortType",
    "EFFORT_COST",
    "BASIC__DEFAULT_QUOTA",
    "SUPER_DEFAULT_QUOTA",
    "HEAVY_DEFAULT_QUOTA",
    "FAIL_THRESHOLD",
    "QuotaWindow",
    "ModeId",
    "PRIMARY_MODE_BY_TIER",
]
