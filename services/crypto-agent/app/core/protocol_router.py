"""Protocol Router — selects trading protocol based on market conditions + accuracy."""
import logging

logger = logging.getLogger("crypto-agent")

PROTOCOLS = {
    "aggressive": {"entry_threshold": 0.4, "position_scale": 1.5, "description": "High-accuracy aggressive mode"},
    "standard":   {"entry_threshold": 0.6, "position_scale": 1.0, "description": "Standard mode"},
    "conservative": {"entry_threshold": 0.8, "position_scale": 0.5, "description": "Conservative mode"},
    "crisis":     {"entry_threshold": 0.9, "position_scale": 0.2, "description": "Crisis management mode"},
}


def select_protocol(
    accuracy: float,
    fear_greed: float = 50,
    regime: str = "",
    adx: float = 25,
    funding_rate: float = 0,
    squeeze: bool = False,
) -> dict:
    """Select protocol AND strategy preset based on conditions.

    Tries strategy presets first for richer condition-based selection,
    falls back to legacy PROTOCOLS on import or lookup errors.
    """
    try:
        from shared.strategies.registry import STRATEGY_PRESETS, get_preset_for_conditions

        preset_name = get_preset_for_conditions(
            regime, accuracy, fear_greed, funding_rate, adx, squeeze,
        )
        preset = STRATEGY_PRESETS[preset_name]

        logger.info("protocol_selected", extra={
            "protocol": preset_name, "accuracy": round(accuracy, 2),
            "fear_greed": fear_greed, "source": "strategy_preset",
        })
        return {
            "name": preset_name,
            "display_name": preset["name"],
            "entry_threshold": preset["entry_threshold"],
            "position_scale": preset["position_scale"],
            "category_weights": preset["category_weights"],
            "description": preset["description"],
        }
    except Exception as e:
        logger.warning("strategy_preset_fallback", extra={"error": str(e)[:200]})

    # Legacy fallback
    if fear_greed < 10 or fear_greed > 90:
        name = "crisis"
    elif accuracy >= 0.60:
        name = "aggressive"
    elif accuracy <= 0.40:
        name = "conservative"
    else:
        name = "standard"

    protocol = PROTOCOLS[name]
    logger.info("protocol_selected", extra={"protocol": name, "accuracy": round(accuracy, 2), "fear_greed": fear_greed})
    return {"name": name, **protocol}
