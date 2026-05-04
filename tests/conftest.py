"""Root conftest: Python < 3.11 UTC patch + per-module `app.*` cache reset.

`services/*/app/`는 각 서비스마다 독립적인 네임스페이스인데, pytest가 여러
테스트 파일을 같은 프로세스에서 collect하다 보면 `app` 모듈이 처음 import된
서비스에 고정되어 다른 서비스 import 시 `ModuleNotFoundError` 발생.

각 test 모듈 시작 전에 `app` / `app.*` sys.modules 캐시를 치워서 재 import
가 항상 현재 sys.path를 따라가도록 함.
"""

import datetime
import sys

import pytest

if sys.version_info < (3, 11) and not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # type: ignore[attr-defined]


@pytest.fixture(autouse=True, scope="module")
def _reset_app_modules():
    """Clear cached `app.*` imports between test modules."""
    # After each test module, remove `app` and its submodules from the cache
    # so the next module's `sys.path.insert(0, ...)` is actually honored.
    yield
    to_drop = [m for m in sys.modules if m == "app" or m.startswith("app.")]
    for m in to_drop:
        sys.modules.pop(m, None)
