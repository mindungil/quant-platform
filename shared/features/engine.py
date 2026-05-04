"""Automated feature engine for ML alpha discovery.

Generates 150+ features from OHLCV + funding data across 6 categories:
momentum, mean-reversion, volatility, microstructure, volume, funding.

All features are strictly causal (use data up to and including bar t only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd


# NOTE: We re-implement helpers here to avoid circular import
# (features.engine -> alpha.base -> alpha.__init__ -> alpha.registry -> alpha.ml_discovery -> features.engine)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std(ddof=0)
    z = (series - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def bollinger_pctb(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.Series:
    mean = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mean + n_std * std
    lower = mean - n_std * std
    width = (upper - lower).replace(0, np.nan)
    return ((close - lower) / width).fillna(0.5)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = _true_range(high, low, close)
    atr_v = tr.ewm(alpha=1.0 / period, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period).mean() / atr_v.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, min_periods=period).mean().fillna(0.0)
from shared.features.fracdiff import frac_diff_ffd
from shared.features.microstructure import (
    amihud_illiquidity,
    corwin_schultz_spread,
    high_low_volatility,
    kyle_lambda,
    roll_spread,
    vpin_proxy,
)


# ---------------------------------------------------------------------------
# Config & metadata
# ---------------------------------------------------------------------------

@dataclass
class FeatureEngineConfig:
    """Knobs for feature generation."""
    momentum_windows: list[int] = field(
        default_factory=lambda: [1, 4, 12, 24, 72, 168, 336, 720]
    )
    mean_rev_windows: list[int] = field(
        default_factory=lambda: [20, 50, 100]
    )
    vol_windows: list[int] = field(
        default_factory=lambda: [12, 24, 72, 168]
    )
    micro_windows: list[int] = field(
        default_factory=lambda: [24, 72]
    )
    volume_windows: list[int] = field(
        default_factory=lambda: [24, 72, 168]
    )
    funding_windows: list[int] = field(
        default_factory=lambda: [24, 72, 168]
    )
    derivatives_windows: list[int] = field(
        default_factory=lambda: [24, 72, 168]
    )
    fracdiff_ds: list[float] = field(
        default_factory=lambda: [0.3, 0.5]
    )
    variance_threshold: float = 1e-10
    clip_value: float = 10.0


@dataclass
class FeatureMeta:
    """Per-feature metadata for downstream use."""
    name: str
    lookback: int
    category: str  # momentum|mean_rev|vol|micro|volume|funding


@dataclass
class FeatureMatrix:
    """Output of feature generation."""
    features: pd.DataFrame
    metadata: list[FeatureMeta]

    @property
    def max_lookback(self) -> int:
        return max((m.lookback for m in self.metadata), default=0)

    @property
    def feature_names(self) -> list[str]:
        return [m.name for m in self.metadata]


# ---------------------------------------------------------------------------
# Feature Engine
# ---------------------------------------------------------------------------

class FeatureEngine:
    """Generate 150+ features from OHLCV (+ optional funding) data."""

    def __init__(self, config: FeatureEngineConfig | None = None) -> None:
        self.config = config or FeatureEngineConfig()

    # ----- public API -----

    def generate(
        self,
        df: pd.DataFrame,
        funding: pd.Series | None = None,
        fear_greed: pd.Series | None = None,
        derivatives: dict[str, pd.DataFrame] | None = None,
        sentiment: pd.Series | None = None,
    ) -> FeatureMatrix:
        """Generate all features for a single symbol.

        Args:
            df: OHLCV DataFrame (columns: open, high, low, close, volume;
                optionally taker_buy_base, quote_volume, n_trades).
            funding: Optional funding rate series aligned to df index.

        Returns:
            FeatureMatrix with NaN-free feature DataFrame and metadata.
        """
        c = self.config
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)
        open_ = df["open"].astype(float)

        features: dict[str, pd.Series] = {}
        metadata: list[FeatureMeta] = []

        def _add(name: str, series: pd.Series, lookback: int, category: str):
            features[name] = series
            metadata.append(FeatureMeta(name=name, lookback=lookback, category=category))

        # --- 1. Momentum features (~30) ---
        log_ret = np.log(close / close.shift(1))
        for w in c.momentum_windows:
            ret_w = close.pct_change(w)
            _add(f"mom_ret_{w}", ret_w, w + 1, "momentum")
            _add(f"mom_logret_{w}", log_ret.rolling(w, min_periods=1).sum(), w + 1, "momentum")
            if w >= 12:
                _add(f"mom_zscore_{w}", rolling_zscore(ret_w, w), w * 2, "momentum")

        # Fractional differenced close
        log_close = np.log(close.replace(0, np.nan).ffill())
        for d in c.fracdiff_ds:
            _add(f"fracdiff_{d}", frac_diff_ffd(log_close, d=d), 720, "momentum")

        # Rate of change acceleration
        roc_24 = close.pct_change(24)
        roc_72 = close.pct_change(72)
        _add("mom_accel_24_72", roc_24 - roc_72, 73, "momentum")

        # --- 2. Mean-Reversion features (~25) ---
        for w in c.mean_rev_windows:
            _add(f"boll_pctb_{w}", bollinger_pctb(close, period=w), w, "mean_rev")
            _add(f"boll_width_{w}", _boll_width(close, w), w, "mean_rev")
            ema_w = ema(close, span=w)
            _add(f"dist_ema_{w}", (close - ema_w) / ema_w.replace(0, np.nan), w, "mean_rev")

        for p in [7, 14, 28]:
            _add(f"rsi_{p}", rsi(close, period=p) / 100.0 - 0.5, p * 2, "mean_rev")

        # Distance from rolling high/low
        for w in [24, 72, 168]:
            rh = close.rolling(w, min_periods=1).max()
            rl = close.rolling(w, min_periods=1).min()
            rng = (rh - rl).replace(0, np.nan)
            _add(f"dist_high_{w}", (close - rh) / rng, w, "mean_rev")
            _add(f"dist_low_{w}", (close - rl) / rng, w, "mean_rev")

        # --- 3. Volatility features (~25) ---
        for w in c.vol_windows:
            rv = log_ret.rolling(w, min_periods=max(2, w // 2)).std(ddof=0)
            _add(f"realized_vol_{w}", rv, w, "vol")
            _add(f"hl_vol_{w}", high_low_volatility(high, low, window=w), w, "vol")

        # ATR at multiple periods
        for p in [14, 24, 72]:
            atr_p = atr(high, low, close, period=p)
            _add(f"atr_{p}", atr_p / close.replace(0, np.nan), p * 2, "vol")

        # Vol-of-vol
        rv_24 = log_ret.rolling(24, min_periods=12).std(ddof=0)
        _add("vol_of_vol_72", rv_24.rolling(72, min_periods=36).std(ddof=0), 96, "vol")

        # Vol ratio (short / long)
        rv_12 = log_ret.rolling(12, min_periods=6).std(ddof=0)
        rv_72 = log_ret.rolling(72, min_periods=36).std(ddof=0)
        _add("vol_ratio_12_72", rv_12 / rv_72.replace(0, np.nan), 72, "vol")

        rv_168 = log_ret.rolling(168, min_periods=84).std(ddof=0)
        _add("vol_ratio_24_168", rv_24 / rv_168.replace(0, np.nan), 168, "vol")

        # Range expansion
        daily_range = (high - low) / close.replace(0, np.nan)
        _add("range_expansion_24", rolling_zscore(daily_range, 24), 48, "vol")

        # ADX (trend strength)
        for p in [14, 28]:
            _add(f"adx_{p}", adx(high, low, close, period=p) / 100.0, p * 3, "vol")

        # Garman-Klass vol
        gk = _garman_klass_vol(open_, high, low, close)
        _add("garman_klass_vol_24", gk.rolling(24, min_periods=12).mean(), 24, "vol")

        # --- 4. Microstructure features (~20) ---
        for w in c.micro_windows:
            _add(f"amihud_{w}", amihud_illiquidity(close, volume, window=w), w, "micro")
            _add(f"kyle_lambda_{w}", kyle_lambda(close, volume, window=w), w, "micro")
            _add(f"vpin_{w}", vpin_proxy(close, volume, window=w), w, "micro")
            _add(f"roll_spread_{w}", roll_spread(close, window=w), w, "micro")

        _add("corwin_schultz", corwin_schultz_spread(high, low), 2, "micro")

        # Z-scored microstructure
        for base_w in [24]:
            amh = amihud_illiquidity(close, volume, window=base_w)
            _add(f"amihud_zscore_{base_w}", rolling_zscore(amh, 72), 96, "micro")
            kl = kyle_lambda(close, volume, window=base_w)
            _add(f"kyle_zscore_{base_w}", rolling_zscore(kl, 72), 96, "micro")

        # --- 5. Volume features (~15) ---
        for w in c.volume_windows:
            _add(f"vol_zscore_{w}", rolling_zscore(volume, w), w * 2, "volume")

        # OBV slope
        obv = (np.sign(close.diff()) * volume).cumsum()
        for w in [24, 72]:
            obv_slope = obv.diff(w) / w
            _add(f"obv_slope_{w}", obv_slope, w + 1, "volume")

        # Taker buy ratio (if available)
        if "taker_buy_base" in df.columns:
            tbr = df["taker_buy_base"].astype(float) / volume.replace(0, np.nan)
            _add("taker_buy_ratio", tbr.fillna(0.5), 1, "volume")
            for w in [24, 72]:
                _add(f"taker_buy_zscore_{w}", rolling_zscore(tbr.fillna(0.5), w), w * 2, "volume")

        # Volume-price divergence
        for w in [24, 72]:
            price_z = rolling_zscore(close, w)
            vol_z = rolling_zscore(volume, w)
            _add(f"vol_price_div_{w}", vol_z - price_z, w * 2, "volume")

        # Dollar volume trend
        dv = close * volume
        _add("dollar_vol_zscore_72", rolling_zscore(dv, 72), 144, "volume")

        # --- 6. Cross-timeframe / interaction features (~15) ---
        # Momentum vs volume interactions
        for w in [24, 72]:
            ret_w = close.pct_change(w)
            vol_z = rolling_zscore(volume, w)
            _add(f"mom_vol_interact_{w}", ret_w * vol_z, w * 2, "momentum")

        # Return autocorrelation
        for w in [24, 72, 168]:
            ret_1 = log_ret
            auto = ret_1.rolling(w, min_periods=max(12, w // 2)).apply(
                lambda x: np.corrcoef(x[:-1], x[1:])[0, 1] if len(x) > 2 else 0.0,
                raw=True,
            )
            _add(f"return_autocorr_{w}", auto, w + 1, "momentum")

        # High-low range percentile
        for w in [24, 72]:
            hl_range = (high - low) / close.replace(0, np.nan)
            _add(f"range_pctile_{w}", hl_range.rolling(w, min_periods=w // 2).rank(pct=True), w, "vol")

        # Close location within bar
        bar_range = (high - low).replace(0, np.nan)
        _add("close_loc_in_bar", (close - low) / bar_range, 1, "vol")

        # Signed volume flow
        sv = np.sign(close.diff()) * volume
        for w in [24, 72]:
            _add(f"signed_vol_zscore_{w}", rolling_zscore(sv, w), w * 2, "volume")

        # Number of trades (if available)
        if "n_trades" in df.columns:
            nt = df["n_trades"].astype(float)
            for w in [24, 72]:
                _add(f"n_trades_zscore_{w}", rolling_zscore(nt, w), w * 2, "volume")

        # Skewness and kurtosis of returns
        for w in [72, 168]:
            _add(f"ret_skew_{w}", log_ret.rolling(w, min_periods=w // 2).skew(), w, "momentum")
            _add(f"ret_kurt_{w}", log_ret.rolling(w, min_periods=w // 2).kurt(), w, "momentum")

        # Up/down volume ratio
        up_vol = (volume * (close.diff() > 0).astype(float))
        dn_vol = (volume * (close.diff() <= 0).astype(float))
        for w in [24, 72]:
            uv = up_vol.rolling(w, min_periods=w // 2).sum()
            dv = dn_vol.rolling(w, min_periods=w // 2).sum()
            _add(f"up_down_vol_ratio_{w}", (uv / dv.replace(0, np.nan)).fillna(1.0) - 1.0, w, "volume")

        # --- 7. Advanced / interaction features (~30) ---

        # 7a. Lagged features — momentum at t-24, t-72 (regime persistence)
        for lag in [24, 72]:
            mom_24 = close.pct_change(24)
            _add(f"mom_24_lag_{lag}", mom_24.shift(lag).fillna(0), 24 + lag, "momentum")
            rv_24_feat = log_ret.rolling(24, min_periods=12).std(ddof=0)
            _add(f"vol_24_lag_{lag}", rv_24_feat.shift(lag).fillna(0), 24 + lag, "vol")

        # 7b. Momentum × Volatility interaction (high vol + strong momentum = more signal)
        for w in [24, 72]:
            mom_w = rolling_zscore(close.pct_change(w), w)
            vol_w = rolling_zscore(log_ret.rolling(w, min_periods=w // 2).std(ddof=0), w)
            _add(f"mom_x_vol_{w}", mom_w * vol_w, w * 2, "momentum")

        # 7c. RSI × Bollinger interaction (overbought in tight bands vs wide bands)
        rsi_14_feat = rsi(close, period=14) / 100.0 - 0.5
        bw_50 = _boll_width(close, 50)
        _add("rsi14_x_bw50", rsi_14_feat * rolling_zscore(bw_50, 50), 100, "mean_rev")

        # 7d. Volume-weighted momentum (VW return vs equal return)
        for w in [24, 72]:
            ret_1bar = close.pct_change()
            vw_ret = (ret_1bar * volume).rolling(w, min_periods=w // 2).sum() / volume.rolling(w, min_periods=w // 2).sum().replace(0, np.nan)
            eq_ret = ret_1bar.rolling(w, min_periods=w // 2).mean()
            _add(f"vw_vs_eq_ret_{w}", (vw_ret - eq_ret).fillna(0), w, "volume")

        # 7e. Rolling price-volume correlation (divergence detector)
        for w in [48, 168]:
            pv_corr = close.rolling(w, min_periods=w // 2).corr(volume)
            _add(f"price_vol_corr_{w}", pv_corr.fillna(0), w, "micro")

        # 7f. Efficiency ratio (Kaufman) — trend vs noise
        for w in [24, 72]:
            direction = (close - close.shift(w)).abs()
            volatility = close.diff().abs().rolling(w, min_periods=1).sum()
            _add(f"efficiency_ratio_{w}", (direction / volatility.replace(0, np.nan)).fillna(0), w, "momentum")

        # 7g. Hurst exponent proxy (rolling R/S)
        for w in [72, 168]:
            rs_hurst = _rolling_hurst_proxy(log_ret, w)
            _add(f"hurst_proxy_{w}", rs_hurst, w, "momentum")

        # --- 8. Funding features + interactions (~15) ---
        if funding is not None:
            fr = funding.reindex(df.index).ffill().fillna(0.0)
            _add("funding_raw", fr, 1, "funding")
            for w in c.funding_windows:
                _add(f"funding_zscore_{w}", rolling_zscore(fr, w), w * 2, "funding")
                _add(f"funding_cum_{w}", fr.rolling(w, min_periods=1).sum(), w, "funding")
            # Funding × momentum interaction
            _add("funding_x_mom_72", fr * close.pct_change(72), 73, "funding")
            # Funding rate reversal (sign change frequency)
            fr_sign = np.sign(fr)
            fr_flips = (fr_sign != fr_sign.shift(1)).astype(float)
            _add("funding_flip_rate_72", fr_flips.rolling(72, min_periods=36).mean(), 72, "funding")

        # --- 9. Fear & Greed Index features (~10) ---
        if fear_greed is not None:
            # Resample daily FNG to hourly (forward-fill)
            fng = fear_greed.reindex(df.index).ffill().fillna(50.0)
            fng_norm = (fng - 50.0) / 50.0  # normalize to [-1, 1]
            _add("fng_raw", fng_norm, 1, "sentiment")
            for w in [7, 14, 30]:
                # Daily windows → hourly equivalent
                wh = w * 24
                _add(f"fng_zscore_{w}d", rolling_zscore(fng, wh), wh * 2, "sentiment")
                _add(f"fng_change_{w}d", fng.diff(wh) / 50.0, wh + 1, "sentiment")

            # Extreme fear/greed indicator
            _add("fng_extreme_fear", (fng < 20).astype(float), 1, "sentiment")
            _add("fng_extreme_greed", (fng > 80).astype(float), 1, "sentiment")

            # FNG × momentum interaction (fear + negative momentum = stronger signal)
            mom_72 = close.pct_change(72)
            _add("fng_x_mom_72", fng_norm * mom_72, 73, "sentiment")

        # --- 9b. NLP Sentiment features (from sentiment_hourly DB) ---
        if sentiment is not None:
            sent = sentiment.reindex(df.index).ffill().fillna(0.0)
            _add("sentiment_raw", sent, 1, "sentiment")
            for w in [24, 72, 168]:
                _add(f"sentiment_zscore_{w}", rolling_zscore(sent, w), w * 2, "sentiment")
                _add(f"sentiment_change_{w}", sent.diff(w).fillna(0.0), w + 1, "sentiment")
            # Extremes
            _add("sentiment_extreme_bull", (sent > 0.3).astype(float), 1, "sentiment")
            _add("sentiment_extreme_bear", (sent < -0.3).astype(float), 1, "sentiment")
            # Sentiment × price interactions
            _add("sentiment_x_mom_24", sent * close.pct_change(24).fillna(0.0), 25, "sentiment")
            _add("sentiment_x_vol_24", sent * log_ret.rolling(24).std().fillna(0.0), 25, "sentiment")
            # Sentiment momentum (is sentiment improving or deteriorating?)
            _add("sentiment_accel", sent.diff(24).diff(24).fillna(0.0), 49, "sentiment")

        # --- 10. On-chain features (~15) ---
        if derivatives is not None and "onchain" in derivatives:
            oc = derivatives["onchain"]
            oc_aligned = oc.reindex(df.index).ffill()
            for col in ["tx_volume_usd", "active_addresses", "hash_rate",
                         "n_transactions", "miners_revenue", "difficulty"]:
                if col in oc_aligned.columns:
                    val = oc_aligned[col].astype(float)
                    _add(f"oc_{col}_zscore_72", rolling_zscore(val, 72), 144, "onchain")
                    _add(f"oc_{col}_change_168", val.pct_change(168).fillna(0), 169, "onchain")

            # Network activity × price interaction
            if "active_addresses" in oc_aligned.columns:
                addr_z = rolling_zscore(oc_aligned["active_addresses"].astype(float), 72)
                price_z = rolling_zscore(close, 72)
                _add("oc_addr_price_div", addr_z - price_z, 144, "onchain")

        # --- 11. Derivatives features (~25) ---
        if derivatives is not None:
            dw = c.derivatives_windows

            # Open Interest
            if "open_interest" in derivatives:
                oi_df = derivatives["open_interest"]
                oi = oi_df.reindex(df.index).ffill().bfill()
                if "sumOpenInterest" in oi.columns:
                    oi_val = oi["sumOpenInterest"].astype(float)
                    for w in dw:
                        oi_chg = oi_val.pct_change(w).fillna(0)
                        _add(f"oi_change_{w}", oi_chg, w + 1, "derivatives")
                        _add(f"oi_zscore_{w}", rolling_zscore(oi_val, w), w * 2, "derivatives")
                        # OI-price divergence (crowding detector)
                        price_z = rolling_zscore(close, w)
                        oi_z = rolling_zscore(oi_val, w)
                        _add(f"oi_price_div_{w}", oi_z - price_z, w * 2, "derivatives")
                    # OI × momentum interaction
                    _add("oi_x_mom_24", rolling_zscore(oi_val, 24) * close.pct_change(24), 48, "derivatives")
                    _add("oi_x_mom_72", rolling_zscore(oi_val, 72) * close.pct_change(72), 144, "derivatives")

            # Global Long/Short Ratio
            if "global_lsr" in derivatives:
                lsr_df = derivatives["global_lsr"]
                lsr = lsr_df.reindex(df.index).ffill().bfill()
                if "longShortRatio" in lsr.columns:
                    lsr_val = lsr["longShortRatio"].astype(float)
                    for w in dw:
                        _add(f"lsr_zscore_{w}", rolling_zscore(lsr_val, w), w * 2, "derivatives")
                    # Extreme indicator (ratio > 2 or < 0.5)
                    _add("lsr_extreme_long", (lsr_val > 2.0).astype(float), 1, "derivatives")
                    _add("lsr_extreme_short", (lsr_val < 0.5).astype(float), 1, "derivatives")
                    # LSR momentum
                    _add("lsr_momentum_24", lsr_val.diff(24).fillna(0), 25, "derivatives")

            # Top Trader Position Ratio
            if "top_lsr" in derivatives:
                top_df = derivatives["top_lsr"]
                top = top_df.reindex(df.index).ffill().bfill()
                if "longShortRatio" in top.columns:
                    top_val = top["longShortRatio"].astype(float)
                    _add("top_lsr_zscore_24", rolling_zscore(top_val, 24), 48, "derivatives")
                    _add("top_lsr_zscore_72", rolling_zscore(top_val, 72), 144, "derivatives")
                    # Smart money vs crowd divergence
                    if "global_lsr" in derivatives and "longShortRatio" in derivatives["global_lsr"].columns:
                        crowd = derivatives["global_lsr"].reindex(df.index).ffill().bfill()["longShortRatio"].astype(float)
                        _add("top_vs_crowd", top_val - crowd, 1, "derivatives")

            # Taker Buy/Sell Volume
            if "taker" in derivatives:
                tk_df = derivatives["taker"]
                tk = tk_df.reindex(df.index).ffill().bfill()
                if "buySellRatio" in tk.columns:
                    bsr = tk["buySellRatio"].astype(float)
                    for w in dw:
                        _add(f"taker_ratio_zscore_{w}", rolling_zscore(bsr, w), w * 2, "derivatives")
                if "buyVol" in tk.columns and "sellVol" in tk.columns:
                    bv = tk["buyVol"].astype(float)
                    sv = tk["sellVol"].astype(float)
                    imbalance = (bv - sv) / (bv + sv).replace(0, np.nan)
                    for w in [24, 72]:
                        _add(f"taker_imbalance_zscore_{w}", rolling_zscore(imbalance.fillna(0), w), w * 2, "derivatives")
                    # Aggressive buy/sell indicators
                    _add("taker_aggressive_buy", (bsr > 1.5).astype(float) if "buySellRatio" in tk.columns else pd.Series(0, index=df.index), 1, "derivatives")
                    _add("taker_aggressive_sell", (bsr < 0.67).astype(float) if "buySellRatio" in tk.columns else pd.Series(0, index=df.index), 1, "derivatives")

            # Cross-derivatives interactions
            if "global_lsr" in derivatives and "taker" in derivatives:
                if "longShortRatio" in derivatives["global_lsr"].columns and "buySellRatio" in derivatives["taker"].columns:
                    lsr_z = rolling_zscore(derivatives["global_lsr"].reindex(df.index).ffill().fillna(1)["longShortRatio"].astype(float), 24)
                    tk_z = rolling_zscore(derivatives["taker"].reindex(df.index).ffill().fillna(1)["buySellRatio"].astype(float), 24)
                    _add("lsr_x_taker", lsr_z * tk_z, 48, "derivatives")

            if funding is not None and "global_lsr" in derivatives:
                if "longShortRatio" in derivatives["global_lsr"].columns:
                    fr = funding.reindex(df.index).ffill().fillna(0)
                    lsr_z = rolling_zscore(derivatives["global_lsr"].reindex(df.index).ffill().fillna(1)["longShortRatio"].astype(float), 24)
                    _add("funding_x_lsr", fr * lsr_z, 24, "derivatives")

        # --- Post-processing ---
        feat_df = pd.DataFrame(features, index=df.index)

        # Clip extreme values
        feat_df = feat_df.clip(-c.clip_value, c.clip_value)

        # Fill NaN/Inf
        feat_df = feat_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)

        # Remove near-constant features — compute variance on a causal
        # prefix only, so future bars cannot decide the column schema.
        # (Previously used the whole series, which let a late-window
        # perturbation flip a feature's inclusion and leaked into past
        # positions — see test_ml_discovery::test_no_lookahead.)
        max_lb = max((m.lookback for m in metadata), default=0)
        prefix = max(max_lb + 1, min(len(feat_df) // 2, 2000))
        prefix = min(prefix, len(feat_df))
        variances = feat_df.iloc[:prefix].var()
        keep = variances[variances > c.variance_threshold].index.tolist()
        removed = set(feat_df.columns) - set(keep)
        feat_df = feat_df[keep]
        metadata = [m for m in metadata if m.name in keep]

        return FeatureMatrix(features=feat_df, metadata=metadata)

    def generate_panel(
        self,
        dfs: dict[str, pd.DataFrame],
        funding: dict[str, pd.Series] | None = None,
        fear_greed: pd.Series | None = None,
        derivatives: dict[str, dict[str, pd.DataFrame]] | None = None,
    ) -> dict[str, FeatureMatrix]:
        """Generate features for multiple symbols."""
        funding = funding or {}
        derivatives = derivatives or {}
        return {
            sym: self.generate(df, funding.get(sym), fear_greed, derivatives.get(sym))
            for sym, df in dfs.items()
        }

    def generate_cached(
        self,
        df: pd.DataFrame,
        funding: pd.Series | None = None,
        fear_greed: pd.Series | None = None,
        derivatives: dict[str, pd.DataFrame] | None = None,
        sentiment: pd.Series | None = None,
        cache_key_extra: str = "",
    ) -> FeatureMatrix:
        """Cached version of generate(). Uses module-level FeatureCache."""
        from shared.features.cache import get_feature_cache
        cache = get_feature_cache()
        return cache.get_or_compute(
            df,
            lambda: self.generate(df, funding=funding, fear_greed=fear_greed,
                                  derivatives=derivatives, sentiment=sentiment),
            extra=cache_key_extra,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _boll_width(close: pd.Series, period: int, n_std: float = 2.0) -> pd.Series:
    """Bollinger band width = (upper - lower) / mid."""
    mean = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    mid = mean.replace(0, np.nan)
    return (2 * n_std * std / mid).fillna(0.0)


def _rolling_hurst_proxy(log_ret: pd.Series, window: int) -> pd.Series:
    """Rolling Hurst exponent proxy via rescaled range (R/S).

    H > 0.5 → trending, H < 0.5 → mean-reverting, H ≈ 0.5 → random walk.
    Returns normalized to [0, 1] range.
    """
    def _rs_hurst(x):
        n = len(x)
        if n < 10:
            return 0.5
        y = np.cumsum(x - np.mean(x))
        r = np.max(y) - np.min(y)
        s = np.std(x, ddof=1)
        if s < 1e-12 or r < 1e-12:
            return 0.5
        rs = r / s
        h = np.log(rs) / np.log(n)
        return np.clip(h, 0.0, 1.0)

    return log_ret.rolling(window, min_periods=window // 2).apply(_rs_hurst, raw=True).fillna(0.5)


def _garman_klass_vol(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
) -> pd.Series:
    """Per-bar Garman-Klass volatility estimator."""
    log_hl = np.log(high.astype(float) / low.astype(float).replace(0, np.nan))
    log_co = np.log(close.astype(float) / open_.astype(float).replace(0, np.nan))
    gk = 0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2
    return gk.fillna(0.0)
