"""Tests for multi-mode quota tracking and inflight timeout cleanup."""

import asyncio
import time
from unittest.mock import patch, AsyncMock

from app.core.config import config
from app.services.token.models import (
    TokenInfo,
    TokenStatus,
    EffortType,
    QuotaWindow,
)
from app.services.token.pool import TokenPool
from app.services.grok.services.model import ModelService


# ==================== quota_mode_for_model ====================


def test_quota_mode_fast_for_basic_models():
    assert ModelService.quota_mode_for_model("grok-3") == "fast"
    assert ModelService.quota_mode_for_model("grok-4") == "fast"
    assert ModelService.quota_mode_for_model("grok-4-thinking") == "fast"
    assert ModelService.quota_mode_for_model("grok-4.1-fast") == "fast"


def test_quota_mode_expert():
    assert ModelService.quota_mode_for_model("grok-4.1-expert") == "expert"


def test_quota_mode_heavy():
    assert ModelService.quota_mode_for_model("grok-4-heavy") == "heavy"


def test_quota_mode_auto_for_super_tier():
    assert ModelService.quota_mode_for_model("grok-4.20-beta") == "fast"


def test_quota_mode_grok_4_3_beta():
    assert ModelService.quota_mode_for_model("grok-4.3-beta") == "grok-420-computer-use-sa"


def test_quota_mode_unknown_model_returns_fast():
    assert ModelService.quota_mode_for_model("nonexistent-model") == "fast"


# ==================== consume with mode ====================


