from datetime import timedelta

import pandas as pd
from ta.momentum import RSIIndicator, StochRSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator, SMAIndicator, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice

from app.models.feature import CandlePayload, FeatureResponse


def _safe_float(value: float | None) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def interpolate_gaps(candles: list[CandlePayload], interval_minutes: int = 60) -> list[CandlePayload]:
    """Fill small gaps (<=2 bars) with forward-fill; reject larger gaps.

    Linear interpolation of OHLCV fabricates fake price action that
    misleads indicators. Forward-fill is honest — signals the pause
    without creating fictional trades. Large gaps (>2 bars) indicate
    data quality issues and should be investigated, not papered over.
    """
    if len(candles) < 2:
        return list(candles)

    sorted_candles = sorted(candles, key=lambda c: c.timestamp)
    interval = timedelta(minutes=interval_minutes)
    result: list[CandlePayload] = [sorted_candles[0]]

    for i in range(1, len(sorted_candles)):
        prev = sorted_candles[i - 1]
        curr = sorted_candles[i]
        diff = curr.timestamp - prev.timestamp

        if diff > interval * 1.5:
            missing_count = int(diff / interval) - 1
            if missing_count > 2:
                raise ValueError(f"data gap of {missing_count} bars exceeds max 2")
            for step in range(1, missing_count + 1):
                interp_ts = prev.timestamp + interval * step
                # Forward-fill: pause bar carries prior close; volume=0 signals no trade.
                result.append(
                    CandlePayload(
                        timestamp=interp_ts,
                        open=prev.close,
                        high=prev.close,
                        low=prev.close,
                        close=prev.close,
                        volume=0.0,
                    )
                )

        result.append(curr)

    return result


RESAMPLE_HOURS = {"4h": 4, "1d": 24}


def resample_candles(candles: list[CandlePayload], target_interval: str) -> list[CandlePayload]:
    """Resample candles to a larger timeframe using OHLCV aggregation.

    Supports: "4h", "1d" (from 1h base candles).
    Returns the original list unchanged for "1h" or unrecognized intervals.
    """
    hours = RESAMPLE_HOURS.get(target_interval)
    if hours is None:
        return candles

    sorted_candles = sorted(candles, key=lambda c: c.timestamp)
    result: list[CandlePayload] = []

    for i in range(0, len(sorted_candles), hours):
        group = sorted_candles[i : i + hours]
        if not group:
            break
        result.append(
            CandlePayload(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            )
        )
    return result


def _last(series) -> float | None:
    """Safe last-value extraction from an indicator series. Returns None if
    the series is empty or the last value is NaN."""
    try:
        if series is None or len(series) == 0:
            return None
        val = series.iloc[-1]
        return _safe_float(val)
    except Exception:
        return None


def _try(fn):
    """Run an indicator closure; return None on any failure (e.g. IndexError
    when candle history is shorter than the indicator's window)."""
    try:
        return fn()
    except Exception:
        return None


def calculate_features(asset: str, candles: list[CandlePayload]) -> FeatureResponse:
    df = pd.DataFrame([candle.model_dump(mode="python") for candle in candles]).sort_values("timestamp")
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- ta library: production-grade indicator calculations ---
    # Each indicator is guarded individually so that a short candle history
    # (e.g. first few candles after service start, before the 200-period EMA
    # window is reached) yields a partial FeatureResponse instead of crashing
    # the consumer and pushing the message to DLQ. Missing values are None.

    rsi = _try(lambda: RSIIndicator(close=close, window=14).rsi())

    macd_line = _try(lambda: MACD(close=close, window_fast=12, window_slow=26, window_sign=9).macd())
    macd_signal_line = _try(lambda: MACD(close=close, window_fast=12, window_slow=26, window_sign=9).macd_signal())

    bb = _try(lambda: BollingerBands(close=close, window=20, window_dev=2))
    bb_upper = _try(lambda: bb.bollinger_hband()) if bb is not None else None
    bb_lower = _try(lambda: bb.bollinger_lband()) if bb is not None else None

    ema_9 = _try(lambda: EMAIndicator(close=close, window=9).ema_indicator())
    ema_21 = _try(lambda: EMAIndicator(close=close, window=21).ema_indicator())
    ema_50 = _try(lambda: EMAIndicator(close=close, window=50).ema_indicator())
    ema_200 = _try(lambda: EMAIndicator(close=close, window=200).ema_indicator())

    sma_20 = _try(lambda: SMAIndicator(close=close, window=20).sma_indicator())
    sma_50 = _try(lambda: SMAIndicator(close=close, window=50).sma_indicator())

    stoch_ind = _try(lambda: StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3))
    stoch_k = _try(lambda: stoch_ind.stoch()) if stoch_ind is not None else None
    stoch_d = _try(lambda: stoch_ind.stoch_signal()) if stoch_ind is not None else None

    # VWAP — cumulative, tolerant of single candle
    try:
        vwap_ind = VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume)
        vwap = vwap_ind.volume_weighted_average_price()
    except Exception:
        typical_price = (high + low + close) / 3
        vwap = _try(lambda: (typical_price * volume).cumsum() / volume.cumsum())

    atr_14 = _try(lambda: AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range())
    adx_14 = _try(lambda: ADXIndicator(high=high, low=low, close=close, window=14).adx())
    obv = _try(lambda: OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume())

    return FeatureResponse(
        asset=asset,
        timestamp=df["timestamp"].iloc[-1],
        close=_safe_float(close.iloc[-1]),
        volume=_safe_float(volume.iloc[-1]),
        rsi_14=_last(rsi),
        macd=_last(macd_line),
        macd_signal=_last(macd_signal_line),
        bb_upper=_last(bb_upper),
        bb_lower=_last(bb_lower),
        ema_9=_last(ema_9),
        ema_21=_last(ema_21),
        ema_50=_last(ema_50),
        ema_200=_last(ema_200),
        sma_20=_last(sma_20),
        sma_50=_last(sma_50),
        stochastic_k=_last(stoch_k),
        stochastic_d=_last(stoch_d),
        vwap=_last(vwap),
        atr_14=_last(atr_14),
        adx_14=_last(adx_14),
        obv=_last(obv),
    )
