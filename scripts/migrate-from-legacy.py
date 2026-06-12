#!/usr/bin/env python3
"""V2 数据迁移脚本

从旧版 grok2api data/token.json 和 data/config.toml 迁移到上游 v2 架构。

用法:
    python scripts/migrate-from-legacy.py --legacy-data-dir=D:/project/grok2api/data --output=accounts.json

输出:
    accounts.json — 可导入上游 local SQLite backend 的 AccountRecord 列表（JSON 数组）
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 模拟上游 v2 的 QuotaWindow 与 AccountQuotaSet
def make_quota_window(remaining: int, total: int, window_seconds: int, source: int = 0) -> dict[str, Any]:
    """构造 QuotaWindow dict（上游序列化格式）"""
    return {
        "remaining": remaining,
        "total": total,
        "window_seconds": window_seconds,
        "reset_at": None,
        "synced_at": None,
        "source": source,
    }

def default_quota_set(pool: str) -> dict[str, dict[str, Any]]:
    """上游 v2 default_quota_set（quota_defaults.py）"""
    if pool == "basic":
        return {
            "auto": make_quota_window(0, 0, 0),
            "fast": make_quota_window(30, 30, 86400),
            "expert": make_quota_window(0, 0, 0),
        }
    elif pool == "super":
        return {
            "auto": make_quota_window(50, 50, 7200),
            "fast": make_quota_window(140, 140, 7200),
            "expert": make_quota_window(50, 50, 7200),
            "grok_4_3": make_quota_window(50, 50, 7200),
        }
    elif pool == "heavy":
        return {
            "auto": make_quota_window(150, 150, 7200),
            "fast": make_quota_window(400, 400, 7200),
            "expert": make_quota_window(150, 150, 7200),
            "heavy": make_quota_window(20, 20, 7200),
            "grok_4_3": make_quota_window(150, 150, 7200),
        }
    return default_quota_set("basic")

def migrate_token(old: dict[str, Any], pool: str) -> dict[str, Any]:
    """旧 token.json 条目 → 上游 AccountRecord"""
    now_ms = old.get("created_at", 0) or 0

    # 上游 v2 AccountRecord 字段
    record: dict[str, Any] = {
        "token": old.get("token", ""),
        "pool": pool,
        "status": old.get("status", "active"),
        "created_at": now_ms,
        "updated_at": old.get("last_used_at", now_ms) or now_ms,
        "tags": old.get("tags", []),
        "usage_use_count": old.get("use_count", 0),
        "usage_fail_count": old.get("fail_count", 0),
        "usage_sync_count": 0,
        "last_use_at": old.get("last_used_at"),
        "last_fail_at": old.get("last_fail_at"),
        "last_fail_reason": old.get("last_fail_reason"),
        "last_sync_at": old.get("last_sync_at"),
        "last_clear_at": old.get("last_asset_clear_at"),
        "state_reason": None,
        "deleted_at": None,
        "ext": {},
        "revision": 0,
    }

    # quota 字段迁移：旧版若有 quotas (multi_mode) 用它，否则用默认
    quotas_old = old.get("quotas")
    if quotas_old and isinstance(quotas_old, dict):
        quota_dict: dict[str, dict[str, Any]] = {}
        for mode_key, qw in quotas_old.items():
            if not isinstance(qw, dict):
                continue
            quota_dict[mode_key] = {
                "remaining": qw.get("remaining", 0),
                "total": qw.get("total", 0),
                "window_seconds": qw.get("window_seconds", 0),
                "reset_at": qw.get("reset_at"),
                "synced_at": qw.get("synced_at"),
                "source": qw.get("source", 0),
            }
        # 上游 v2 要求至少有 auto/fast/expert；没有的补默认
        defaults = default_quota_set(pool)
        for mode_key in ("auto", "fast", "expert", "heavy", "grok_4_3"):
            if mode_key not in quota_dict and mode_key in defaults:
                quota_dict[mode_key] = defaults[mode_key]
        record["quota"] = quota_dict
    else:
        # 旧版单值 quota 无法映射到多模式，用默认
        record["quota"] = default_quota_set(pool)

    return record

def main():
    parser = argparse.ArgumentParser(description="Migrate legacy token.json to v2 AccountRecord JSON")
    parser.add_argument("--legacy-data-dir", type=str, required=True, help="旧版 data/ 目录路径")
    parser.add_argument("--output", type=str, default="accounts.json", help="输出文件名")
    args = parser.parse_args()

    legacy_dir = Path(args.legacy_data_dir)
    token_json_path = legacy_dir / "token.json"

    if not token_json_path.exists():
        print(f"错误: {token_json_path} 不存在", file=sys.stderr)
        sys.exit(1)

    with open(token_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 旧版格式：{ssoBasic: [...], super: [...], heavy: [...]}
    all_records: list[dict[str, Any]] = []
    for pool_key in ("ssoBasic", "basic", "super", "heavy"):
        pool_items = data.get(pool_key, [])
        pool_name = "basic" if pool_key in ("ssoBasic", "basic") else pool_key
        for old_token in pool_items:
            record = migrate_token(old_token, pool_name)
            all_records.append(record)

    output_path = Path(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"[OK] Migration complete: {len(all_records)} records -> {output_path}")
    print()
    print("Next steps:")
    print("1. Start v2 backend once to create SQLite DB:")
    print("   cd .worktrees/v2 && uv run python -m app.main")
    print("2. Import accounts.json to DB (need custom import script or SQL INSERT)")
    print("3. Or use upstream control/account/commands.py upsert_accounts API")

if __name__ == "__main__":
    main()
