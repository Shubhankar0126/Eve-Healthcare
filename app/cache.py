"""Optional Redis cache. Every failure degrades to a cache miss - Redis is never a hard dependency."""
import json
import logging

import redis

from app.config import settings

log = logging.getLogger(__name__)
_client: redis.Redis | None = None


def get_redis() -> redis.Redis | None:
    global _client
    if not settings.redis_url:
        return None
    if _client is None:
        _client = redis.Redis.from_url(settings.redis_url, socket_timeout=0.5,
                                       socket_connect_timeout=0.5, decode_responses=True)
    return _client


def ns_key(namespace: str, suffix: str) -> str:
    """Namespaced key carrying a version, so invalidation is a single INCR."""
    r = get_redis()
    version = "0"
    if r:
        try:
            version = r.get(f"ver:{namespace}") or "0"
        except redis.RedisError:
            log.warning("cache_unavailable")
    return f"{namespace}:v{version}:{suffix}"


def cache_get(key: str):
    r = get_redis()
    if not r:
        return None
    try:
        raw = r.get(key)
        return json.loads(raw) if raw else None
    except (redis.RedisError, ValueError):
        return None


def cache_set(key: str, value, ttl: int | None = None) -> None:
    r = get_redis()
    if not r:
        return
    try:
        r.set(key, json.dumps(value, default=str), ex=ttl or settings.cache_ttl_seconds)
    except redis.RedisError:
        log.warning("cache_write_failed", extra={"key": key})


def invalidate(namespace: str) -> None:
    r = get_redis()
    if not r:
        return
    try:
        r.incr(f"ver:{namespace}")
    except redis.RedisError:
        log.warning("cache_invalidate_failed", extra={"namespace": namespace})
