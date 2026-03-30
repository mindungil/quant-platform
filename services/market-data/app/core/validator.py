from app.models.candle import CandlePayload, ValidationResult


def validate_candle_transition(
    previous: CandlePayload | None, current: CandlePayload
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
    anomaly_detected = price_delta > 0.10
    reason = "spike_detected" if anomaly_detected else "ok"
    return ValidationResult(accepted=True, anomaly_detected=anomaly_detected, reason=reason)