def test_consume_deducts_mode_quota_when_multi_mode_enabled():
    config._config = {"token": {"multi_mode_quota_enabled": True, "consumed_mode_enabled": False}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    token = TokenInfo(token="abc", quota=100, quotas={
        "auto": QuotaWindow(remaining=50, total=140),
        "fast": QuotaWindow(remaining=30, total=140),
    })
    pool.add(token)
    mgr.pools = {"ssoSuper": pool}

    asyncio.run(mgr.consume("abc", EffortType.LOW, mode="auto"))

    assert token.quotas["auto"].remaining == 49
    assert token.quotas["fast"].remaining == 30


def test_consume_skips_mode_deduction_when_mode_not_in_quotas():
    config._config = {"token": {"multi_mode_quota_enabled": True, "consumed_mode_enabled": False}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoBasic")
    token = TokenInfo(token="abc", quota=80, quotas={
        "fast": QuotaWindow(remaining=80, total=80),
    })
    pool.add(token)
    mgr.pools = {"ssoBasic": pool}

    asyncio.run(mgr.consume("abc", EffortType.HIGH, mode="expert"))

    # expert not in quotas, so only legacy quota deducted
    assert token.quotas["fast"].remaining == 80
    assert token.quota <= 76  # legacy deducted 4


def test_consume_high_effort_deducts_4_from_mode():
    config._config = {"token": {"multi_mode_quota_enabled": True, "consumed_mode_enabled": False}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoHeavy")
    token = TokenInfo(token="abc", quota=150, quotas={
        "heavy": QuotaWindow(remaining=20, total=150),
    })
    pool.add(token)
    mgr.pools = {"ssoHeavy": pool}

    asyncio.run(mgr.consume("abc", EffortType.HIGH, mode="heavy"))

    assert token.quotas["heavy"].remaining == 16


# ==================== inflight timeout cleanup ====================


def test_inflight_timeout_prunes_stale_entries():
    config._config = {"token": {"inflight_timeout_sec": 1}}

    pool = TokenPool("ssoBasic")
    token = TokenInfo(token="t1", quota=80)
    pool.add(token)

    pool.acquire("t1")
    pool.acquire("t1")

    # Manually backdate one entry
    pool._inflight["t1"][0] = time.monotonic() - 10

    count = pool.get_inflight("t1")
    assert count == 1  # one expired, one alive


def test_inflight_release_removes_oldest():
    config._config = {"token": {"inflight_timeout_sec": 300}}

    pool = TokenPool("ssoBasic")
    token = TokenInfo(token="t1", quota=80)
    pool.add(token)

    pool.acquire("t1")
    pool.acquire("t1")
    pool.release("t1")

    assert pool.get_inflight("t1") == 1


def test_inflight_cleanup_stale_batch():
    config._config = {"token": {"inflight_timeout_sec": 1}}

    pool = TokenPool("ssoBasic")
    t1 = TokenInfo(token="t1", quota=80)
    t2 = TokenInfo(token="t2", quota=80)
    pool.add(t1)
    pool.add(t2)

    pool.acquire("t1")
    pool.acquire("t2")
    pool.acquire("t2")

    # Backdate all
    now = time.monotonic()
    pool._inflight["t1"] = [now - 10]
    pool._inflight["t2"] = [now - 10, now - 5]

    cleaned = pool.cleanup_stale_inflight()
    assert cleaned == 3
    assert pool.get_inflight("t1") == 0
    assert pool.get_inflight("t2") == 0


def test_select_by_score_penalizes_inflight():
    config._config = {"token": {"inflight_enabled": True, "inflight_timeout_sec": 300}}

    pool = TokenPool("ssoBasic")
    t1 = TokenInfo(token="t1", quota=80)
    t2 = TokenInfo(token="t2", quota=80)
    pool.add(t1)
    pool.add(t2)

    pool.acquire("t1")
    pool.acquire("t1")
    pool.acquire("t1")

    # t2 has no inflight, should be preferred
    selected = pool.select()
    assert selected.token == "t2"


# ==================== mode-aware selection ====================


def test_select_by_quota_uses_mode_remaining_when_multi_mode_enabled():
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    pool = TokenPool("ssoSuper")
    # t1 legacy quota high but expert exhausted
    t1 = TokenInfo(token="t1", quota=140, quotas={
        "auto": QuotaWindow(remaining=140),
        "expert": QuotaWindow(remaining=0),
    })
    # t2 legacy quota lower but expert healthy
    t2 = TokenInfo(token="t2", quota=50, quotas={
        "auto": QuotaWindow(remaining=50),
        "expert": QuotaWindow(remaining=30),
    })
    pool.add(t1)
    pool.add(t2)

    selected = pool.select(mode="expert")
    assert selected.token == "t2"


def test_select_by_quota_falls_back_to_legacy_when_multi_mode_off():
    config._config = {"token": {"multi_mode_quota_enabled": False}}

    pool = TokenPool("ssoSuper")
    t1 = TokenInfo(token="t1", quota=140, quotas={
        "expert": QuotaWindow(remaining=0),
    })
    t2 = TokenInfo(token="t2", quota=50, quotas={
        "expert": QuotaWindow(remaining=30),
    })
    pool.add(t1)
    pool.add(t2)

    # multi_mode off -> legacy quota wins -> t1
    selected = pool.select(mode="expert")
    assert selected.token == "t1"


def test_select_filters_zero_mode_quota_when_multi_mode_enabled():
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    pool = TokenPool("ssoSuper")
    # both have legacy quota, but only t2 has expert remaining
    t1 = TokenInfo(token="t1", quota=140, quotas={"expert": QuotaWindow(remaining=0)})
    t2 = TokenInfo(token="t2", quota=10, quotas={"expert": QuotaWindow(remaining=5)})
    pool.add(t1)
    pool.add(t2)

    selected = pool.select(mode="expert")
    assert selected.token == "t2"


def test_select_no_mode_filter_falls_back_to_all_when_all_zero():
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    pool = TokenPool("ssoSuper")
    t1 = TokenInfo(token="t1", quota=140, quotas={"expert": QuotaWindow(remaining=0)})
    t2 = TokenInfo(token="t2", quota=50, quotas={"expert": QuotaWindow(remaining=0)})
    pool.add(t1)
    pool.add(t2)

    # all expert exhausted -> filter doesn't apply, fall back to legacy quota selection
    selected = pool.select(mode="expert")
    assert selected.token == "t1"  # higher legacy quota


def test_select_by_score_uses_mode_quota():
    config._config = {
        "token": {"inflight_enabled": True, "multi_mode_quota_enabled": True, "inflight_timeout_sec": 300}
    }

    pool = TokenPool("ssoSuper")
    t1 = TokenInfo(token="t1", quota=140, quotas={"expert": QuotaWindow(remaining=0)})
    t2 = TokenInfo(token="t2", quota=10, quotas={"expert": QuotaWindow(remaining=20)})
    pool.add(t1)
    pool.add(t2)

    # filter excludes t1; t2 wins by score
    selected = pool.select(mode="expert")
    assert selected.token == "t2"


def test_select_mode_missing_quota_falls_back_to_legacy():
    """token 没有该 mode 桶 -> get_effective_quota 返回 legacy quota"""
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    pool = TokenPool("ssoBasic")
    t1 = TokenInfo(token="t1", quota=10)  # quotas=None
    t2 = TokenInfo(token="t2", quota=80)  # quotas=None
    pool.add(t1)
    pool.add(t2)

    selected = pool.select(mode="fast")
    assert selected.token == "t2"


# ==================== acquire/release integration ====================


def test_manager_acquire_release_balanced():
    config._config = {"token": {"inflight_timeout_sec": 300}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    pool = TokenPool("ssoBasic")
    pool.add(TokenInfo(token="t1", quota=80))
    mgr.pools = {"ssoBasic": pool}

    assert mgr.acquire_token("t1") is True
    assert pool.get_inflight("t1") == 1
    mgr.release_token("t1")
    assert pool.get_inflight("t1") == 0


def test_manager_acquire_unknown_returns_false():
    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr.pools = {"ssoBasic": TokenPool("ssoBasic")}

    assert mgr.acquire_token("nonexistent") is False


def test_manager_release_without_acquire_is_noop():
    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    pool = TokenPool("ssoBasic")
    pool.add(TokenInfo(token="t1", quota=80))
    mgr.pools = {"ssoBasic": pool}

    # release without acquire shouldn't raise or affect state
    mgr.release_token("t1")
    assert pool.get_inflight("t1") == 0


def test_manager_release_unknown_token_is_noop():
    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr.pools = {"ssoBasic": TokenPool("ssoBasic")}

    # Should not raise
    mgr.release_token("nonexistent")


# ==================== admin per-mode quota exposure ====================


def test_token_info_model_dump_includes_quotas():
    """admin endpoint 依赖 model_dump 透出 quotas 字段"""
    token = TokenInfo(token="t1", quota=100, quotas={
        "auto": QuotaWindow(remaining=50, total=140, window_seconds=7200),
        "expert": QuotaWindow(remaining=10, total=50),
    })
    payload = token.model_dump()
    assert "quotas" in payload
    assert payload["quotas"]["auto"]["remaining"] == 50
    assert payload["quotas"]["auto"]["total"] == 140
    assert payload["quotas"]["expert"]["remaining"] == 10


def test_token_info_quotas_round_trip_through_dict():
    """admin POST 写回时通过 TokenInfo(**dict) 重建必须保留 quotas"""
    original = TokenInfo(token="t1", quota=100, quotas={
        "fast": QuotaWindow(remaining=80, total=80, window_seconds=72000),
    })
    payload = original.model_dump()

    rebuilt = TokenInfo(**payload)
    assert rebuilt.quotas is not None
    assert rebuilt.quotas["fast"].remaining == 80
    assert rebuilt.quotas["fast"].total == 80
    assert rebuilt.quotas["fast"].window_seconds == 72000


def test_token_info_dump_omits_quotas_when_none():
    """未启用 multi_mode 的 token 不应携带空 quotas 字段污染响应"""
    token = TokenInfo(token="t1", quota=80)
    payload = token.model_dump()
    # quotas exists as key but value is None
    assert payload.get("quotas") is None


# ==================== per-mode rate-limit ====================


def test_mark_rate_limited_per_mode_keeps_token_active():
    """multi_mode + mode 已知 -> 只清零该 mode，token 保持 ACTIVE"""
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    token = TokenInfo(token="t1", quota=140, quotas={
        "auto": QuotaWindow(remaining=50),
        "expert": QuotaWindow(remaining=20),
    })
    pool.add(token)
    mgr.pools = {"ssoSuper": pool}

    asyncio.run(mgr.mark_rate_limited("t1", mode="expert"))

    assert token.status == TokenStatus.ACTIVE
    assert token.quotas["expert"].remaining == 0
    assert token.quotas["auto"].remaining == 50  # untouched


def test_mark_rate_limited_falls_back_to_cooling_when_multi_mode_off():
    config._config = {"token": {"multi_mode_quota_enabled": False}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    token = TokenInfo(token="t1", quota=140)
    pool.add(token)
    mgr.pools = {"ssoSuper": pool}

    asyncio.run(mgr.mark_rate_limited("t1", mode="expert"))

    assert token.status == TokenStatus.COOLING
    assert token.quota == 0


def test_mark_rate_limited_no_mode_falls_back_to_cooling():
    """multi_mode 启用但 mode 未指定 -> 退化为旧行为"""
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    token = TokenInfo(token="t1", quota=140, quotas={"auto": QuotaWindow(remaining=50)})
    pool.add(token)
    mgr.pools = {"ssoSuper": pool}

    asyncio.run(mgr.mark_rate_limited("t1"))

    assert token.status == TokenStatus.COOLING
    assert token.quota == 0


def test_mark_rate_limited_unknown_mode_falls_back_to_cooling():
    """multi_mode + 未知 mode（不在 quotas 里）-> 退化为旧行为"""
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    from app.services.token.manager import TokenManager

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    token = TokenInfo(token="t1", quota=140, quotas={"auto": QuotaWindow(remaining=50)})
    pool.add(token)
    mgr.pools = {"ssoSuper": pool}

    asyncio.run(mgr.mark_rate_limited("t1", mode="heavy"))

    assert token.status == TokenStatus.COOLING
    assert token.quota == 0


# ==================== need_refresh covers empty mode buckets ====================


def test_need_refresh_active_token_with_zero_mode_returns_true():
    """ACTIVE token 但有 mode 桶 remaining=0 -> 需要刷新"""
    token = TokenInfo(token="t1", quota=50, quotas={
        "auto": QuotaWindow(remaining=50),
        "expert": QuotaWindow(remaining=0),  # per-mode rate limited
    })
    # last_sync_at None -> 立即 refresh
    assert token.need_refresh(interval_hours=8) is True


def test_need_refresh_active_token_all_modes_healthy_returns_false():
    """ACTIVE token 所有 mode 桶都有余 -> 不需要刷新"""
    token = TokenInfo(token="t1", quota=50, quotas={
        "auto": QuotaWindow(remaining=50),
        "expert": QuotaWindow(remaining=20),
    })
    token.mark_synced()  # 刚同步过
    assert token.need_refresh(interval_hours=8) is False


def test_need_refresh_active_token_no_quotas_returns_false():
    """ACTIVE token 没有 quotas（multi_mode 未启用）-> 不需要刷新"""
    token = TokenInfo(token="t1", quota=50)
    token.mark_synced()
    assert token.need_refresh(interval_hours=8) is False


def test_need_refresh_zero_mode_respects_interval():
    """有零桶但同步间隔未到 -> 不刷新"""
    import time as _time
    token = TokenInfo(token="t1", quota=50, quotas={
        "expert": QuotaWindow(remaining=0),
    })
    # 假装刚同步过
    token.last_sync_at = int(_time.time() * 1000)
    assert token.need_refresh(interval_hours=8) is False


def test_need_refresh_cooling_token_still_works():
    """旧逻辑回归：COOLING token 立即刷新"""
    token = TokenInfo(token="t1", quota=0, status=TokenStatus.COOLING)
    assert token.need_refresh(interval_hours=8) is True


# ==================== url_citation annotations ====================


def test_build_url_citations_basic():
    from app.services.grok.services.chat import _build_url_citations

    text = "AI is great [1] and useful [2]."
    sources = [
        {"url": "https://a.com", "title": "Article A", "type": "web"},
        {"url": "https://b.com", "title": "Article B", "type": "web"},
    ]
    anns = _build_url_citations(text, sources)
    assert len(anns) == 2
    assert anns[0]["type"] == "url_citation"
    assert anns[0]["url_citation"]["url"] == "https://a.com"
    assert anns[0]["url_citation"]["title"] == "Article A"
    assert anns[0]["url_citation"]["start_index"] == text.index("[1]")
    assert anns[0]["url_citation"]["end_index"] == text.index("[1]") + 3
    assert anns[1]["url_citation"]["url"] == "https://b.com"


def test_build_url_citations_out_of_range_skipped():
    from app.services.grok.services.chat import _build_url_citations

    text = "ref [1] then [3] then [2]"
    sources = [{"url": "https://a.com", "title": "A"}]
    anns = _build_url_citations(text, sources)
    # Only [1] is valid; [3] and [2] are out of range
    assert len(anns) == 1
    assert anns[0]["url_citation"]["url"] == "https://a.com"


def test_build_url_citations_falls_back_to_url_when_title_missing():
    from app.services.grok.services.chat import _build_url_citations

    text = "see [1]"
    sources = [{"url": "https://a.com"}]
    anns = _build_url_citations(text, sources)
    assert anns[0]["url_citation"]["title"] == "https://a.com"


def test_build_url_citations_empty_inputs():
    from app.services.grok.services.chat import _build_url_citations

    assert _build_url_citations("", [{"url": "https://a.com"}]) == []
    assert _build_url_citations("text", []) == []
    assert _build_url_citations("no markers here", [{"url": "https://a.com"}]) == []


def test_build_url_citations_repeated_marker_recorded_each_time():
    """同一个 [1] 在文中多次出现，每次都生成独立 annotation（位置不同）"""
    from app.services.grok.services.chat import _build_url_citations

    text = "[1] and [1] again"
    sources = [{"url": "https://a.com", "title": "A"}]
    anns = _build_url_citations(text, sources)
    assert len(anns) == 2
    assert anns[0]["url_citation"]["start_index"] == 0
    assert anns[1]["url_citation"]["start_index"] == text.index("[1] again")


# ==================== inline citations ====================


def test_inline_citations_basic():
    from app.services.grok.services.chat import _inline_citations

    text = "AI is great [1] and useful [2]."
    sources = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://b.com", "title": "B"},
    ]
    out = _inline_citations(text, sources)
    assert out == "AI is great [[1]](https://a.com) and useful [[2]](https://b.com)."


def test_inline_citations_dedup_consecutive():
    from app.services.grok.services.chat import _inline_citations

    text = "claim [1][1][1] then [2][2]"
    sources = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://b.com", "title": "B"},
    ]
    out = _inline_citations(text, sources)
    # consecutive [1][1][1] -> [1] then linkified
    assert out == "claim [[1]](https://a.com) then [[2]](https://b.com)"


def test_inline_citations_dedup_with_whitespace():
    from app.services.grok.services.chat import _inline_citations

    text = "x [1] [1] y"
    sources = [{"url": "https://a.com"}]
    out = _inline_citations(text, sources)
    assert out == "x [[1]](https://a.com) y"


def test_inline_citations_out_of_range_kept_raw():
    from app.services.grok.services.chat import _inline_citations

    text = "see [5] for more"
    sources = [{"url": "https://a.com"}]
    out = _inline_citations(text, sources)
    # [5] out of range -> keep as-is
    assert out == "see [5] for more"


def test_inline_citations_empty_url_kept_raw():
    from app.services.grok.services.chat import _inline_citations

    text = "ref [1]"
    sources = [{"url": ""}]
    out = _inline_citations(text, sources)
    assert out == "ref [1]"


def test_inline_citations_no_sources_returns_input():
    from app.services.grok.services.chat import _inline_citations

    text = "ref [1] [2]"
    assert _inline_citations(text, []) == text
    assert _inline_citations("", [{"url": "https://a.com"}]) == ""


# ==================== auto chat mode fallback ====================


def test_mode_candidates_auto_returns_fallback_chain():
    config._config = {"features": {"auto_chat_mode_fallback": True}}
    from app.services.grok.utils.retry import _mode_candidates

    # grok-4 / grok-3 etc. -> primary mode is "fast" (basic tier fallback)
    # so they don't trigger the fallback
    cands = _mode_candidates("grok-4")
    assert cands == ("fast",)


def test_mode_candidates_auto_super_tier_triggers_fallback():
    config._config = {"features": {"auto_chat_mode_fallback": True}}
    from app.services.grok.utils.retry import _mode_candidates

    # grok-4.20-beta is BASIC tier → primary "fast" only
    cands = _mode_candidates("grok-4.20-beta")
    assert cands == ("fast",)


def test_mode_candidates_disabled_returns_primary_only():
    config._config = {"features": {"auto_chat_mode_fallback": False}}
    from app.services.grok.utils.retry import _mode_candidates

    # Even if model maps to "auto", disabled flag means primary only
    # (no model in our registry maps to "auto" currently, but verify the gate)
    cands = _mode_candidates("grok-4-heavy")
    assert cands == ("heavy",)  # heavy tier, not auto


def test_mode_candidates_unknown_model_returns_none_tuple():
    from app.services.grok.utils.retry import _mode_candidates

    # ModelService.quota_mode_for_model returns "fast" for unknown,
    # so candidates will be ("fast",) — but if it raises we handle it
    cands = _mode_candidates("unknown-model")
    assert cands == ("fast",)


# ==================== feature flag env override ====================


def test_feature_enabled_falls_back_to_config():
    import os
    from app.core.config import feature_enabled

    config._config = {"token": {"multi_mode_quota_enabled": True}}
    os.environ.pop("GROK2API_TOKEN_MULTI_MODE_QUOTA_ENABLED", None)
    assert feature_enabled("token.multi_mode_quota_enabled", False) is True


def test_feature_enabled_env_overrides_config():
    import os
    from app.core.config import feature_enabled

    config._config = {"token": {"multi_mode_quota_enabled": False}}
    os.environ["GROK2API_TOKEN_MULTI_MODE_QUOTA_ENABLED"] = "true"
    try:
        assert feature_enabled("token.multi_mode_quota_enabled", False) is True
    finally:
        os.environ.pop("GROK2API_TOKEN_MULTI_MODE_QUOTA_ENABLED", None)


def test_feature_enabled_env_can_disable():
    import os
    from app.core.config import feature_enabled

    config._config = {"token": {"inflight_enabled": True}}
    os.environ["GROK2API_TOKEN_INFLIGHT_ENABLED"] = "0"
    try:
        assert feature_enabled("token.inflight_enabled", True) is False
    finally:
        os.environ.pop("GROK2API_TOKEN_INFLIGHT_ENABLED", None)


def test_feature_enabled_non_allowlisted_key_ignores_env():
    """非白名单 key 不读环境变量，只走 config"""
    import os
    from app.core.config import feature_enabled

    config._config = {"some": {"random_flag": False}}
    os.environ["GROK2API_SOME_RANDOM_FLAG"] = "true"
    try:
        assert feature_enabled("some.random_flag", False) is False
    finally:
        os.environ.pop("GROK2API_SOME_RANDOM_FLAG", None)


def test_feature_flags_summary_includes_all_allowlisted():
    from app.core.config import feature_flags_summary

    summary = feature_flags_summary()
    # All allowlisted keys should appear
    assert "token.multi_mode_quota_enabled" in summary
    assert "token.inflight_enabled" in summary
    assert "features.auto_chat_mode_fallback" in summary
    # Each value is a bool
    for v in summary.values():
        assert isinstance(v, bool)


# ==================== admin metrics endpoint ====================


def test_admin_metrics_aggregates_per_mode_quota():
    config._config = {"token": {"multi_mode_quota_enabled": True}}

    from app.services.token.manager import TokenManager
    from app.api.v1.admin_api.metrics import admin_metrics

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoSuper")
    pool.add(TokenInfo(token="t1", quota=140, quotas={
        "auto": QuotaWindow(remaining=100, total=140),
        "fast": QuotaWindow(remaining=80, total=140),
        "expert": QuotaWindow(remaining=0, total=50),
    }))
    pool.add(TokenInfo(token="t2", quota=100, quotas={
        "auto": QuotaWindow(remaining=50, total=140),
    }))
    mgr.pools = {"ssoSuper": pool}

    # patch the singleton lookup
    with patch("app.api.v1.admin_api.metrics.get_token_manager", AsyncMock(return_value=mgr)):
        result = asyncio.run(admin_metrics())

    assert "pools" in result
    super_info = result["pools"]["ssoSuper"]
    assert super_info["total"] == 2
    # auto bucket: 100 + 50 = 150
    auto = next(p for p in super_info["per_mode_quota"] if p["mode"] == "auto")
    assert auto["total_remaining"] == 150
    assert auto["tokens_with_bucket"] == 2
    # expert bucket: only t1 has it, and it's 0
    expert = next(p for p in super_info["per_mode_quota"] if p["mode"] == "expert")
    assert expert["total_remaining"] == 0
    assert expert["tokens_exhausted"] == 1
    # feature flags surface
    assert "feature_flags" in result
    assert "token.multi_mode_quota_enabled" in result["feature_flags"]


def test_admin_metrics_inflight_distribution():
    config._config = {"token": {"inflight_timeout_sec": 300}}

    from app.services.token.manager import TokenManager
    from app.api.v1.admin_api.metrics import admin_metrics

    mgr = TokenManager()
    mgr.initialized = True
    mgr._schedule_save = lambda: None
    pool = TokenPool("ssoBasic")
    pool.add(TokenInfo(token="t1", quota=80))
    pool.add(TokenInfo(token="t2", quota=80))
    mgr.pools = {"ssoBasic": pool}
    pool.acquire("t1")
    pool.acquire("t1")
    pool.acquire("t2")

    with patch("app.api.v1.admin_api.metrics.get_token_manager", AsyncMock(return_value=mgr)):
        result = asyncio.run(admin_metrics())

    basic = result["pools"]["ssoBasic"]
    assert basic["total_inflight"] == 3
    assert basic["inflight_distribution"]["tokens_in_flight"] == 2
    assert basic["inflight_distribution"]["max_per_token"] == 2
    assert result["summary"]["total_inflight"] == 3


# ==================== log rotation ====================


def test_prune_old_logs_keeps_only_max_files(tmp_path, monkeypatch):
    """超过 max_files 的旧日志按日期排序后被清理"""
    from app.core import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", tmp_path)
    for day in ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]:
        (tmp_path / f"app_{day}.log").write_text("x", encoding="utf-8")
    # An unrelated file should not be touched
    (tmp_path / "other.txt").write_text("y", encoding="utf-8")

    logger_mod._prune_old_logs(3)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        "app_2026-01-03.log",
        "app_2026-01-04.log",
        "app_2026-01-05.log",
        "other.txt",
    ]


def test_prune_old_logs_noop_when_under_limit(tmp_path, monkeypatch):
    from app.core import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", tmp_path)
    (tmp_path / "app_2026-01-01.log").write_text("x", encoding="utf-8")
    (tmp_path / "app_2026-01-02.log").write_text("y", encoding="utf-8")

    logger_mod._prune_old_logs(5)

    assert len(list(tmp_path.iterdir())) == 2


def test_prune_old_logs_zero_max_files_disabled(tmp_path, monkeypatch):
    """max_files=0 表示禁用清理"""
    from app.core import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", tmp_path)
    for day in ["2026-01-01", "2026-01-02", "2026-01-03"]:
        (tmp_path / f"app_{day}.log").write_text("x", encoding="utf-8")

    logger_mod._prune_old_logs(0)

    assert len(list(tmp_path.iterdir())) == 3


def test_prune_old_logs_ignores_non_matching_filenames(tmp_path, monkeypatch):
    from app.core import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", tmp_path)
    (tmp_path / "app_2026-01-01.log").write_text("x", encoding="utf-8")
    (tmp_path / "debug.log").write_text("y", encoding="utf-8")
    (tmp_path / "app_invalid.log").write_text("z", encoding="utf-8")

    logger_mod._prune_old_logs(0)

    assert len(list(tmp_path.iterdir())) == 3
