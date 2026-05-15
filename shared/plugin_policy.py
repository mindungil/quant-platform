"""Open-core policy plugin helper.

Each service that has an IP-bearing policy module (signal-service scoring,
crypto-agent recommender, risk-service drawdown gate, etc.) exposes a
`services/<svc>/app/policy/` package with:

  - A Protocol that defines the policy surface.
  - A no-op default implementation so the service can boot without a plugin.
  - register_<policy>() / get_<policy>() entrypoints.
  - A module-load call to load_policy(env_var) below.

Private repos (e.g. quant-alpha) provide concrete implementations that
import this module and call register_<policy>(MyPolicyImpl()) at import time.
The compose env wires that import in:

  QUANT_SIGNAL_POLICY=quant_alpha.policies.signal_service.scoring
"""
from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger("plugin_policy")


def load_policy(env_var: str, *, plugin_label: str) -> None:
    """Import the module named by *env_var*.

    Side-effect-only: the imported module is expected to register its policy
    class against the appropriate service-level registry. Failures are
    warned, not raised — the service falls back to its no-op default.
    """
    mod_name = (os.environ.get(env_var) or "").strip()
    if not mod_name:
        return
    try:
        importlib.import_module(mod_name)
        log.info("policy_plugin_loaded", extra={"label": plugin_label, "module": mod_name})
    except Exception as exc:
        log.warning(
            "policy_plugin_load_failed",
            extra={
                "label": plugin_label,
                "module": mod_name,
                "error": str(exc)[:200],
            },
        )
