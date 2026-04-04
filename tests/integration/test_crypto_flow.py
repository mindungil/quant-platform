"""
Integration test for the full crypto execution flow.

Verifies the data contract chain:
  market-data → feature-store → signal-service → crypto-agent → order-service

Each service layer is tested with real business logic but stubbed I/O.
"""

from datetime import UTC, datetime
from pathlib import Path
import importlib
import importlib.util
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Ensure shared/ is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _isolate_load(service_dir: str, module_dotpath: str):
    """
    Load a module from a service directory, isolating the service's `app` package.

    Temporarily replaces any existing `app` entries in sys.modules with ones
    rooted in the given service directory, loads the requested module, then
    stores all loaded `app.*` modules under a namespaced key so they don't
    collide with the next service's `app` package.
    """
    svc_root = ROOT / "services" / service_dir
    svc_str = str(svc_root)
    ns = f"_svc_{service_dir.replace('-', '_')}"

    # Check if we already loaded this service's module
    cached_key = f"{ns}.{module_dotpath}"
    if cached_key in sys.modules:
        return sys.modules[cached_key]

    # Save and remove any existing app.* modules
    saved = {}
    to_remove = [k for k in sys.modules if k == "app" or k.startswith("app.")]
    for k in to_remove:
        saved[k] = sys.modules.pop(k)

    # Restore this service's previously loaded app.* modules
    for k, v in list(sys.modules.items()):
        if k.startswith(f"{ns}.app"):
            real_key = k[len(ns) + 1:]  # strip "ns." prefix
            sys.modules[real_key] = v

    # Prepend service root
    inserted = svc_str not in sys.path
    if inserted:
        sys.path.insert(0, svc_str)

    try:
        # Import the requested module
        mod = importlib.import_module(module_dotpath)

        # Namespace all app.* modules for this service
        current_app_mods = {
            k: v for k, v in sys.modules.items()
            if k == "app" or k.startswith("app.")
        }
        for k, v in current_app_mods.items():
            sys.modules[f"{ns}.{k}"] = v

        # Store the target module under its namespaced key too
        sys.modules[cached_key] = mod

    finally:
        # Remove this service's app.* from the global namespace
        to_clean = [k for k in sys.modules if k == "app" or k.startswith("app.")]
        for k in to_clean:
            del sys.modules[k]

        # Restore previously saved app.* modules
        sys.modules.update(saved)

        if inserted:
            sys.path.remove(svc_str)

    return mod


# ---- Eagerly load all needed service modules ----

validator_mod = _isolate_load("market-data", "app.core.validator")
candle_model = _isolate_load("market-data", "app.models.candle")

indicators_mod = _isolate_load("feature-store", "app.core.indicators")
feature_model = _isolate_load("feature-store", "app.models.feature")

scoring_mod = _isolate_load("signal-service", "app.core.scoring")
signal_model = _isolate_load("signal-service", "app.models.signal")


# ===== helpers =====

def _make_candles(count: int = 30):
    CandlePayload = feature_model.CandlePayload
    base_price = 82000.0
    candles = []
    for i in range(count):
        ts = datetime(2026, 3, 1 + i // 24, i % 24, 0, 0, tzinfo=UTC)
        p = base_price + (i * 50) + ((-1) ** i * 30)
        candles.append(
            CandlePayload(
                timestamp=ts, open=p - 10, high=p + 40, low=p - 50,
                close=p, volume=1000 + i * 10,
            )
        )
    return candles


def _make_feature_snapshot():
    candles = _make_candles(30)
    features = indicators_mod.calculate_features("BTCUSDT", candles)
    return signal_model.FeatureSnapshot(
        asset="BTCUSDT", timestamp=features.timestamp, close=features.close,
        volume=features.volume, rsi_14=features.rsi_14,
        macd=features.macd, macd_signal=features.macd_signal,
        bb_upper=features.bb_upper, bb_lower=features.bb_lower,
        sma_20=features.sma_20, vwap=features.vwap,
    )


# ===== market-data validation =====

class TestMarketDataValidation:
    def test_valid_candle_accepted(self):
        candle = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            open=82000, high=82300, low=81800, close=82150, volume=1200,
        )
        result = validator_mod.validate_candle_transition(None, candle)
        assert result.accepted

    def test_anomaly_detection_on_spike(self):
        c1 = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 29, tzinfo=UTC),
            open=80000, high=80500, low=79500, close=80000, volume=1000,
        )
        c2 = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            open=80000, high=120000, low=79000, close=119000, volume=5000,
        )
        result = validator_mod.validate_candle_transition(c1, c2)
        assert result.accepted
        assert result.anomaly_detected


