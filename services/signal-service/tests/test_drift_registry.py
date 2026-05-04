"""Integration test for drift_registry persist/restore cycle."""
import json
from unittest.mock import MagicMock

import pytest


def test_persist_and_restore_cycle(monkeypatch):
    """Observe → persist to Redis → create new registry → restore → same state."""
    # Mock Redis as an in-memory dict
    store = {}

    class MockRedis:
        def get(self, key):
            return store.get(key)

        def set(self, key, value):
            store[key] = value

    # Force re-import with fresh state
    import importlib
    import app.core.drift_registry as mod

    monkeypatch.setattr(mod, "_get_redis", lambda: MockRedis())
    monkeypatch.setattr(mod, "_PERSIST_EVERY", 1)  # persist on every observe

    reg1 = mod._Registry()
    reg1._redis = MockRedis()

    # Observe 50 returns
    for i in range(50):
        reg1.observe("ETHUSDT", 0.001 * (i % 5 - 2))

    # Force final persist
    reg1._persist("ETHUSDT")

    # Verify Redis has data
    raw = store.get("drift:obs:ETHUSDT")
    assert raw is not None
    data = json.loads(raw)
    assert len(data) == 50

    # Create new registry, restore from Redis
    reg2 = mod._Registry()
    reg2._redis = MockRedis()
    mon2 = reg2.get("ETHUSDT")

    # Should have 50 observations restored
    assert len(mon2._returns) == 50

    # Alerts should be evaluable
    alert = reg2.evaluate("ETHUSDT")
    assert alert.level in ("ok", "warn", "breach")


def test_no_redis_graceful():
    """Registry works fine without Redis — no crash, just no persistence."""
    import app.core.drift_registry as mod

    reg = mod._Registry()
    reg._redis = None

    reg.observe("BTCUSDT", 0.005)
    reg.observe("BTCUSDT", -0.003)
    alert = reg.evaluate("BTCUSDT")
    assert alert.level == "ok"  # insufficient_samples with 2 obs
