"""Risk and position sizing utilities.

The full position-sizing toolkit (fractional Kelly, vol targeting, CVaR cap)
is proprietary and ships with private builds. Public-only builds expose a
no-op stub so importing `shared.risk` does not raise.
"""
__all__: list[str] = []

try:
    from shared.risk.position_sizing import (
        SizingResult,
        fractional_kelly,
        vol_target_scaler,
        realized_vol_from_atr,
        cvar_normal_approx,
        cvar_cap_scaler,
        size_position,
        DEFAULT_KELLY_FRACTION,
        DEFAULT_TARGET_ANNUAL_VOL,
        DEFAULT_CVAR_CAP_PCT,
        DEFAULT_MAX_LEVERAGE,
    )
    __all__.extend([
        "SizingResult", "fractional_kelly", "vol_target_scaler",
        "realized_vol_from_atr", "cvar_normal_approx", "cvar_cap_scaler",
        "size_position", "DEFAULT_KELLY_FRACTION", "DEFAULT_TARGET_ANNUAL_VOL",
        "DEFAULT_CVAR_CAP_PCT", "DEFAULT_MAX_LEVERAGE",
    ])
except ImportError:
    SizingResult = None  # type: ignore
    fractional_kelly = vol_target_scaler = realized_vol_from_atr = None  # type: ignore
    cvar_normal_approx = cvar_cap_scaler = size_position = None  # type: ignore
    DEFAULT_KELLY_FRACTION = DEFAULT_TARGET_ANNUAL_VOL = 0.0
    DEFAULT_CVAR_CAP_PCT = DEFAULT_MAX_LEVERAGE = 0.0

try:
    from shared.risk.forecast_combination import (
        scale_forecast,
        forecast_diversification_multiplier,
        combine_forecasts,
        forecast_to_position,
    )
    __all__.extend([
        "scale_forecast", "forecast_diversification_multiplier",
        "combine_forecasts", "forecast_to_position",
    ])
except ImportError:
    scale_forecast = forecast_diversification_multiplier = None  # type: ignore
    combine_forecasts = forecast_to_position = None  # type: ignore