# ===== feature computation =====

class TestFeatureComputation:
    def test_indicators_computed(self):
        candles = _make_candles(30)
        features = indicators_mod.calculate_features("BTCUSDT", candles)
        assert features.asset == "BTCUSDT"
        assert features.rsi_14 is not None
        assert features.macd is not None
        assert features.sma_20 is not None
        assert features.vwap is not None

    def test_features_compatible_with_signal(self):
        snapshot = _make_feature_snapshot()
        assert snapshot.close is not None
        assert snapshot.rsi_14 is not None


# ===== signal scoring =====

class TestSignalScoring:
    def test_produces_valid_response(self):
        snapshot = _make_feature_snapshot()
        sig = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        assert sig.asset == "BTCUSDT"
        assert sig.direction in ("BUY", "SELL", "HOLD")
        assert isinstance(sig.components, dict)

    def test_low_threshold_triggers(self):
        snapshot = _make_feature_snapshot()
        sig = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.01)
        if abs(sig.signal_score) > 0.01:
            assert sig.threshold_crossed

    def test_high_threshold_holds(self):
        snapshot = _make_feature_snapshot()
        sig = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=99.0)
        assert not sig.threshold_crossed
        assert sig.direction == "HOLD"

    def test_external_context_modifies_score(self):
        snapshot = _make_feature_snapshot()
        base = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.6)
        ext = signal_model.ExternalContextSnapshot(
            asset="BTCUSDT", timestamp=datetime(2026, 3, 30, tzinfo=UTC),
            news_sentiment=1.0, onchain_score=0.8, fear_greed_index=80,
        )
        with_ext = scoring_mod.build_signal_response(
            "BTCUSDT", snapshot, threshold=0.6,
            external_context=ext, external_signal_weight=0.35,
        )
        assert with_ext.signal_score != base.signal_score


# ===== crypto-agent decision loop =====

class TestCryptoAgentDecision:
    def test_decision_loop(self, monkeypatch):
        agent_engine = _isolate_load("crypto-agent", "app.core.engine")
        agent_models = _isolate_load("crypto-agent", "app.models.agent")

        snapshot = _make_feature_snapshot()
        signal = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.3)

        class StubSignal:
            def get_latest_signal(self, asset, *, user_id=None):
                return agent_models.SignalSnapshot(
                    asset=asset, signal_score=signal.signal_score,
                    threshold=signal.threshold,
                    threshold_crossed=signal.threshold_crossed,
                    direction=signal.direction, components=signal.components,
                    feature_timestamp=datetime.now(UTC),
                    reference_price=signal.reference_price,
                )

        class StubStrategy:
            def get_active_strategy(self, at, *, user_id=None):
                return agent_models.StrategySnapshot(
                    id="s1", name="Momentum", asset_type=at,
                    indicators=["rsi_14", "macd"],
                    weights={"rsi": 0.5, "macd": 0.5},
                    thresholds={"entry": 0.3}, version="v1", status="ACTIVE",
                )

        class StubMemory:
            recorded = []
            def search(self, r):
                return agent_models.MemorySearchResponse(query=r, items=[])
            def record(self, r):
                self.recorded.append(r); return r

        class StubLlm:
            def generate_reasoning(self, **kw):
                return "integration reasoning"

        class StubPub:
            published = []
            def publish_agent_action(self, d, o):
                self.published.append((d, o))

        stub_signal = StubSignal()
        stub_strategy = StubStrategy()
        stub_memory = StubMemory()
        stub_llm = StubLlm()
        stub_pub = StubPub()
        monkeypatch.setattr(agent_engine, "signal_client", stub_signal)
        monkeypatch.setattr(agent_engine, "strategy_client", stub_strategy)
        monkeypatch.setattr(agent_engine, "memory_client", stub_memory)
        monkeypatch.setattr(agent_engine, "llm_gateway_client", stub_llm)
        monkeypatch.setattr(agent_engine, "publisher", stub_pub)

        # Also patch graph._clients to avoid `import app.core.engine` at runtime
        agent_graph = _isolate_load("crypto-agent", "app.core.graph")
        monkeypatch.setattr(agent_graph, "_clients", lambda: (stub_signal, stub_memory, stub_strategy, stub_llm, stub_pub))

        def _stub_build_order(decision, shadow_override=False):
            return {"user_id": decision.user_id, "exchange": "binance", "asset": decision.asset,
                    "side": decision.action, "quantity": 0.01, "price": 82000,
                    "requested_notional": 820, "max_notional": 5000,
                    "current_drawdown": 0.01, "current_exposure": 0, "exposure_limit": 50000}

        monkeypatch.setattr(agent_graph, "_get_engine_helpers", lambda: (
            _stub_build_order,
            lambda *a, **kw: "integration reasoning",  # _fallback_reasoning
            lambda *a, **kw: [],    # _risk_pre_check
        ))

        decision = agent_engine.run_decision_loop(
            "BTCUSDT", user_id="test-user", correlation_id="corr-int",
        )
        assert decision.asset == "BTCUSDT"
        assert decision.action in ("BUY", "SELL", "HOLD")
        assert decision.user_id == "test-user"
        assert "integration reasoning" in decision.reasoning

        if decision.threshold_crossed and decision.action in ("BUY", "SELL"):
            assert len(stub_pub.published) == 1


