"""Paper Portfolio — track agent decisions as virtual trades.

No real money. Simulates BUY/SELL/HOLD decisions and computes:
- Cumulative return
- Max drawdown
- Sharpe-like ratio
- Win rate per direction
- Current open position

Pure analytics — no capital required.
"""
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("statistics-service")


def compute_paper_portfolio(decisions: list[dict], current_price: float) -> dict:
    """Compute virtual portfolio performance from a chronological list of decisions.

    Each decision: {timestamp, action, reference_price, asset}

    State machine:
    - HOLD → no change
    - BUY → open position (if no open) at reference_price
    - SELL → close position (if open) at reference_price, record PnL
    """
    if not decisions:
        return {
            "total_decisions": 0,
            "total_trades": 0,
            "open_position": None,
            "cumulative_return_pct": 0,
            "max_drawdown_pct": 0,
            "win_rate": 0,
            "trades": [],
        }

    # Sort chronologically
    sorted_decisions = sorted(decisions, key=lambda d: d.get("timestamp", ""))

    open_position = None  # {entry_price, entry_time}
    completed_trades = []  # [{entry, exit, pnl_pct, duration_hours, action_sequence}]

    initial_capital = 10000.0  # virtual $10k starting capital
    capital = initial_capital
    equity_curve = [initial_capital]

    for d in sorted_decisions:
        action = d.get("action", "HOLD")
        ref_price = d.get("reference_price")
        ts = d.get("timestamp", "")

        if not ref_price or ref_price <= 0:
            continue

        if action == "BUY":
            if open_position is None:
                open_position = {
                    "entry_price": ref_price,
                    "entry_time": ts,
                    "decision_id": d.get("decision_id"),
                }
        elif action == "SELL":
            if open_position is not None:
                pnl_pct = ((ref_price - open_position["entry_price"]) / open_position["entry_price"]) * 100
                # Update virtual capital
                capital = capital * (1 + pnl_pct / 100)
                equity_curve.append(capital)

                try:
                    entry_dt = datetime.fromisoformat(open_position["entry_time"].replace("Z", "+00:00"))
                    exit_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    hours = (exit_dt - entry_dt).total_seconds() / 3600
                except Exception:
                    hours = 0

                completed_trades.append({
                    "entry_price": open_position["entry_price"],
                    "exit_price": ref_price,
                    "pnl_pct": round(pnl_pct, 4),
                    "duration_hours": round(hours, 1),
                    "entry_time": open_position["entry_time"],
                    "exit_time": ts,
                    "win": pnl_pct > 0,
                })
                open_position = None

    # If position still open, mark-to-market with current price
    open_pnl = 0
    if open_position is not None and current_price > 0:
        open_pnl = ((current_price - open_position["entry_price"]) / open_position["entry_price"]) * 100
        equity_curve.append(capital * (1 + open_pnl / 100))

    # Cumulative return
    final_equity = equity_curve[-1]
    cumulative_return = ((final_equity - initial_capital) / initial_capital) * 100

    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = ((peak - v) / peak) * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Win rate
    wins = sum(1 for t in completed_trades if t["win"])
    win_rate = wins / len(completed_trades) if completed_trades else 0

    # Average trade
    avg_pnl = sum(t["pnl_pct"] for t in completed_trades) / len(completed_trades) if completed_trades else 0

    return {
        "initial_capital": initial_capital,
        "current_equity": round(final_equity, 2),
        "cumulative_return_pct": round(cumulative_return, 2),
        "total_decisions": len(sorted_decisions),
        "total_trades": len(completed_trades),
        "win_count": wins,
        "loss_count": len(completed_trades) - wins,
        "win_rate": round(win_rate, 4),
        "avg_pnl_pct": round(avg_pnl, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "open_position": {
            "entry_price": open_position["entry_price"],
            "entry_time": open_position["entry_time"],
            "current_price": current_price,
            "unrealized_pnl_pct": round(open_pnl, 2),
        } if open_position else None,
        "recent_trades": completed_trades[-10:] if completed_trades else [],
        "equity_curve": [round(e, 2) for e in equity_curve[-50:]],  # last 50 points
    }
