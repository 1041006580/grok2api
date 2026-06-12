"""Token 刷新调度器"""

import asyncio
from typing import Optional

from app.core.config import get_config
from app.core.logger import logger
from app.core.storage import get_storage, StorageError, RedisStorage
from app.services.token.manager import get_token_manager


DEFAULT_INFLIGHT_CLEANUP_INTERVAL_SEC = 60


class TokenRefreshScheduler:
    """Token 自动刷新调度器"""

    def __init__(self, interval_hours: int = 8):
        self.interval_hours = interval_hours
        self.interval_seconds = interval_hours * 3600
        self._task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

    async def _refresh_loop(self):
        """刷新循环"""
        logger.info(f"Scheduler: started (interval: {self.interval_hours}h)")

        while self._running:
            try:
                storage = get_storage()
                lock_acquired = False
                redis_lock = None
                lock_ctx = None

                if isinstance(storage, RedisStorage):
                    lock_key = "grok2api:lock:token_refresh"
                    redis_lock = storage.redis.lock(
                        lock_key, timeout=self.interval_seconds + 60, blocking_timeout=0
                    )
                    lock_acquired = await redis_lock.acquire(blocking=False)
                else:
                    try:
                        lock_ctx = storage.acquire_lock("token_refresh", timeout=1)
                        await lock_ctx.__aenter__()
                        lock_acquired = True
                    except (StorageError, Exception):
                        lock_ctx = None
                        lock_acquired = False

                if not lock_acquired:
                    logger.info("Scheduler: skipped (lock not acquired)")
                    await asyncio.sleep(self.interval_seconds)
                    continue

                try:
                    logger.info("Scheduler: starting token refresh...")
                    manager = await get_token_manager()
                    result = await manager.refresh_cooling_tokens()

                    logger.info(
                        f"Scheduler: refresh completed - "
                        f"checked={result['checked']}, "
                        f"refreshed={result['refreshed']}, "
                        f"recovered={result['recovered']}, "
                        f"expired={result['expired']}"
                    )
                finally:
                    if redis_lock is not None and lock_acquired:
                        try:
                            await redis_lock.release()
                        except Exception:
                            pass
                    if lock_ctx is not None:
                        try:
                            await lock_ctx.__aexit__(None, None, None)
                        except Exception:
                            pass

                await asyncio.sleep(self.interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduler: refresh error - {e}")
                await asyncio.sleep(self.interval_seconds)

    async def _cleanup_loop(self):
        """周期性清理过期 inflight 条目"""
        while self._running:
            try:
                interval = get_config(
                    "token.inflight_cleanup_interval_sec",
                    DEFAULT_INFLIGHT_CLEANUP_INTERVAL_SEC,
                )
                try:
                    interval = float(interval)
                except (TypeError, ValueError):
                    interval = float(DEFAULT_INFLIGHT_CLEANUP_INTERVAL_SEC)
                if interval <= 0:
                    interval = float(DEFAULT_INFLIGHT_CLEANUP_INTERVAL_SEC)

                await asyncio.sleep(interval)

                manager = await get_token_manager()
                total_cleaned = 0
                for pool in manager.pools.values():
                    try:
                        total_cleaned += pool.cleanup_stale_inflight()
                    except Exception as e:
                        logger.warning(
                            f"Inflight cleanup failed for pool '{pool.name}': {e}"
                        )
                if total_cleaned > 0:
                    logger.info(f"Inflight cleanup: removed {total_cleaned} stale entries")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Inflight cleanup loop error: {e}")
                await asyncio.sleep(DEFAULT_INFLIGHT_CLEANUP_INTERVAL_SEC)

    def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("Scheduler: already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._refresh_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Scheduler: enabled")

    def stop(self):
        """停止调度器"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        logger.info("Scheduler: stopped")


# 全局单例
_scheduler: Optional[TokenRefreshScheduler] = None


def get_scheduler(interval_hours: int = 8) -> TokenRefreshScheduler:
    """获取调度器单例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = TokenRefreshScheduler(interval_hours)
    return _scheduler


__all__ = ["TokenRefreshScheduler", "get_scheduler"]