# ===== order processing =====

class TestOrderProcessing:
    def test_order_fill_pipeline(self, monkeypatch):
        order_engine = _isolate_load("order-service", "app.core.engine")
        order_models = _isolate_load("order-service", "app.models.order")

        class StubRisk:
            def approve(self, p):
                return {"approved": True, "reason": "approved"}

        class StubExchange:
            def place(self, p):
                return {"status": "FILLED", "order_id": "ex-int"}

        class StubCred:
            def get(self, uid, exch):
                return {"user_id": uid, "exchange": exch, "sandbox": True, "label": "t"}

        class StubPortfolio:
            def apply_fill(self, p, *, order_id, status):
                return {
                    "user_id": p.user_id, "positions": {p.asset: p.quantity},
                    "average_entry_prices": {p.asset: p.price}, "recent_fills": [],
                    "total_exposure": p.quantity * p.price, "rebalance_needed": False,
                }

        class StubStats:
            def record_trade(self, p, *, order_status, order_id=None):
                return {
                    "user_id": p.user_id, "trade_count": 1,
                    "total_return": 0, "win_rate": 0, "drift_detected": False,
                }

        class StubRepo:
            config = order_models.ExecutionConfig(
                live_trading_enabled=False, allowed_exchanges=["binance"],
                default_shadow_mode=True, strict_runtime=False,
            )
            def get_execution_config(self): return self.config
            def save(self, *a, **kw): pass
            def record_lifecycle(self, *a, **kw): pass

        class StubPub:
            events = []
            def publish_risk_triggered(self, **kw): pass
            def publish_order_created(self, p, oid): self.events.append("created")
            def publish_order_filled(self, p, r): self.events.append("filled")

        stub_pub = StubPub()
        monkeypatch.setattr(order_engine, "risk_client", StubRisk())
        monkeypatch.setattr(order_engine, "exchange_client", StubExchange())
        monkeypatch.setattr(order_engine, "credential_client", StubCred())
        monkeypatch.setattr(order_engine, "portfolio_client", StubPortfolio())
        monkeypatch.setattr(order_engine, "statistics_client", StubStats())
        monkeypatch.setattr(order_engine, "order_repository", StubRepo())
        monkeypatch.setattr(order_engine, "publisher", stub_pub)

        result = order_engine.process_order(
            order_models.OrderRequest(
                user_id="int-user", exchange="binance", asset="BTCUSDT",
                side="BUY", quantity=0.012, price=82000,
                requested_notional=1000, max_notional=5000,
                current_drawdown=0.01, current_exposure=0, exposure_limit=50000,
            )
        )
        assert result.status == "FILLED"
        assert result.portfolio is not None
        assert "filled" in stub_pub.events

    def test_risk_rejection(self, monkeypatch):
        order_engine = _isolate_load("order-service", "app.core.engine")
        order_models = _isolate_load("order-service", "app.models.order")

        class StubRisk:
            def approve(self, p):
                return {"approved": False, "reason": "drawdown_exceeded"}

        class StubRepo:
            config = order_models.ExecutionConfig(
                live_trading_enabled=False, allowed_exchanges=["binance"],
                default_shadow_mode=True, strict_runtime=False,
            )
            def get_execution_config(self): return self.config
            def save(self, *a, **kw): pass
            def record_lifecycle(self, *a, **kw): pass

        class StubPub:
            events = []
            def publish_risk_triggered(self, **kw): self.events.append("risk")
            def publish_order_created(self, p, oid): pass
            def publish_order_filled(self, p, r): pass

        stub_pub = StubPub()
        monkeypatch.setattr(order_engine, "risk_client", StubRisk())
        monkeypatch.setattr(order_engine, "order_repository", StubRepo())
        monkeypatch.setattr(order_engine, "publisher", stub_pub)

        result = order_engine.process_order(
            order_models.OrderRequest(
                user_id="u1", exchange="binance", asset="BTCUSDT",
                side="BUY", quantity=1, price=82000,
                requested_notional=82000, max_notional=5000,
                current_drawdown=0.5, current_exposure=100000, exposure_limit=50000,
            )
        )
        assert result.status == "REJECTED"
        assert result.risk_reason == "drawdown_exceeded"


