"""Protocol Router — selects trading protocol based on market conditions + accuracy."""
import logging

logger = logging.getLogger("crypto-agent")

PROTOCOLS = {
    "aggressive": {"entry_threshold": 0.4, "position_scale": 1.5, "description": "High-accuracy aggressive mode"},
    "standard":   {"entry_threshold": 0.6, "position_scale": 1.0, "description": "Standard mode"},
    "conservative": {"entry_threshold": 0.8, "position_scale": 0.5, "description": "Conservative mode"},
    "crisis":     {"entry_threshold": 0.9, "position_scale": 0.2, "description": "Crisis management mode"},
}

def select_protocol(accuracy: float, fear_greed: float = 50, regime: str = "") -> dict:
    """Select protocol based on recent accuracy + market conditions."""
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
