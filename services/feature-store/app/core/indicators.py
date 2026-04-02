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
    """Fill gaps with linear interpolation between surrounding candles."""
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
            for step in range(1, missing_count + 1):
                ratio = step / (missing_count + 1)
                interp_ts = prev.timestamp + interval * step
                result.append(
                    CandlePayload(
                        timestamp=interp_ts,
                        open=prev.open + (curr.open - prev.open) * ratio,
                        high=prev.high + (curr.high - prev.high) * ratio,
                        low=prev.low + (curr.low - prev.low) * ratio,
                        close=prev.close + (curr.close - prev.close) * ratio,
                        volume=prev.volume + (curr.volume - prev.volume) * ratio,
                    )
                )

        result.append(curr)

    return result


def calculate_features(asset: str, candles: list[CandlePayload]) -> FeatureResponse:
    df = pd.DataFrame([candle.model_dump(mode="python") for candle in candles]).sort_values("timestamp")
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- ta library: production-grade indicator calculations ---

    # RSI (14)
    rsi_ind = RSIIndicator(close=close, window=14)
    rsi = rsi_ind.rsi()

    # MACD (12, 26, 9)
    macd_ind = MACD(close=close, window_fast=12, window_slow=26, window_sign=9)
    macd_line = macd_ind.macd()
    macd_signal_line = macd_ind.macd_signal()

    # Bollinger Bands (20, 2.0)
    bb_ind = BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb_ind.bollinger_hband()
    bb_lower = bb_ind.bollinger_lband()

    # EMAs
    ema_9 = EMAIndicator(close=close, window=9).ema_indicator()
    ema_21 = EMAIndicator(close=close, window=21).ema_indicator()
    ema_50 = EMAIndicator(close=close, window=50).ema_indicator()
    ema_200 = EMAIndicator(close=close, window=200).ema_indicator()

    # SMAs
    sma_20 = SMAIndicator(close=close, window=20).sma_indicator()
    sma_50 = SMAIndicator(close=close, window=50).sma_indicator()

    # Stochastic (14, 3)
    stoch_ind = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
    stoch_k = stoch_ind.stoch()
    stoch_d = stoch_ind.stoch_signal()

    # VWAP
    try:
        vwap_ind = VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume)
        vwap = vwap_ind.volume_weighted_average_price()
    except Exception:
        # VWAP may fail with insufficient data
        typical_price = (high + low + close) / 3
        vwap = (typical_price * volume).cumsum() / volume.cumsum()

    # ATR (14)
    atr_ind = AverageTrueRange(high=high, low=low, close=close, window=14)
    atr_14 = atr_ind.average_true_range()

    # ADX (14)
    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx_14 = adx_ind.adx()

    # OBV
    obv_ind = OnBalanceVolumeIndicator(close=close, volume=volume)
    obv = obv_ind.on_balance_volume()

    return FeatureResponse(
        asset=asset,
        timestamp=df["timestamp"].iloc[-1],
        close=_safe_float(close.iloc[-1]),
        volume=_safe_float(volume.iloc[-1]),
        rsi_14=_safe_float(rsi.iloc[-1]),
        macd=_safe_float(macd_line.iloc[-1]),
        macd_signal=_safe_float(macd_signal_line.iloc[-1]),
        bb_upper=_safe_float(bb_upper.iloc[-1]),
        bb_lower=_safe_float(bb_lower.iloc[-1]),
        ema_9=_safe_float(ema_9.iloc[-1]),
        ema_21=_safe_float(ema_21.iloc[-1]),
        ema_50=_safe_float(ema_50.iloc[-1]),
        ema_200=_safe_float(ema_200.iloc[-1]),
        sma_20=_safe_float(sma_20.iloc[-1]),
        sma_50=_safe_float(sma_50.iloc[-1]),
        stochastic_k=_safe_float(stoch_k.iloc[-1]),
        stochastic_d=_safe_float(stoch_d.iloc[-1]),
        vwap=_safe_float(vwap.iloc[-1]),
        atr_14=_safe_float(atr_14.iloc[-1]),
        adx_14=_safe_float(adx_14.iloc[-1]),
        obv=_safe_float(obv.iloc[-1]),
    )
