"""Online learning closed loop — wraps V2 monitors (OnlineDSR,
AlphaPauseDecider, FactorDecayMonitor) into a single LearningLoop
that incubator / strategy-lab daemon code can call once per cycle
and get back a list of state transitions (LIVE↔SHADOW per alpha,
decay flag per factor)."""
from shared.learning.loop import (
    AlphaLoopResult,
    FactorLoopResult,
    LearningLoop,
    LearningLoopConfig,
)
from shared.learning.persist import (
    decider_to_dict,
    dict_to_decider,
    dict_to_factor_buffer,
    dict_to_online_dsr,
    factor_buffer_to_dict,
    online_dsr_to_dict,
)

__all__ = [
    "LearningLoop",
    "LearningLoopConfig",
    "AlphaLoopResult",
    "FactorLoopResult",
    "online_dsr_to_dict",
    "dict_to_online_dsr",
    "decider_to_dict",
    "dict_to_decider",
    "factor_buffer_to_dict",
    "dict_to_factor_buffer",
]
