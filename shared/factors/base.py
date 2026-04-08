"""Factor base class for the quant trading platform."""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class Factor:
    """Base class for all alpha factors.

    Each factor takes a features dict and returns a normalized score in [-1, 1].
    Returns 0.0 if required data is missing.
    """
    name: str
    category: str  # technical, momentum, reversion, volatility, derivatives, sentiment
    description: str = ""

    def compute(self, features: dict) -> float:
        raise NotImplementedError

    @staticmethod
    def _safe_get(features: dict, key: str, default: float = 0.0) -> float:
        val = features.get(key)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        return float(val)

    @staticmethod
    def _tanh_norm(value: float, scale: float = 1.0) -> float:
        """Normalize to [-1, 1] via tanh."""
        if scale <= 0:
            return 0.0
        return math.tanh(value / scale)

    @staticmethod
    def _linear_norm(value: float, center: float, half_range: float) -> float:
        """Linear normalize to [-1, 1] centered at `center`."""
        if half_range <= 0:
            return 0.0
        return max(-1.0, min(1.0, (value - center) / half_range))
