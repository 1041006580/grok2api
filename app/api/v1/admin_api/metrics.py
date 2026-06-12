"""Admin metrics endpoint — runtime visibility for token pools and features."""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.core.auth import verify_app_key
from app.core.config import feature_flags_summary
from app.services.token.manager import POOL_SYNC_MODES, get_token_manager

router = APIRouter()


@router.get("/metrics", dependencies=[Depends(verify_app_key)])
async def admin_metrics() -> Dict[str, Any]:
    """
    Runtime metrics: token pool health, per-mode quota distribution,
    inflight counts, feature flag status.
    """
    mgr = await get_token_manager()

    pools_info: Dict[str, Any] = {}
    grand_total_inflight = 0
    grand_per_mode: Dict[str, Dict[str, int]] = {}

    for pool_name, pool in mgr.pools.items():
        stats = pool.get_stats()
        per_mode_remaining: Dict[str, int] = {}
        per_mode_zero_count: Dict[str, int] = {}
        per_mode_token_count: Dict[str, int] = {}
        inflight_dist: List[int] = []

        for token in pool.list():
            inflight = pool.get_inflight(token.token)
            if inflight > 0:
                inflight_dist.append(inflight)
            if token.quotas:
                for mode_id, window in token.quotas.items():
                    per_mode_remaining[mode_id] = (
                        per_mode_remaining.get(mode_id, 0) + window.remaining
                    )
                    per_mode_token_count[mode_id] = (
                        per_mode_token_count.get(mode_id, 0) + 1
                    )
                    if window.remaining == 0:
                        per_mode_zero_count[mode_id] = (
                            per_mode_zero_count.get(mode_id, 0) + 1
                        )

        per_mode: List[Dict[str, Any]] = []
        for mode_id in POOL_SYNC_MODES.get(pool_name, []):
            per_mode.append({
                "mode": mode_id,
                "total_remaining": per_mode_remaining.get(mode_id, 0),
                "tokens_with_bucket": per_mode_token_count.get(mode_id, 0),
                "tokens_exhausted": per_mode_zero_count.get(mode_id, 0),
            })
            bucket = grand_per_mode.setdefault(mode_id, {
                "total_remaining": 0,
                "tokens_with_bucket": 0,
                "tokens_exhausted": 0,
            })
            bucket["total_remaining"] += per_mode_remaining.get(mode_id, 0)
            bucket["tokens_with_bucket"] += per_mode_token_count.get(mode_id, 0)
            bucket["tokens_exhausted"] += per_mode_zero_count.get(mode_id, 0)

        pools_info[pool_name] = {
            "total": stats.total,
            "active": stats.active,
            "cooling": stats.cooling,
            "expired": stats.expired,
            "disabled": stats.disabled,
            "total_quota": stats.total_quota,
            "avg_quota": round(stats.avg_quota, 2),
            "total_inflight": stats.total_inflight,
            "inflight_distribution": {
                "tokens_in_flight": len(inflight_dist),
                "max_per_token": max(inflight_dist) if inflight_dist else 0,
                "avg_per_token": (
                    round(sum(inflight_dist) / len(inflight_dist), 2)
                    if inflight_dist else 0.0
                ),
            },
            "per_mode_quota": per_mode,
        }
        grand_total_inflight += stats.total_inflight

    return {
        "pools": pools_info,
        "summary": {
            "total_inflight": grand_total_inflight,
            "per_mode_quota": [
                {"mode": k, **v} for k, v in grand_per_mode.items()
            ],
        },
        "feature_flags": feature_flags_summary(),
    }


__all__ = ["router"]
