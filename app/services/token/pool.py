"""Token 池管理"""

import random
import time
from typing import Dict, List, Optional, Iterator, Set

from app.services.token.models import TokenInfo, TokenStatus, TokenPoolStats
from app.core.config import get_config, feature_enabled

DEFAULT_INFLIGHT_TIMEOUT_SEC = 120


class TokenPool:
    """Token 池（管理一组 Token）"""

    def __init__(self, name: str):
        self.name = name
        self._tokens: Dict[str, TokenInfo] = {}
        self._inflight: Dict[str, List[float]] = {}

    def add(self, token: TokenInfo):
        """添加 Token"""
        self._tokens[token.token] = token

    def remove(self, token_str: str) -> bool:
        """删除 Token"""
        if token_str in self._tokens:
            del self._tokens[token_str]
            self._inflight.pop(token_str, None)
            return True
        return False

    def get(self, token_str: str) -> Optional[TokenInfo]:
        """获取 Token"""
        return self._tokens.get(token_str)

    def _get_inflight_timeout(self) -> float:
        try:
            val = get_config("token.inflight_timeout_sec", DEFAULT_INFLIGHT_TIMEOUT_SEC)
            return float(val)
        except Exception:
            return float(DEFAULT_INFLIGHT_TIMEOUT_SEC)

    def _prune_inflight(self, token_str: str) -> int:
        """清理过期 inflight 条目，返回剩余有效计数"""
        entries = self._inflight.get(token_str)
        if not entries:
            return 0
        timeout = self._get_inflight_timeout()
        cutoff = time.monotonic() - timeout
        alive = [t for t in entries if t > cutoff]
        if alive:
            self._inflight[token_str] = alive
        else:
            del self._inflight[token_str]
        return len(alive)

    def acquire(self, token_str: str) -> bool:
        """标记 token 为 in-flight（请求发出前调用）"""
        if token_str not in self._tokens:
            return False
        if token_str not in self._inflight:
            self._inflight[token_str] = []
        self._inflight[token_str].append(time.monotonic())
        return True

    def release(self, token_str: str):
        """释放 in-flight 标记（请求完成后调用，移除最早的一条）"""
        entries = self._inflight.get(token_str)
        if not entries:
            return
        entries.pop(0)
        if not entries:
            del self._inflight[token_str]

    def get_inflight(self, token_str: str) -> int:
        """获取当前有效 in-flight 计数（自动清理超时条目）"""
        return self._prune_inflight(token_str)

    def _is_consumed_mode(self) -> bool:
        try:
            return feature_enabled("token.consumed_mode_enabled", False)
        except Exception:
            return False

    def _is_inflight_enabled(self) -> bool:
        try:
            return feature_enabled("token.inflight_enabled", False)
        except Exception:
            return False

    def _is_multi_mode(self) -> bool:
        try:
            return feature_enabled("token.multi_mode_quota_enabled", False)
        except Exception:
            return False

    def _effective_quota(self, t: TokenInfo, mode: Optional[str]) -> int:
        """返回 mode-aware 有效配额；multi_mode 关或 mode 为空时回落到 legacy"""
        if mode and self._is_multi_mode():
            return t.get_effective_quota(mode)
        return t.quota

    def select(
        self,
        exclude: set = None,
        prefer_tags: Optional[Set[str]] = None,
        mode: Optional[str] = None,
    ) -> Optional[TokenInfo]:
        """
        选择一个可用 Token

        三种模式（按 config 决定）：
        - inflight_enabled: 评分选择（health + quota - inflight - fails - recent）
        - consumed_mode: 选 consumed 最少的
        - 默认: 选 quota 最多的

        当 mode 指定且 multi_mode_quota_enabled 时，按 quotas[mode].remaining 选择。
        """
        available = [
            t for t in self._tokens.values()
            if t.status == TokenStatus.ACTIVE
            and (not exclude or t.token not in exclude)
        ]
        if not available:
            return None

        if prefer_tags:
            preferred = [t for t in available if prefer_tags.issubset(set(t.tags or []))]
            if preferred:
                available = preferred

        # mode-aware 过滤：排除该 mode 配额已耗尽的 token；全员零则退回 legacy 排序
        effective_mode = mode
        if mode and self._is_multi_mode():
            with_quota = [t for t in available if self._effective_quota(t, mode) > 0]
            if with_quota:
                available = with_quota
            else:
                effective_mode = None  # 全员该 mode 耗尽 -> 用 legacy quota 选「最不糟」

        if self._is_inflight_enabled():
            return self._select_by_score(available, effective_mode)

        if self._is_consumed_mode():
            return self._select_by_consumed(available)

        return self._select_by_quota(available, effective_mode)

    def _select_by_score(self, available: List[TokenInfo], mode: Optional[str] = None) -> Optional[TokenInfo]:
        """评分选择：health*100 + quota*25 - inflight*20 - fails*4 - recent_penalty"""
        now_ms = int(time.time() * 1000)

        def _score(t: TokenInfo) -> float:
            inflight = self.get_inflight(t.token)
            health = 1.0 if t.status == TokenStatus.ACTIVE else 0.5
            quota = self._effective_quota(t, mode)
            score = health * 100.0 + quota * 25.0 - inflight * 20.0 - min(t.fail_count, 10) * 4.0
            if t.last_used_at:
                age_s = (now_ms - t.last_used_at) / 1000.0
                if age_s < 15:
                    score -= (1.0 - age_s / 15.0) * 15.0
            return score

        available.sort(key=_score, reverse=True)
        top_score = _score(available[0])
        candidates = [t for t in available if _score(t) >= top_score - 5.0]
        return random.choice(candidates)

    def _select_by_consumed(self, available: List[TokenInfo]) -> Optional[TokenInfo]:
        """consumed 模式：选消耗最少的"""
        new_logic = [t for t in available if t.consumed > 0]
        old_logic = [t for t in available if t.consumed == 0 and t.quota > 0]

        if new_logic:
            available = new_logic
        elif old_logic:
            available = old_logic
        else:
            return None

        min_consumed = min(t.consumed for t in available)
        candidates = [t for t in available if t.consumed == min_consumed]
        return random.choice(candidates)

    def _select_by_quota(self, available: List[TokenInfo], mode: Optional[str] = None) -> Optional[TokenInfo]:
        """默认模式：选有效配额最多的"""
        max_quota = max(self._effective_quota(t, mode) for t in available)
        candidates = [t for t in available if self._effective_quota(t, mode) == max_quota]
        return random.choice(candidates)

    def count(self) -> int:
        """Token 数量"""
        return len(self._tokens)

    def list(self) -> List[TokenInfo]:
        """获取所有 Token"""
        return list(self._tokens.values())

    def get_stats(self) -> TokenPoolStats:
        """获取池统计信息"""
        stats = TokenPoolStats(total=len(self._tokens))

        for token in self._tokens.values():
            stats.total_quota += token.quota
            stats.total_consumed += token.consumed

            if token.status == TokenStatus.ACTIVE:
                stats.active += 1
            elif token.status == TokenStatus.DISABLED:
                stats.disabled += 1
            elif token.status == TokenStatus.EXPIRED:
                stats.expired += 1
            elif token.status == TokenStatus.COOLING:
                stats.cooling += 1

        if stats.total > 0:
            stats.avg_quota = stats.total_quota / stats.total
            stats.avg_consumed = stats.total_consumed / stats.total

        stats.total_inflight = sum(
            self.get_inflight(t.token) for t in self._tokens.values()
        )
        return stats

    def cleanup_stale_inflight(self) -> int:
        """全量清理过期 inflight 条目，返回清除数"""
        cleaned = 0
        timeout = self._get_inflight_timeout()
        cutoff = time.monotonic() - timeout
        for token_str in list(self._inflight.keys()):
            entries = self._inflight.get(token_str) or []
            alive = [t for t in entries if t > cutoff]
            cleaned += len(entries) - len(alive)
            if alive:
                self._inflight[token_str] = alive
            else:
                self._inflight.pop(token_str, None)
        return cleaned

    def _rebuild_index(self):
        """重建索引（预留接口，用于加载时调用）"""
        pass

    def __iter__(self) -> Iterator[TokenInfo]:
        return iter(self._tokens.values())


__all__ = ["TokenPool"]
