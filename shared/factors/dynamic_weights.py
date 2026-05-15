"""Dynamic factor/category weights stored in Redis.
Updated by the optimizer; read by factor_ensemble.
Falls back to static defaults if Redis unavailable.
"""
import os
import logging
import json

logger = logging.getLogger("dynamic-weights")

_redis = None

DEFAULT_CATEGORY_WEIGHTS = {
    "trending": {"technical": 1.0, "momentum": 1.2, "reversion": 0.8, "volatility": 0.8, "derivatives": 1.0, "sentiment": 0.9},
    "sideways": {"technical": 1.0, "momentum": 0.6, "reversion": 1.4, "volatility": 0.8, "derivatives": 1.0, "sentiment": 1.0},
    "volatile": {"technical": 0.8, "momentum": 0.7, "reversion": 1.0, "volatility": 1.4, "derivatives": 1.2, "sentiment": 1.0},
    "default":  {"technical": 1.0, "momentum": 1.0, "reversion": 1.0, "volatility": 1.0, "derivatives": 1.0, "sentiment": 1.0},
}

DEFAULT_FACTOR_WEIGHTS = {}  # empty = all factors equal weight 1.0

REDIS_CATEGORY_KEY = "learning:category_weights:v1"
REDIS_FACTOR_KEY = "learning:factor_weights:v1"
REDIS_PROTOCOL_KEY = "learning:active_protocol"
REDIS_ACCURACY_KEY = "learning:recent_accuracy"

def _get_redis():
    global _redis
    if _redis is None:
        try:
            import redis
            _redis = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        except Exception:
            pass
    return _redis

def load_category_weights() -> dict:
    r = _get_redis()
    if r:
        try:
            raw = r.get(REDIS_CATEGORY_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.debug(f"redis_load_failed: {e}")
    return DEFAULT_CATEGORY_WEIGHTS

def save_category_weights(weights: dict):
    r = _get_redis()
    if r:
        try:
            r.set(REDIS_CATEGORY_KEY, json.dumps(weights))
        except Exception as e:
            logger.warning(f"redis_save_failed: {e}")

def load_factor_weights() -> dict:
    r = _get_redis()
    if r:
        try:
            raw = r.get(REDIS_FACTOR_KEY)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return DEFAULT_FACTOR_WEIGHTS

def save_factor_weights(weights: dict):
    r = _get_redis()
    if r:
        try:
            r.set(REDIS_FACTOR_KEY, json.dumps(weights))
        except Exception:
            pass

def get_active_protocol() -> str:
    r = _get_redis()
    if r:
        try:
            return r.get(REDIS_PROTOCOL_KEY) or "standard"
        except Exception:
            pass
    return "standard"

def set_active_protocol(protocol: str):
    r = _get_redis()
    if r:
        try:
            r.set(REDIS_PROTOCOL_KEY, protocol)
        except Exception:
            pass

def get_recent_accuracy() -> float:
    r = _get_redis()
    if r:
        try:
            val = r.get(REDIS_ACCURACY_KEY)
            return float(val) if val else 0.0
        except Exception:
            pass
    return 0.0

def set_recent_accuracy(accuracy: float):
    r = _get_redis()
    if r:
        try:
            r.set(REDIS_ACCURACY_KEY, str(round(accuracy, 4)))
        except Exception:
            pass
