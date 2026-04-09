"""Global FormulaMAB instance shared across engine and outcome_consumer.

Extracted to its own module to avoid circular imports between
engine.py and outcome_consumer.py.
"""
from __future__ import annotations

from app.core.bandit import FormulaMAB
from shared.formulas import formula_registry

formula_mab = FormulaMAB(formula_registry.list_names())
