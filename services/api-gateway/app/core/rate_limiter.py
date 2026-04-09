"""Redis-based sliding window rate limiter."""
import time
import redis
import os
import logging

logger = logging.getLogger("api-gateway")

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        _redis = redis.Redis.from_url(url, decode_responses=True)
    return _redis


TIER_LIMITS = {
    "free": 100,    # 100 req/min
    "user": 100,
    "pro": 500,
    "admin": 2000,
}


def check_rate_limit(user_id: str, tier: str = "user") -> tuple[bool, int]:
    """Check if user is within rate limit. Returns (allowed, remaining)."""
    limit = TIER_LIMITS.get(tier, 100)
    key = f"ratelimit:{user_id}"
    now = time.time()
    window = 60  # 1 minute

    try:
        r = _get_redis()
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zadd(key, {f"{now}:{id(now)}": now})
        pipe.zcard(key)
        pipe.expire(key, window + 1)
        results = pipe.execute()
        count = results[2]
        remaining = max(0, limit - count)
        return count <= limit, remaining
    except Exception:
        return True, limit  # fail open
