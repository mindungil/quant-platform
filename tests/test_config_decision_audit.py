"""Config decision-audit guard.

Enforces that every production-config decision (parked symbol, alpha override,
parked alpha) carries a human-readable rationale. Without this, configs drift
silently and we lose the ability to answer "why is BNB clip_short=true?" or
"why was SOL re-promoted?" without spelunking git history.

Convention enforced:
  - symbols_parked.<SYM>          → must have `reason` (and optionally `parked_at`)
  - parked_alphas.<NAME>          → must have `reason` (and optionally `status`)
  - asset_overrides.<SYM>         → must have `note` OR `_decision_doc`
  - alphas.<NAME>.note            → recommended but not enforced (alphas list is
                                    self-evident from the registry comments)

The convention itself is in CLAUDE.md / docs — this test is the executable
backstop that catches commits which try to skip it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_CFG = REPO_ROOT / "config" / "v4_production.json"


def _load_cfg() -> dict:
    return json.loads(PROD_CFG.read_text())


def test_v4_production_config_loads():
    """Sanity: production config parses as JSON."""
    cfg = _load_cfg()
    assert isinstance(cfg, dict)
    assert "alphas" in cfg


def test_symbols_parked_have_reason():
    cfg = _load_cfg()
    for sym, entry in (cfg.get("symbols_parked") or {}).items():
        assert isinstance(entry, dict), f"symbols_parked.{sym} must be a dict"
        assert entry.get("reason"), (
            f"symbols_parked.{sym} missing 'reason' — every park decision needs "
            f"a rationale documenting why and when it was parked."
        )


def test_parked_alphas_have_reason():
    cfg = _load_cfg()
    for name, entry in (cfg.get("parked_alphas") or {}).items():
        assert isinstance(entry, dict), f"parked_alphas.{name} must be a dict"
        assert entry.get("reason"), (
            f"parked_alphas.{name} missing 'reason' — alpha demote decisions "
            f"must carry rationale (SR figures, regime hypothesis, review window)."
        )


def test_asset_overrides_have_decision_doc():
    """Every asset_overrides entry must carry a `note` or `_decision_doc`.

    Without this we can't answer 'why does BNBUSDT have clip_short=true?' or
    'who picked SOL's alpha set' without git archaeology.
    """
    cfg = _load_cfg()
    missing: list[str] = []
    for sym, entry in (cfg.get("asset_overrides") or {}).items():
        if not isinstance(entry, dict):
            continue
        has_doc = bool(entry.get("note") or entry.get("_decision_doc"))
        if not has_doc:
            missing.append(sym)
    assert not missing, (
        f"asset_overrides missing decision doc for: {missing}. "
        f"Add a `note` or `_decision_doc` field describing the override "
        f"(date, reason, validation evidence)."
    )


def test_clip_short_decisions_explained():
    """If clip_short=true, the note must mention long-only/clip/short rationale."""
    cfg = _load_cfg()
    for sym, entry in (cfg.get("asset_overrides") or {}).items():
        if not isinstance(entry, dict) or not entry.get("clip_short"):
            continue
        doc = (entry.get("note") or "") + " " + (entry.get("_decision_doc") or "")
        doc_l = doc.lower()
        assert any(k in doc_l for k in ("clip_short", "long-only", "long only", "short")), (
            f"asset_overrides.{sym}.clip_short=true but note doesn't explain "
            f"the long-only/short-clip decision. Note: {entry.get('note', '')[:120]}"
        )
