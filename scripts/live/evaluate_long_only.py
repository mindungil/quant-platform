"""Long-only alpha evaluation for Upbit 현물.

Upbit 현물은 숏 불가 → 음수 포지션은 0으로 clip.
L/S 대비 얼마나 Sharpe가 떨어지는지 정직하게 측정.

Upbit 수수료 모델 (기본): 시장가 0.05% (5 bps) 양쪽 = round-trip 10 bps.
Binance Futures 기본 4 bps 대비 ~2.5x 비쌈.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.alpha.base import AlphaConfig
from shared.alpha.kalman_trend import KalmanTrendAlpha
from shared.alpha.momentum_ensemble import MomentumEnsembleAlpha
from shared.alpha.vol_breakout import VolBreakoutAlpha
from shared.alpha.trend_breakout import TrendBreakoutAlpha


ALPHAS = {
    "momentum_ensemble": MomentumEnsembleAlpha,
    "kalman_trend": KalmanTrendAlpha,
    "vol_breakout": VolBreakoutAlpha,
    "trend_breakout": TrendBreakoutAlpha,
}


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = f"/home/ubuntu/quant/data/ohlcv/{symbol}_1h_stitched.csv"
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    return df.astype({c: float for c in ["open", "high", "low", "close", "volume"]})


def evaluate(pos: pd.Series, bar_ret: pd.Series, cost_bps: float) -> dict:
    """Cost-adjusted Sharpe + MDD + turnover."""
    pnl = (pos.shift(1).fillna(0.0) * bar_ret).fillna(0.0)
    cost = pos.diff().abs().fillna(0.0) * cost_bps * 1e-4
    net = pnl - cost
    if net.std() < 1e-12:
        return {"sharpe": 0.0, "mdd": 0.0, "turnover": 0.0, "return": 0.0, "active_pct": 0.0}
    sharpe = float(net.mean() / net.std() * np.sqrt(24 * 365))
    eq = np.cumprod(1 + net)
    mdd = float((eq / eq.cummax() - 1).min())
    return {
        "sharpe": sharpe,
        "mdd": abs(mdd),
        "turnover": float(pos.diff().abs().mean()),
        "return": float(eq.iloc[-1] - 1),
        "active_pct": float((pos.abs() > 0.01).mean()),
    }


def run_alpha(alpha_cls, df: pd.DataFrame) -> pd.Series:
    cfg = AlphaConfig(name=alpha_cls.__name__.lower().replace("alpha", ""), asset_type="crypto")
    alpha = alpha_cls(cfg)
    return alpha.generate(df).position


def main():
    # Upbit 수수료 모델: 시장가 5 bps 각 방향. Binance futures 4 bps.
    UPBIT_COST_BPS = 5.0
    BINANCE_COST_BPS = 4.0

    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]

    rows = []
    for symbol in symbols:
        print(f"\n{'=' * 80}\n  {symbol}\n{'=' * 80}")
        df = load_ohlcv(symbol)
        bar_ret = df["close"].pct_change().fillna(0.0)
        print(f"  {len(df):,} bars | {df.index[0].date()} → {df.index[-1].date()}")

        for name, cls in ALPHAS.items():
            t0 = time.time()
            pos_ls = run_alpha(cls, df)        # long/short (native)
            pos_lo = pos_ls.clip(lower=0)      # long-only (short → flat)
            dt = time.time() - t0

            ls_binance = evaluate(pos_ls, bar_ret, BINANCE_COST_BPS)
            lo_upbit = evaluate(pos_lo, bar_ret, UPBIT_COST_BPS)

            delta_sr = lo_upbit["sharpe"] - ls_binance["sharpe"]
            rows.append({
                "symbol": symbol,
                "alpha": name,
                "ls_binance_sr": ls_binance["sharpe"],
                "lo_upbit_sr": lo_upbit["sharpe"],
                "delta_sr": delta_sr,
                "lo_mdd": lo_upbit["mdd"],
                "lo_return": lo_upbit["return"],
                "lo_turnover": lo_upbit["turnover"],
                "lo_active_pct": lo_upbit["active_pct"],
                "time_s": dt,
            })
            print(
                f"  {name:22s}  L/S(Bin)={ls_binance['sharpe']:+.3f}  "
                f"LO(Upbit)={lo_upbit['sharpe']:+.3f}  Δ={delta_sr:+.3f}  "
                f"MDD={lo_upbit['mdd']:.1%}  ret={lo_upbit['return']:+.1%}  "
                f"active={lo_upbit['active_pct']:.1%}"
            )

    res = pd.DataFrame(rows)
    out_path = Path("/home/ubuntu/quant/data/analysis/long_only_eval.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_path, index=False)
    print(f"\n\n  Saved → {out_path}")

    # Per-alpha summary
    print(f"\n{'=' * 80}\n  SUMMARY (alpha-avg across symbols)\n{'=' * 80}")
    agg = res.groupby("alpha").agg({
        "ls_binance_sr": "mean",
        "lo_upbit_sr": "mean",
        "delta_sr": "mean",
        "lo_return": "mean",
        "lo_active_pct": "mean",
    }).sort_values("lo_upbit_sr", ascending=False)
    print(agg.round(3).to_string())

    # Honesty check
    print(f"\n{'=' * 80}\n  정직한 결론\n{'=' * 80}")
    good = agg[agg["lo_upbit_sr"] > 0.3]
    if len(good) == 0:
        print("  ⚠️  Long-only + Upbit 수수료에서 SR > 0.3 인 알파 없음.")
        print("  → 현 상태로 Upbit 배포는 수익성 의문.")
    else:
        print(f"  ✓ {len(good)}개 알파가 SR > 0.3 유지:")
        for a in good.index:
            print(f"    - {a}: avg SR = {good.loc[a, 'lo_upbit_sr']:.3f}")
    loss = (agg["ls_binance_sr"] - agg["lo_upbit_sr"]).mean()
    print(f"\n  L/S → LO 전환으로 평균 Sharpe 손실: {loss:.3f}")
    print(f"  (백테스트 기준, 실거래는 slippage/fill-rate로 추가 10-30% 저하 예상)")


if __name__ == "__main__":
    main()
