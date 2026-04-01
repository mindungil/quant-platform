"""Admin DLQ management endpoints."""
from __future__ import annotations

import asyncio
import json
import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.auth import require_role
from app.models.auth import GatewayPrincipal

router = APIRouter()

STREAMS = ["MARKET_DATA", "FEATURE_DATA", "SIGNAL_DATA", "EXECUTION_DATA"]


async def _nats_connect():
    import nats

    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
    nc = await nats.connect(nats_url)
    return nc


async def _count_dlq_for_stream(js, stream: str) -> int:
    """Count DLQ messages in a stream by inspecting stream info."""
    try:
        info = await js.find_stream_name_by_subject(f"*.*.*.dlq")
    except Exception:
        pass

    count = 0
    consumer_name = f"dlq-stats-{stream.lower().replace('_', '-')}"
    try:
        sub = await js.pull_subscribe(
            ">",
            durable=consumer_name,
            stream=stream,
        )
        try:
            while True:
                try:
                    messages = await sub.fetch(batch=100, timeout=2)
                except Exception:
                    break
                if not messages:
                    break
                for msg in messages:
                    if msg.subject.endswith(".dlq"):
                        count += 1
                    await msg.nak()  # Don't consume — just count
        except Exception:
            pass
    except Exception:
        pass
    return count


@router.get("/admin/dlq/stats")
async def dlq_stats(principal: GatewayPrincipal = Depends(require_role("admin"))) -> JSONResponse:
    """Count of DLQ messages per stream."""
    try:
        nc = await _nats_connect()
        js = nc.jetstream()

        result: dict[str, int] = {}
        for stream in STREAMS:
            result[stream] = await _count_dlq_for_stream(js, stream)

        await nc.drain()
        return JSONResponse({"streams": result, "total": sum(result.values())})
    except Exception as exc:
        return JSONResponse(
            {"error": "nats_unavailable", "detail": str(exc)},
            status_code=503,
        )


@router.post("/admin/dlq/reprocess/{stream}")
async def dlq_reprocess(
    stream: str,
    principal: GatewayPrincipal = Depends(require_role("admin")),
) -> JSONResponse:
    """Trigger reprocessing for a specific stream."""
    if stream not in STREAMS:
        return JSONResponse(
            {"error": "invalid_stream", "valid": STREAMS},
            status_code=400,
        )

    try:
        nc = await _nats_connect()
        js = nc.jetstream()

        consumer_name = f"dlq-reprocess-admin-{stream.lower().replace('_', '-')}"
        reprocessed = 0
        errors = 0

        try:
            sub = await js.pull_subscribe(
                ">",
                durable=consumer_name,
                stream=stream,
            )

            while True:
                try:
                    messages = await sub.fetch(batch=100, timeout=2)
                except Exception:
                    break
                if not messages:
                    break
                for msg in messages:
                    if not msg.subject.endswith(".dlq"):
                        await msg.ack()
                        continue
                    original_subject = msg.subject.removesuffix(".dlq")
                    try:
                        envelope = json.loads(msg.data.decode("utf-8"))
                        original_data = envelope.get("data", envelope)
                        await js.publish(original_subject, json.dumps(original_data).encode("utf-8"))
                        await msg.ack()
                        reprocessed += 1
                    except Exception:
                        errors += 1
                        try:
                            await msg.ack()
                        except Exception:
                            pass
        except Exception:
            pass

        await nc.drain()

        return JSONResponse({
            "stream": stream,
            "reprocessed": reprocessed,
            "errors": errors,
            "triggered_by": principal.user_id,
        })
    except Exception as exc:
        return JSONResponse(
            {"error": "nats_unavailable", "detail": str(exc)},
            status_code=503,
        )
