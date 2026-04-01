import math
import time

from prometheus_client import Counter, Histogram

from app.models.risk import RiskApprovalRequest, RiskApprovalResponse
from app.db.repository import risk_repository

risk_approvals_total = Counter(
    "risk_approvals_total",
    "Total risk approval decisions",
    ["result", "level"],
)
risk_approval_latency_seconds = Histogram(
    "risk_approval_latency_seconds",
    "Latency of risk approval evaluation",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

# Normal distribution quantiles
Z_95 = 1.6449
Z_99 = 2.3263

# Long-term baseline volatility (annualized)
LONG_TERM_VOL = 0.20

# Base drawdown thresholds
BASE_WARNING_DD = 0.05
BASE_LIQUIDATE_DD = 0.10


def _record_metrics(result: RiskApprovalResponse, start: float) -> None:
    label_result = "approved" if result.approved else "rejected"
    risk_approvals_total.labels(result=label_result, level=result.level).inc()
    risk_approval_latency_seconds.observe(time.monotonic() - start)


def _calculate_var_cvar(
    daily_returns: list[float], portfolio_value: float
) -> tuple[float, float, float]:
    """Calculate parametric VaR(95%), CVaR(95%), and realized volatility.

    Returns (var_95, cvar_95, realized_vol_annualized).
    """
    if len(daily_returns) < 5:
        return 0.0, 0.0, LONG_TERM_VOL

    n = len(daily_returns)
    mu = sum(daily_returns) / n
    variance = sum((r - mu) ** 2 for r in daily_returns) / (n - 1)
    sigma = math.sqrt(variance) if variance > 0 else 1e-9

    # Parametric VaR (95%): worst daily loss at 95% confidence
    var_95 = portfolio_value * (mu - Z_95 * sigma)
    var_95 = abs(min(var_95, 0.0))  # VaR is positive number representing loss

    # CVaR (Expected Shortfall): expected loss beyond VaR
    # For normal distribution: CVaR = mu - sigma * phi(z) / (1 - alpha)
    # phi(1.6449) ≈ 0.1031
    phi_z95 = math.exp(-0.5 * Z_95 ** 2) / math.sqrt(2 * math.pi)
    cvar_95 = portfolio_value * abs(mu - sigma * phi_z95 / 0.05)
    cvar_95 = max(cvar_95, var_95)  # CVaR >= VaR always

    # Annualized realized volatility
    realized_vol = sigma * math.sqrt(252)

    return round(var_95, 2), round(cvar_95, 2), realized_vol


def _classify_volatility(realized_vol: float) -> str:
    """Classify current volatility regime."""
    if realized_vol < LONG_TERM_VOL * 0.7:
        return "low"
    elif realized_vol > LONG_TERM_VOL * 1.5:
        return "high"
    return "normal"


def approve_order(payload: RiskApprovalRequest) -> RiskApprovalResponse:
    _start = time.monotonic()
    exposure_ratio = 0.0 if payload.exposure_limit == 0 else payload.current_exposure / payload.exposure_limit

    # Calculate VaR/CVaR from recent returns
    var_95, cvar_95, realized_vol = _calculate_var_cvar(
        payload.recent_daily_returns, payload.portfolio_value
    )
    vol_regime = _classify_volatility(realized_vol)

    # Volatility-adjusted drawdown thresholds
    vol_ratio = max(realized_vol / LONG_TERM_VOL, 0.5) if LONG_TERM_VOL > 0 else 1.0
    vol_ratio = min(vol_ratio, 2.0)  # cap adjustment
    warning_dd = BASE_WARNING_DD / vol_ratio    # tighter in high vol
    liquidate_dd = BASE_LIQUIDATE_DD / vol_ratio

    def _make_response(approved: bool, reason: str, level: str) -> RiskApprovalResponse:
        result = RiskApprovalResponse(
            approved=approved,
            reason=reason,
            level=level,
            exposure_ratio=round(exposure_ratio, 4),
            var_95=var_95,
            cvar_95=cvar_95,
            volatility_regime=vol_regime,
        )
        risk_repository.record(payload, result)
        _record_metrics(result, _start)
        return result

    if not payload.automation_enabled:
        return _make_response(False, "automation_disabled", "HALT")

    if payload.current_drawdown >= liquidate_dd:
        return _make_response(
            False,
            f"liquidate_threshold_reached (dd={payload.current_drawdown:.3f} >= {liquidate_dd:.3f})",
            "LIQUIDATE",
        )

    if payload.current_drawdown >= warning_dd:
        return _make_response(
            False,
            f"warning_threshold_reached (dd={payload.current_drawdown:.3f} >= {warning_dd:.3f})",
            "HALT",
        )

    if payload.requested_notional > payload.max_notional:
        return _make_response(False, "notional_limit_exceeded", "HALT")

    if payload.current_exposure + payload.requested_notional > payload.exposure_limit:
        return _make_response(False, "exposure_limit_exceeded", "HALT")

    # VaR-based check: reject if single order > 50% of daily VaR
    if var_95 > 0 and payload.requested_notional > var_95 * 0.5:
        return _make_response(
            False,
            f"var_limit_exceeded (notional={payload.requested_notional:.0f} > 50% VaR={var_95 * 0.5:.0f})",
            "HALT",
        )

    return _make_response(True, "approved", "OK")
