#!/usr/bin/env python3
"""Hourly alpha health check. Logs status and alerts on degradation."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from shared.engine.config import load_config  # noqa: E402
from shared.engine.health import AlphaHealthMonitor, HealthStatus  # noqa: E402
from shared.engine.logger import PerformanceLogger  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config" / "v4_production.json"
LOG_DIR = REPO_ROOT / "data" / "metrics" / "health_log"
UTC = timezone.utc


def main() -> int:
    cfg = load_config(CONFIG_PATH)
    logger = PerformanceLogger(cfg.metrics_dir)
    monitor = AlphaHealthMonitor(
        windows_hours=cfg.health_windows_hours,
        sharpe_warn=cfg.health_sharpe_warn,
        sharpe_critical=cfg.health_sharpe_critical,
        weight_reduction=cfg.health_weight_reduction,
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC)
    results = {}

    for sym in cfg.symbols[:3]:  # Top 3 symbols
        metrics = logger.compute_rolling_metrics(sym, cfg.health_windows_hours)
        if not metrics:
            continue

        print(f"\n  {sym} health:")
        total = metrics.get("total", {})
        for w, sh in total.items():
            print(f"    total {w}: sh={sh:+.2f}")

        alphas = metrics.get("alphas", {})
        for name, windows in alphas.items():
            shortest_key = f"{cfg.health_windows_hours[0]}h"
            sh = windows.get(shortest_key, 0)
            if sh < cfg.health_sharpe_critical:
                status = "CRITICAL"
            elif sh < cfg.health_sharpe_warn:
                status = "DEGRADED"
            else:
                status = "HEALTHY"
            print(f"    {name:20s} {shortest_key}={sh:+.2f} → {status}")
            results[f"{sym}/{name}"] = {"sharpe": sh, "status": status}

    # Save log
    log_path = LOG_DIR / f"health_{ts.strftime('%Y%m%d_%H%M')}.json"
    with open(log_path, "w") as f:
        json.dump({"timestamp": ts.isoformat(), "results": results}, f, indent=2)

    # Alert on critical
    critical = [k for k, v in results.items() if v["status"] == "CRITICAL"]
    if critical:
        print(f"\n  ⚠ CRITICAL ALPHAS: {critical}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
