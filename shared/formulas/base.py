from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc


@dataclass
class FormulaResult:
    """Output of a formula computation."""
    score: float              # [-1, 1] signal strength
    confidence: float         # [0, 1] how confident the formula is
    components: dict = field(default_factory=dict)
    formula_name: str = ""
    regime_label: str = ""


class BaseFormula:
    """Base class for all trading signal formulas."""
    name: str = "base"
    description: str = ""
    best_regime: str = "any"  # trending | sideways | reversal | breakout | any
    required_indicators: list[str] = []

    def compute(self, features: dict) -> FormulaResult:
        """Compute signal from features dict. Override in subclasses."""
        raise NotImplementedError

    def to_metadata(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "best_regime": self.best_regime,
            "required_indicators": self.required_indicators,
        }
