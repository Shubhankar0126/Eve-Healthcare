"""Fixed-window rate limiter: Redis when configured, in-process fallback otherwise."""
import threading
import time

import redis
from fastapi import Request

from app.cache import get_redis
from app.config import settings
from app.errors import AppError

WINDOW = 60
_local: dict[str, tuple[int, float]] = {}
_lock = threading.Lock()


def reset_memory() -> None:
    with _lock:
        _local.clear()


def _hit(key: str) -> tuple[int, int]:
    r = get_redis()
    if r:
        try:
            count = r.incr(key)
            if count == 1:
                r.expire(key, WINDOW)
            return count, max(r.ttl(key), 1)
        except redis.RedisError:
            pass  # fall through to local counter
    now = time.time()
    with _lock:
        count, expires = _local.get(key, (0, 0.0))
        if expires <= now:
            count, expires = 0, now + WINDOW
        _local[key] = (count + 1, expires)
        return count + 1, int(expires - now) + 1


def rate_limit(scope: str, setting_name: str):
    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        limit = getattr(settings, setting_name)
        ip = request.client.host if request.client else "unknown"
        count, retry_after = _hit(f"rl:{scope}:{ip}:{int(time.time() // WINDOW)}")
        if count > limit:
            raise AppError(429, "rate_limited", "Too many requests, slow down",
                           {"Retry-After": str(retry_after)})

    return dependency
