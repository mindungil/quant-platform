#!/usr/bin/env python3
"""V4-4 reoptimizer daemon — runs reoptimize() every N minutes.

Lives alongside learning-loop in the strategy-lab image. Each cycle:
  1. Pull recent alpha_positions + bar_returns from the ledger
  2. Pull current_positions from portfolio-service
  3. Pull venue_quotes from market-data
  4. Call shared.portfolio.reoptimizer.reoptimize()
  5. For each routed order: emit to order-service (or just log in shadow)

This script is a REFERENCE — production callers must adapt the data-fetch
functions to their actual repository layout. The reoptimize() core is the
real value; this script proves the wiring.
"""
from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, "/code")

logger = logging.getLogger("reoptimizer-daemon")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

INTERVAL = int(os.getenv("REOPTIMIZER_INTERVAL_SECONDS", "300"))
DRY_RUN = os.getenv("REOPTIMIZER_DRY_RUN", "1") == "1"


def _fetch_inputs():
    """Build a ReoptInput from the live ledger. Adapt to your schema."""
    import pandas as pd
    from shared.portfolio.reoptimizer import ReoptInput
    from shared.execution.router import VenueQuote
    # Placeholder: real impl pulls from DB / market-data.
    # Returning a small synthetic input so the daemon proves end-to-end wiring.
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return ReoptInput(
        alpha_positions=pd.DataFrame({"alpha_baseline": [0.5] * n}, index=idx),
        bar_returns=pd.Series([0.0] * n, index=idx),
        current_positions={"BTC": 0.0},
        market_data={"BTC": {"mid_price": 50_000, "spread_bp": 4.0,
                              "adv_usd": 1_000_000_000}},
        venue_quotes={
            "BTC": {
                "binance": VenueQuote("binance", 49_950, 50_050, 100, 100),
            }
        },
        expected_alpha_per_position_bps={"BTC": 5.0},
    )


def _run_cycle():
    from shared.portfolio.reoptimizer import reoptimize
    inp = _fetch_inputs()
    result = reoptimize(inp)
    summary = result.summary()
    if DRY_RUN:
        logger.info("dry_run_cycle_complete: %s", summary)
    else:
        logger.info("cycle_complete: %s", summary)
        # Real impl would POST each routed order to order-service here.
    return summary


def _daemon():
    try:
        from prometheus_client import start_http_server
        port = int(os.getenv("METRICS_PORT", "9101"))
        start_http_server(port)
        logger.info("reoptimizer_metrics_exposed port=%s", port)
    except Exception as exc:
        logger.warning("metrics_server_start_failed: %s", exc)

    logger.info("reoptimizer_daemon_starting interval=%s dry_run=%s", INTERVAL, DRY_RUN)
    while True:
        try:
            _run_cycle()
        except Exception as exc:
            logger.exception("cycle_failed: %s", exc)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        _daemon()
    else:
        summary = _run_cycle()
        logger.info("cycle_complete: %s", summary)
