"""Token 刷新调度器"""

import asyncio
from typing import Optional

from app.core.logger import logger
from app.core.storage import get_storage, StorageError, RedisStorage
from app.services.token.manager import get_token_manager


class TokenRefreshScheduler:
    """Token 自动刷新调度器"""

    def __init__(self, interval_hours: int = 8):
        self.interval_hours = interval_hours
        self.interval_seconds = interval_hours * 3600
        self._task: Optional[asyncio.Task] = None
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

    def start(self):
        """启动调度器"""
        if self._running:
            logger.warning("Scheduler: already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._refresh_loop())
        logger.info("Scheduler: enabled")

    def stop(self):
        """停止调度器"""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
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
