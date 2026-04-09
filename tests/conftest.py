"""Root conftest: patch datetime.UTC for Python < 3.11 compatibility."""

import datetime
import sys

if sys.version_info < (3, 11) and not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]