# ===== Full E2E =====

class TestFullCryptoE2E:
    def test_candle_to_order(self, monkeypatch):
        """Candle → features → signal → decision → order."""
        # 1. Validate
        candle = candle_model.CandlePayload(
            timestamp=datetime(2026, 3, 30, 12, 0, tzinfo=UTC),
            open=83000, high=83500, low=82800, close=83200, volume=1500,
        )
        assert validator_mod.validate_candle_transition(None, candle).accepted

        # 2. Compute features
        candles = _make_candles(29)
        candles.append(feature_model.CandlePayload(
            timestamp=candle.timestamp, open=candle.open,
            high=candle.high, low=candle.low,
            close=candle.close, volume=candle.volume,
        ))
        features = indicators_mod.calculate_features("BTCUSDT", candles)
        assert features.close == 83200

        # 3. Score signal
        snapshot = signal_model.FeatureSnapshot(
            asset="BTCUSDT", timestamp=features.timestamp, close=features.close,
            volume=features.volume, rsi_14=features.rsi_14,
            macd=features.macd, macd_signal=features.macd_signal,
            bb_upper=features.bb_upper, bb_lower=features.bb_lower,
            sma_20=features.sma_20, vwap=features.vwap,
        )
        signal = scoring_mod.build_signal_response("BTCUSDT", snapshot, threshold=0.3)

        # 4. Agent decision
        agent_engine = _isolate_load("crypto-agent", "app.core.engine")
        agent_models = _isolate_load("crypto-agent", "app.models.agent")

        class StubSignal:
            def get_latest_signal(self, asset, *, user_id=None):
                return agent_models.SignalSnapshot(
                    asset=asset, signal_score=signal.signal_score,
                    threshold=signal.threshold,
                    threshold_crossed=signal.threshold_crossed,
                    direction=signal.direction, components=signal.components,
                    feature_timestamp=datetime.now(UTC),
                    reference_price=signal.reference_price,
                )

        class StubStrategy:
            def get_active_strategy(self, at, *, user_id=None):
                return agent_models.StrategySnapshot(
                    id="s1", name="E2E", asset_type=at,
                    indicators=["rsi_14"], weights={"rsi": 1.0},
                    thresholds={"entry": 0.3}, version="v1", status="ACTIVE",
                )

        class StubMemory:
            recorded = []
            def search(self, r):
                return agent_models.MemorySearchResponse(query=r, items=[])
            def record(self, r):
                self.recorded.append(r); return r

        class StubLlm:
            def generate_reasoning(self, **kw): return "e2e"

        class StubAgentPub:
            published = []
            def publish_agent_action(self, d, o):
                self.published.append((d, o))

        stub_signal = StubSignal()
        stub_strategy = StubStrategy()
        stub_memory = StubMemory()
        stub_llm = StubLlm()
        apub = StubAgentPub()
        monkeypatch.setattr(agent_engine, "signal_client", stub_signal)
        monkeypatch.setattr(agent_engine, "strategy_client", stub_strategy)
        monkeypatch.setattr(agent_engine, "memory_client", stub_memory)
        monkeypatch.setattr(agent_engine, "llm_gateway_client", stub_llm)
        monkeypatch.setattr(agent_engine, "publisher", apub)

        # Patch graph._clients to avoid `import app.core.engine` at runtime
        agent_graph = _isolate_load("crypto-agent", "app.core.graph")
        monkeypatch.setattr(agent_graph, "_clients", lambda: (stub_signal, stub_memory, stub_strategy, stub_llm, apub))

        def _stub_build_order_e2e(decision, shadow_override=False):
            return {"user_id": decision.user_id, "exchange": "binance", "asset": decision.asset,
                    "side": decision.action, "quantity": 0.01, "price": 82000,
                    "requested_notional": 820, "max_notional": 5000,
                    "current_drawdown": 0.01, "current_exposure": 0, "exposure_limit": 50000}

        monkeypatch.setattr(agent_graph, "_get_engine_helpers", lambda: (
            _stub_build_order_e2e,
            lambda *a, **kw: "e2e",  # _fallback_reasoning
            lambda *a, **kw: [],    # _risk_pre_check
        ))

        decision = agent_engine.run_decision_loop(
            "BTCUSDT", user_id="e2e", correlation_id="e2e-corr",
        )
        assert decision.asset == "BTCUSDT"

        # 5. Order (if actionable)
        if decision.threshold_crossed and decision.action in ("BUY", "SELL"):
            _, order_req = apub.published[0]

            order_engine = _isolate_load("order-service", "app.core.engine")
            order_models = _isolate_load("order-service", "app.models.order")

            class StubRisk:
                def approve(self, p): return {"approved": True, "reason": "ok"}
            class StubExch:
                def place(self, p): return {"status": "FILLED", "order_id": "e"}
            class StubCred:
                def get(self, u, e):
                    return {"user_id": u, "exchange": e, "sandbox": True, "label": "e2e"}
            class StubPort:
                def apply_fill(self, p, *, order_id, status):
                    return {"user_id": p.user_id, "positions": {}, "average_entry_prices": {},
                            "recent_fills": [], "total_exposure": 0, "rebalance_needed": False}
            class StubStat:
                def record_trade(self, p, *, order_status, order_id=None):
                    return {"user_id": p.user_id, "trade_count": 1, "total_return": 0,
                            "win_rate": 0, "drift_detected": False}
            class StubRepo:
                config = order_models.ExecutionConfig(
                    live_trading_enabled=False, allowed_exchanges=["binance"],
                    default_shadow_mode=True, strict_runtime=False,
                )
                def get_execution_config(self): return self.config
                def save(self, *a, **kw): pass
                def record_lifecycle(self, *a, **kw): pass
            class StubOPub:
                events = []
                def publish_risk_triggered(self, **kw): pass
                def publish_order_created(self, p, oid): self.events.append("c")
                def publish_order_filled(self, p, r): self.events.append("f")

            opub = StubOPub()
            monkeypatch.setattr(order_engine, "risk_client", StubRisk())
            monkeypatch.setattr(order_engine, "exchange_client", StubExch())
            monkeypatch.setattr(order_engine, "credential_client", StubCred())
            monkeypatch.setattr(order_engine, "portfolio_client", StubPort())
            monkeypatch.setattr(order_engine, "statistics_client", StubStat())
            monkeypatch.setattr(order_engine, "order_repository", StubRepo())
            monkeypatch.setattr(order_engine, "publisher", opub)

            res = order_engine.process_order(order_models.OrderRequest(
                user_id=order_req["user_id"], exchange=order_req["exchange"],
                asset=order_req["asset"], side=order_req["side"],
                quantity=order_req["quantity"], price=order_req["price"],
                requested_notional=order_req["requested_notional"],
                max_notional=order_req["max_notional"],
                current_drawdown=order_req["current_drawdown"],
                current_exposure=order_req["current_exposure"],
                exposure_limit=order_req["exposure_limit"],
                correlation_id=decision.correlation_id,
            ))
            assert res.status == "FILLED"
