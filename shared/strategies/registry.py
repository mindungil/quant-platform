"""Strategy Presets — pre-configured factor weight overrides for different market conditions.

Each preset defines which factor categories to emphasize and what thresholds to use.
The protocol_router selects the active preset based on regime + accuracy + conditions.
"""

STRATEGY_PRESETS = {
    "trend_following": {
        "name": "추세 추종",
        "description": "강한 추세장에서 모멘텀 팩터를 강화하여 추세를 따라감",
        "regime_match": ["trending"],
        "condition": "adx > 30",
        "category_weights": {
            "technical": 1.0, "momentum": 2.5, "reversion": 0.2,
            "volatility": 0.8, "derivatives": 1.2, "sentiment": 0.6,
        },
        "entry_threshold": 0.45,
        "position_scale": 1.3,
    },
    "mean_reversion": {
        "name": "평균 회귀",
        "description": "횡보장에서 볼린저/VWAP/RSI 극단값으로 역발상 매매",
        "regime_match": ["sideways", "ranging"],
        "condition": "adx < 20",
        "category_weights": {
            "technical": 0.8, "momentum": 0.3, "reversion": 2.5,
            "volatility": 1.0, "derivatives": 0.8, "sentiment": 1.0,
        },
        "entry_threshold": 0.5,
        "position_scale": 1.0,
    },
    "volatility_breakout": {
        "name": "변동성 돌파",
        "description": "볼린저/켈트너 스퀴즈 후 돌파 시점을 포착",
        "regime_match": ["volatile"],
        "condition": "squeeze_detected",
        "category_weights": {
            "technical": 1.2, "momentum": 1.5, "reversion": 0.5,
            "volatility": 2.5, "derivatives": 1.0, "sentiment": 0.8,
        },
        "entry_threshold": 0.5,
        "position_scale": 0.8,
    },
    "crisis_defense": {
        "name": "위기 방어",
        "description": "극단적 공포/탐욕 시 감성+파생 팩터 위주로 보수적 운용",
        "regime_match": ["any"],
        "condition": "fear_greed < 15 or fear_greed > 85",
        "category_weights": {
            "technical": 0.5, "momentum": 0.3, "reversion": 0.5,
            "volatility": 1.0, "derivatives": 2.0, "sentiment": 2.5,
        },
        "entry_threshold": 0.8,
        "position_scale": 0.3,
    },
    "funding_arbitrage": {
        "name": "펀딩레이트 차익",
        "description": "높은 펀딩레이트 이용 — 숏 진입으로 펀딩 수취",
        "regime_match": ["any"],
        "condition": "funding_rate > 0.0003",
        "category_weights": {
            "technical": 0.3, "momentum": 0.3, "reversion": 0.5,
            "volatility": 0.5, "derivatives": 3.0, "sentiment": 1.0,
        },
        "entry_threshold": 0.3,
        "position_scale": 0.5,
    },
    "balanced": {
        "name": "균형",
        "description": "모든 팩터를 균등하게 사용하는 기본 전략",
        "regime_match": ["any"],
        "condition": "default",
        "category_weights": {
            "technical": 1.0, "momentum": 1.0, "reversion": 1.0,
            "volatility": 1.0, "derivatives": 1.0, "sentiment": 1.0,
        },
        "entry_threshold": 0.6,
        "position_scale": 1.0,
    },
}

def get_preset(name: str) -> dict | None:
    return STRATEGY_PRESETS.get(name)

def get_preset_for_regime(regime: str) -> str:
    """Select best preset for given regime label."""
    regime_lower = regime.lower()
    for name, preset in STRATEGY_PRESETS.items():
        for match in preset["regime_match"]:
            if match != "any" and match in regime_lower:
                return name
    return "balanced"

def get_preset_for_conditions(
    regime: str,
    accuracy: float,
    fear_greed: float = 50,
    funding_rate: float = 0,
    adx: float = 25,
    squeeze: bool = False,
) -> str:
    """Select preset based on full market conditions."""
    # Crisis check first
    if fear_greed < 15 or fear_greed > 85:
        return "crisis_defense"

    # Funding arbitrage
    if funding_rate > 0.0003:
        return "funding_arbitrage"

    # Volatility breakout
    if squeeze:
        return "volatility_breakout"

    # Regime-based
    if adx > 30:
        return "trend_following"
    if adx < 20:
        return "mean_reversion"

    return "balanced"
