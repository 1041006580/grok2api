"""
Proxy pool with sticky selection and failover rotation.
"""

import threading
from typing import Optional

from app.core.logger import logger

_lock = threading.Lock()
_pools: dict[str, list[str]] = {}
_indexes: dict[str, int] = {}
_raw_cache: dict[str, str] = {}
_FAILOVER_STATUS_CODES = frozenset({403, 429, 502})


def _parse_proxies(raw: str) -> list[str]:
    if not raw:
        return []
    return [proxy.strip() for proxy in raw.split(",") if proxy.strip()]


def _ensure_pool(config_key: str) -> list[str]:
    from app.core.config import config

    raw = config.get(config_key, "") or ""
    if raw != _raw_cache.get(config_key):
        proxies = _parse_proxies(raw)
        _pools[config_key] = proxies
        _indexes[config_key] = 0
        _raw_cache[config_key] = raw
        if len(proxies) > 1:
            logger.info(
                f"ProxyPool: {config_key} loaded {len(proxies)} proxies for failover"
            )
    return _pools.get(config_key, [])


def get_current_proxy(config_key: str) -> str:
    with _lock:
        pool = _ensure_pool(config_key)
        if not pool:
            return ""
        idx = _indexes.get(config_key, 0) % len(pool)
        _indexes[config_key] = idx
        return pool[idx]


def get_current_proxy_from(*config_keys: str) -> tuple[Optional[str], str]:
    for config_key in config_keys:
        proxy = get_current_proxy(config_key)
        if proxy:
            return config_key, proxy
    return None, ""


def rotate_proxy(config_key: str) -> str:
    with _lock:
        pool = _ensure_pool(config_key)
        if not pool:
            return ""
        if len(pool) == 1:
            return pool[0]
        next_idx = (_indexes.get(config_key, 0) + 1) % len(pool)
        _indexes[config_key] = next_idx
        proxy = pool[next_idx]
        logger.warning(
            f"ProxyPool: rotate {config_key} to index {next_idx + 1}/{len(pool)}"
        )
        return proxy


def should_rotate_proxy(status_code: Optional[int]) -> bool:
    return status_code in _FAILOVER_STATUS_CODES


def build_http_proxies(proxy_url: str) -> Optional[dict[str, str]]:
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


__all__ = [
    "build_http_proxies",
    "get_current_proxy",
    "get_current_proxy_from",
    "rotate_proxy",
    "should_rotate_proxy",
]
