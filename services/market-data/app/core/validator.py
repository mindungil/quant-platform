from datetime import timedelta

from app.models.candle import CandlePayload, ValidationResult


def detect_gaps(candles: list[CandlePayload], expected_interval_minutes: int = 60) -> list[dict]:
    """Return list of detected gaps with from_ts, to_ts, missing_count."""
    if len(candles) < 2:
        return []

    sorted_candles = sorted(candles, key=lambda c: c.timestamp)
    interval = timedelta(minutes=expected_interval_minutes)
    gaps: list[dict] = []

    for i in range(1, len(sorted_candles)):
        prev_ts = sorted_candles[i - 1].timestamp
        curr_ts = sorted_candles[i].timestamp
        diff = curr_ts - prev_ts

        if diff > interval * 1.5:  # Allow 50% tolerance before flagging
            missing_count = int(diff / interval) - 1
            if missing_count > 0:
                gaps.append({
                    "from_ts": prev_ts.isoformat(),
                    "to_ts": curr_ts.isoformat(),
                    "missing_count": missing_count,
                })

    return gaps


def validate_candle_transition(
    previous: CandlePayload | None, current: CandlePayload, asset: str | None = None
) -> ValidationResult:
    if current.high < current.low:
        return ValidationResult(accepted=False, anomaly_detected=True, reason="high_below_low")

    if current.volume <= 0:
        return ValidationResult(accepted=False, anomaly_detected=True, reason="non_positive_volume")

    if previous is None:
        return ValidationResult(accepted=True, anomaly_detected=False, reason="initial")

    if current.timestamp <= previous.timestamp:
        return ValidationResult(accepted=False, anomaly_detected=True, reason="non_monotonic_timestamp")

    price_delta = abs(current.close - previous.close) / previous.close
    # Per-asset spike thresholds: stablecoins are strict, crypto volatile assets lenient
    asset_name = (asset or getattr(current, "asset", "")).upper()
    if any(s in asset_name for s in ("USDT", "USDC", "DAI", "BUSD")):
        spike_threshold = 0.02   # 2% for stablecoins — a 2% move is crisis-level
    elif any(s in asset_name for s in ("BTC", "ETH")):
        spike_threshold = 0.15   # 15% for majors
    else:
        spike_threshold = 0.20   # 20% for alts (SOL, etc.)

    anomaly_detected = price_delta > spike_threshold
    if anomaly_detected:
        # Quarantine: reject the bar so it doesn't propagate to alphas.
        # Downstream should request a re-fetch or wait for the next bar.
        return ValidationResult(
            accepted=False,
            anomaly_detected=True,
            reason=f"spike_quarantined:{price_delta:.1%}>{spike_threshold:.0%}",
        )
    return ValidationResult(accepted=True, anomaly_detected=False, reason="ok")
