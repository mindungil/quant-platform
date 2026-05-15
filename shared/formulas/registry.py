from __future__ import annotations
from shared.formulas.base import BaseFormula


class FormulaRegistry:
    """Central registry of all available formulas."""

    def __init__(self) -> None:
        self._formulas: dict[str, BaseFormula] = {}

    def register(self, formula: BaseFormula) -> None:
        self._formulas[formula.name] = formula

    def get(self, name: str) -> BaseFormula | None:
        return self._formulas.get(name)

    def list_all(self) -> list[BaseFormula]:
        return list(self._formulas.values())

    def list_names(self) -> list[str]:
        return list(self._formulas.keys())

    def get_for_regime(self, regime: str) -> list[BaseFormula]:
        """Return formulas that match or are compatible with a regime."""
        result = []
        for f in self._formulas.values():
            if f.best_regime == regime or f.best_regime == "any":
                result.append(f)
        return result

    def get_default(self) -> BaseFormula:
        return self._formulas.get("composite_adaptive") or list(self._formulas.values())[0]


formula_registry = FormulaRegistry()
