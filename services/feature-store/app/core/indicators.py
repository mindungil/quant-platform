from datetime import timedelta

import pandas as pd

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

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(window=14, min_periods=14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(loss.ne(0), 100.0)

    ema_9 = close.ewm(span=9, adjust=False).mean()
    ema_12 = close.ewm(span=12, adjust=False).mean()
    ema_21 = close.ewm(span=21, adjust=False).mean()
    ema_26 = close.ewm(span=26, adjust=False).mean()
    ema_50 = close.ewm(span=50, adjust=False).mean()
    ema_200 = close.ewm(span=200, adjust=False).mean()
    sma_20 = close.rolling(window=20, min_periods=20).mean()
    sma_50 = close.rolling(window=50, min_periods=50).mean()
    rolling_std = close.rolling(window=20, min_periods=20).std()
    bb_upper = sma_20 + (rolling_std * 2)
    bb_lower = sma_20 - (rolling_std * 2)
    macd = ema_12 - ema_26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    lowest_low = low.rolling(window=14, min_periods=14).min()
    highest_high = high.rolling(window=14, min_periods=14).max()
    stochastic_k = ((close - lowest_low) / (highest_high - lowest_low).replace(0, pd.NA)) * 100
    stochastic_d = stochastic_k.rolling(window=3, min_periods=3).mean()
    typical_price = (high + low + close) / 3
    vwap = (typical_price * volume).cumsum() / volume.cumsum()

    return FeatureResponse(
        asset=asset,
        timestamp=df["timestamp"].iloc[-1],
        close=_safe_float(close.iloc[-1]),
        volume=_safe_float(volume.iloc[-1]),
        rsi_14=_safe_float(rsi.iloc[-1]),
        macd=_safe_float(macd.iloc[-1]),
        macd_signal=_safe_float(macd_signal.iloc[-1]),
        bb_upper=_safe_float(bb_upper.iloc[-1]),
        bb_lower=_safe_float(bb_lower.iloc[-1]),
        ema_9=_safe_float(ema_9.iloc[-1]),
        ema_21=_safe_float(ema_21.iloc[-1]),
        ema_50=_safe_float(ema_50.iloc[-1]),
        ema_200=_safe_float(ema_200.iloc[-1]),
        sma_20=_safe_float(sma_20.iloc[-1]),
        sma_50=_safe_float(sma_50.iloc[-1]),
        stochastic_k=_safe_float(stochastic_k.iloc[-1]),
        stochastic_d=_safe_float(stochastic_d.iloc[-1]),
        vwap=_safe_float(vwap.iloc[-1]),
    )
