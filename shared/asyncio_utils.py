from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any

from shared.logging import get_logger

logger = get_logger("shared-asyncio")


def _run_in_thread(coro: Coroutine[Any, Any, Any]) -> None:
    try:
        asyncio.run(coro)
    except Exception as exc:  # pragma: no cover - exercised in compose runtime
        logger.exception(
            "background_coroutine_failed",
            extra={
                "service": "shared-asyncio",
                "event_type": "background.coroutine.failed",
            },
        )
        raise exc


def run_coro(coro: Coroutine[Any, Any, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        thread = threading.Thread(target=_run_in_thread, args=(coro,), daemon=True)
        thread.start()
        return
    loop.create_task(coro)
