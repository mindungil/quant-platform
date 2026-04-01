from __future__ import annotations

import sys
from pathlib import Path

from common import load_env

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

load_env()

from shared.persistence import SqlStore  # noqa: E402


PLATFORM_TABLES = {
    "auth_users",
    "auth_refresh_tokens",
    "memory_records",
    "strategy_records",
    "credential_records",
    "order_events",
    "order_lifecycle_events",
    "execution_config",
    "portfolio_positions",
    "portfolio_fills",
    "statistics_trades",
    "risk_incidents",
    "exchange_order_audits",
    "orchestrator_snapshots",
}

MARKET_TABLES = {
    "market_candles",
    "market_anomalies",
    "feature_history",
    "signal_history",
}


def _table_names(store: SqlStore) -> set[str]:
    rows = store.fetch_all(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        """
    )
    return {row["table_name"] for row in rows}


def main() -> None:
    platform = SqlStore("postgresql+psycopg://postgres:postgres@localhost:5432/platform")
    market = SqlStore("postgresql+psycopg://postgres:postgres@localhost:5433/market")

    platform_tables = _table_names(platform)
    market_tables = _table_names(market)

    missing_platform = sorted(PLATFORM_TABLES - platform_tables)
    missing_market = sorted(MARKET_TABLES - market_tables)

    if missing_platform or missing_market:
        raise SystemExit(
            "missing_tables:"
            f" platform={missing_platform or '[]'}"
            f" market={missing_market or '[]'}"
        )

    print("migration smoke passed")


if __name__ == "__main__":
    main()
