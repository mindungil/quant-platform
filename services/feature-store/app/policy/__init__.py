"""Feature-store policy plugin — proprietary microstructure & fracdiff features.

Standard OHLCV-derived indicators (RSI, MACD, ATR, ADX, Bollinger, …) remain
open. Proprietary additions (microstructure, fractional differencing tuning,
sentiment fusion) plug in here.
"""
from __future__ import annotations

from typing import Any, Protocol

from shared.plugin_policy import load_policy


class ExtraFeaturesPolicy(Protocol):
    def compute(self, candles_df: Any) -> dict: ...


class _NoopExtraFeatures:
    def compute(self, candles_df): return {}


_policy: ExtraFeaturesPolicy | None = None


def register_extra_features_policy(p: ExtraFeaturesPolicy) -> None:
    global _policy; _policy = p


def get_extra_features_policy() -> ExtraFeaturesPolicy:
    return _policy or _NoopExtraFeatures()


load_policy("QUANT_FEATURE_POLICY", plugin_label="feature_store.extra")
