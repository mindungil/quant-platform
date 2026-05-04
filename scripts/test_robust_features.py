"""Test robust feature selection vs single-window IC_IR."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.features.engine import FeatureEngine
from shared.features.importance import (
    compute_rolling_ic,
    rank_features,
    robust_feature_ranking,
)

df = pd.read_csv("/home/ubuntu/quant/data/ohlcv/BTCUSDT_1h_stitched.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"])
df = df.set_index("timestamp").sort_index().astype(
    {c: float for c in ["open", "high", "low", "close", "volume"]}
)
df = df.iloc[-10000:]

engine = FeatureEngine()
fm = engine.generate(df)
fwd = df["close"].pct_change().shift(-1).fillna(0.0)

train_end = int(len(df) * 0.7)
train_feats = fm.features.iloc[:train_end]
train_fwd = fwd.iloc[:train_end]
test_feats = fm.features.iloc[train_end:]
test_fwd = fwd.iloc[train_end:]


def oos_ic(features, test_f, test_y):
    ics = []
    for c in features:
        if c in test_f.columns:
            ic = test_f[c].corr(test_y, method="spearman")
            if not np.isnan(ic):
                ics.append(abs(ic))
    return float(np.mean(ics)) if ics else 0.0


# Method A: single-window IC_IR
ic_panel = compute_rolling_ic(train_feats, train_fwd, window=500)
old_rep = rank_features(ic_panel, top_n=30, min_ic_ir=0.2)
top10_old = old_rep.top_features[:10]
bot10_old = [c for c, v in sorted(old_rep.ic_ir.items(), key=lambda x: x[1])[:10]]
old_top_ic = oos_ic(top10_old, test_feats, test_fwd)
old_bot_ic = oos_ic(bot10_old, test_feats, test_fwd)

# Method B: robust
t0 = time.perf_counter()
ranking = robust_feature_ranking(
    train_feats, train_fwd, ic_windows=[250, 500, 1000], n_bootstrap=8
)
dt = time.perf_counter() - t0
top10_new = ranking.head(10)["feature"].tolist()
bot10_new = ranking.tail(10)["feature"].tolist()
new_top_ic = oos_ic(top10_new, test_feats, test_fwd)
new_bot_ic = oos_ic(bot10_new, test_feats, test_fwd)

print(f"Robust ranking time: {dt:.1f}s")
print()
header = f"{'Method':<35}{'OOS top-10 IC':<16}{'OOS bot-10 IC':<16}Ratio"
print(header)
print("-" * 80)
print(
    f"{'Single-window IC_IR':<35}"
    f"{old_top_ic:<16.4f}{old_bot_ic:<16.4f}{old_top_ic/max(old_bot_ic,1e-9):.2f}x"
)
print(
    f"{'Robust multi-window bootstrap':<35}"
    f"{new_top_ic:<16.4f}{new_bot_ic:<16.4f}{new_top_ic/max(new_bot_ic,1e-9):.2f}x"
)
print()
print("Top-5 robust:")
print(ranking.head(5).to_string(index=False))
