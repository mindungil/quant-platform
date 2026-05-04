"""Event classifier for risk filtering.

Classifies news/market events into actionable categories.
Not for price prediction — for answering "WHAT is happening?"
so the alpha pipeline can adjust risk accordingly.

Categories and their risk implications:
  HACK           → reduce exposure, widen stops
  REGULATION     → reduce exposure, pause new entries
  LIQUIDATION    → potential reversal, tighten but don't exit
  EXCHANGE_RISK  → reduce exposure on affected exchange
  MACRO_SHOCK    → reduce all exposure
  ADOPTION       → no risk action (positive, but noisy)
  ROUTINE        → no risk action
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("event-classifier")


class EventType(str, Enum):
    HACK = "hack"
    REGULATION = "regulation"
    LIQUIDATION = "liquidation"
    EXCHANGE_RISK = "exchange_risk"
    MACRO_SHOCK = "macro_shock"
    DEPEG = "depeg"
    ADOPTION = "adoption"
    ROUTINE = "routine"


@dataclass
class EventClassification:
    event_type: EventType
    confidence: float          # 0-1, how sure we are about the classification
    severity: float            # 0-1, how severe within this category
    risk_action: str           # human-readable action
    signal_dampening: float    # 0-1, how much to dampen alpha signals (0=no change, 1=block all)
    details: str               # why this classification


# ─── Keyword patterns (ordered by priority) ────────────────

_PATTERNS: list[tuple[EventType, list[str], float]] = [
    # (type, keywords, base_severity)

    # HACK — security breaches, exploits, theft
    (EventType.HACK, [
        "hack", "hacked", "exploit", "exploited", "stolen",
        "drained", "breach", "vulnerability", "rug pull",
        "rugpull", "compromised", "attack on", "flash loan attack",
        "private key", "wallet breach",
    ], 0.8),

    # LIQUIDATION — cascade liquidations, margin calls
    (EventType.LIQUIDATION, [
        "liquidat", "margin call", "forced selling",
        "cascade", "billion liquidated", "million liquidated",
        "wipeout", "rekt", "short squeeze", "long squeeze",
    ], 0.6),

    # EXCHANGE_RISK — exchange problems, insolvency
    (EventType.EXCHANGE_RISK, [
        "exchange halt", "withdrawal halt", "freeze withdrawal",
        "freezes withdrawal", "suspends trading", "insolvent",
        "bankruptcy", "bank run", "proof of reserves",
        "ftx", "celsius", "voyager", "deposits frozen",
        "suspends operation", "halts trading", "liquidity concern",
        "liquidity crisis",
    ], 0.8),

    # REGULATION — government/legal actions
    (EventType.REGULATION, [
        "ban", "banned", "illegal", "crackdown", "sec sue",
        "sec charges", "cftc", "lawsuit", "enforcement",
        "sanction", "prohibition", "regulatory action",
        "indicted", "arrested", "criminal charges",
        "tax", "compliance order",
    ], 0.6),

    # MACRO_SHOCK — broad economic events affecting all markets
    (EventType.MACRO_SHOCK, [
        "war", "invade", "invasion", "missile", "nuclear",
        "pandemic", "covid", "lockdown",
        "bank failure", "bank collapse", "svb",
        "default", "debt ceiling", "credit crisis",
        "rate hike", "emergency rate",
    ], 0.7),

    # DEPEG — stablecoin depegging, loss of peg
    (EventType.DEPEG, [
        "depeg", "de-peg", "lost peg", "loses peg", "below peg",
        "usdt depeg", "usdc depeg", "dai depeg", "stablecoin risk",
        "stablecoin crash", "redemption halt", "reserve shortfall",
    ], 0.8),

    # ADOPTION — positive but doesn't require risk action
    (EventType.ADOPTION, [
        "etf approved", "etf approval", "etf product", "etf launch",
        "institutional", "legal tender", "adopt", "partnership",
        "launches bitcoin", "accepts crypto", "accepts bitcoin",
        "reserve", "treasury", "blackrock", "fidelity",
        "upgrade success", "hard fork complete", "merge complete",
    ], 0.3),
]

# Risk action rules per event type
_RISK_ACTIONS = {
    EventType.HACK: {
        "action": "reduce_exposure",
        "dampening": 0.7,          # dampen 70% of alpha signals
        "description": "Security breach detected — reduce position sizes, widen stops",
    },
    EventType.REGULATION: {
        "action": "reduce_exposure",
        "dampening": 0.5,
        "description": "Regulatory action detected — reduce exposure, pause new entries",
    },
    EventType.LIQUIDATION: {
        "action": "tighten_stops",
        "dampening": 0.3,          # less dampening — liquidation cascades often reverse
        "description": "Liquidation cascade — tighten stops but potential reversal",
    },
    EventType.EXCHANGE_RISK: {
        "action": "reduce_exposure",
        "dampening": 0.8,
        "description": "Exchange risk — significantly reduce exposure",
    },
    EventType.MACRO_SHOCK: {
        "action": "reduce_all",
        "dampening": 0.6,
        "description": "Macro shock — reduce all positions",
    },
    EventType.DEPEG: {
        "action": "reduce_exposure",
        "dampening": 0.7,
        "description": "Stablecoin depeg risk — reduce exposure, risk of cascading liquidations",
    },
    EventType.ADOPTION: {
        "action": "none",
        "dampening": 0.0,
        "description": "Positive adoption news — no risk adjustment needed",
    },
    EventType.ROUTINE: {
        "action": "none",
        "dampening": 0.0,
        "description": "Routine news — no risk adjustment",
    },
}


def classify_event(
    title: str,
    nlp_score: float | None = None,
    nlp_confidence: float | None = None,
    body: str | None = None,
) -> EventClassification:
    """Classify a news item into an event type.

    Uses keyword matching (fast, deterministic) combined with
    NLP sentiment score for severity calibration.
    """
    text = (title + " " + (body or "")).lower()

    # Try each pattern in priority order
    for event_type, keywords, base_severity in _PATTERNS:
        matched = [kw for kw in keywords if kw in text]
        if matched:
            # More keyword matches = higher confidence
            confidence = min(len(matched) / 3, 1.0)

            # Adjust severity by NLP score
            severity = base_severity
            if nlp_score is not None and nlp_confidence is not None:
                # Strongly negative NLP + high confidence → higher severity
                if nlp_score < -0.3 and nlp_confidence > 0.7:
                    severity = min(severity + 0.2, 1.0)
                # NLP disagrees with negative classification → lower severity
                elif nlp_score > 0.3 and event_type in (
                    EventType.HACK, EventType.REGULATION,
                    EventType.EXCHANGE_RISK, EventType.MACRO_SHOCK,
                ):
                    severity *= 0.5

            rule = _RISK_ACTIONS[event_type]
            dampening = rule["dampening"] * severity

            return EventClassification(
                event_type=event_type,
                confidence=confidence,
                severity=severity,
                risk_action=rule["action"],
                signal_dampening=dampening,
                details=f"matched: {', '.join(matched[:3])}",
            )

    # No pattern matched → routine
    rule = _RISK_ACTIONS[EventType.ROUTINE]
    return EventClassification(
        event_type=EventType.ROUTINE,
        confidence=0.9,
        severity=0.0,
        risk_action=rule["action"],
        signal_dampening=0.0,
        details="no risk keywords detected",
    )


def classify_market_anomaly(
    funding_rate: float | None = None,
    oi_change_1h_pct: float | None = None,
    volume_zscore: float | None = None,
    price_change_1h_pct: float | None = None,
    liquidation_volume_usd: float | None = None,
) -> EventClassification | None:
    """Classify market microstructure anomalies.

    Returns classification only if an anomaly is detected.
    These are faster than news — they happen in real-time.
    """
    anomalies = []

    # Extreme funding rate → overleveraged
    if funding_rate is not None and abs(funding_rate) > 0.05:
        direction = "shorts" if funding_rate > 0 else "longs"
        anomalies.append((
            EventType.LIQUIDATION,
            abs(funding_rate) / 0.1,  # severity scales with funding
            f"extreme_funding({funding_rate:+.4f}): {direction} overleveraged",
        ))

    # Sudden OI drop → mass liquidation
    if oi_change_1h_pct is not None and oi_change_1h_pct < -5.0:
        anomalies.append((
            EventType.LIQUIDATION,
            min(abs(oi_change_1h_pct) / 10, 1.0),
            f"oi_crash({oi_change_1h_pct:+.1f}%): mass position closure",
        ))

    # Volume spike → something is happening
    if volume_zscore is not None and volume_zscore > 3.0:
        anomalies.append((
            EventType.LIQUIDATION if (price_change_1h_pct or 0) < -3 else EventType.ROUTINE,
            min(volume_zscore / 5, 1.0),
            f"volume_spike(z={volume_zscore:.1f})",
        ))

    # Large liquidations
    if liquidation_volume_usd is not None and liquidation_volume_usd > 100_000_000:
        anomalies.append((
            EventType.LIQUIDATION,
            min(liquidation_volume_usd / 500_000_000, 1.0),
            f"mass_liquidation(${liquidation_volume_usd/1e6:.0f}M)",
        ))

    # Flash crash detection
    if price_change_1h_pct is not None and price_change_1h_pct < -8:
        anomalies.append((
            EventType.MACRO_SHOCK,
            min(abs(price_change_1h_pct) / 15, 1.0),
            f"flash_crash({price_change_1h_pct:+.1f}%)",
        ))

    if not anomalies:
        return None

    # Return the most severe anomaly
    anomalies.sort(key=lambda x: -x[1])
    event_type, severity, detail = anomalies[0]
    rule = _RISK_ACTIONS[event_type]

    return EventClassification(
        event_type=event_type,
        confidence=0.8,
        severity=severity,
        risk_action=rule["action"],
        signal_dampening=rule["dampening"] * severity,
        details=detail,
    )
