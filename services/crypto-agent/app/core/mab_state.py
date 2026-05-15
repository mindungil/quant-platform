"""Global FormulaMAB instance shared across engine and outcome_consumer.

Extracted to its own module to avoid circular imports between
engine.py and outcome_consumer.py.
"""
from __future__ import annotations

from app.core.bandit import FormulaMAB
try:
    from shared.formulas import formula_registry
except ImportError:
    formula_registry = None  # type: ignore

formula_mab = FormulaMAB(formula_registry.list_names())
