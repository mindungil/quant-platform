"""Observability package — HTTP middleware + live-drift monitor."""
from shared.observability._core import *  # noqa: F401,F403
from shared.observability._core import (  # noqa: F401
    install_http_observability,
    startup_dependency_guard,
)
try:  # noqa: SIM105
    from shared.observability.live_drift import (  # noqa: F401
        LiveDriftMonitor,
        DriftAlert,
    )
except ModuleNotFoundError:
    # Some lightweight service images do not ship the scientific stack.
    # They still need the HTTP observability helpers, so keep live-drift optional.
    LiveDriftMonitor = None  # type: ignore[assignment]
    DriftAlert = None  # type: ignore[assignment]
