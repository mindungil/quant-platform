"""Self-improving trading engine.

Combines alpha generation, ensemble allocation, and automated
performance management into a cohesive system that adapts without
manual intervention.

Components:
  - EngineConfig: unified configuration with refit/health/adaptive params
  - PerformanceLogger: per-bar attribution logging to disk
  - AlphaHealthMonitor: rolling multi-window health assessment
  - AdaptiveTimeframe: vol-regime-driven 1h/8h selection
  - RollingRefitter: weekly parameter re-optimization with OOS gating
"""

from shared.engine.config import EngineConfig, load_config, save_config
from shared.engine.logger import PerformanceLogger
from shared.engine.health import AlphaHealthMonitor, AlphaHealth
from shared.engine.adaptive_tf import AdaptiveTimeframe, TimeframeDecision
from shared.engine.refit import RollingRefitter, RefitResult

__all__ = [
    "EngineConfig", "load_config", "save_config",
    "PerformanceLogger",
    "AlphaHealthMonitor", "AlphaHealth",
    "AdaptiveTimeframe", "TimeframeDecision",
    "RollingRefitter", "RefitResult",
]
