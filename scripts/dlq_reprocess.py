"""DLQ Reprocessor — re-publishes dead-letter messages to their original subjects.

Usage:
    python3 scripts/dlq_reprocess.py [--stream MARKET_DATA] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import nats
from nats.js.api import ConsumerConfig

STREAMS = ["MARKET_DATA", "FEATURE_DATA", "SIGNAL_DATA", "EXECUTION_DATA"]


async def _get_dlq_messages(js, stream: str) -> list[tuple[str, bytes, object]]:
    """Return list of (subject, raw_data, msg) for DLQ messages in a stream."""
    results: list[tuple[str, bytes, object]] = []
    consumer_name = f"dlq-reprocess-{stream.lower().replace('_', '-')}"
    try:
        sub = await js.pull_subscribe(
            "*.*.*.dlq",
            durable=consumer_name,
            stream=stream,
            config=ConsumerConfig(filter_subject="*.*.*.dlq"),
        )
    except Exception:
        # Try broader pattern — DLQ subjects may vary
        try:
            sub = await js.pull_subscribe(
                ">",
                durable=f"{consumer_name}-all",
                stream=stream,
            )
        except Exception:
            return results

    try:
        while True:
            try:
                messages = await sub.fetch(batch=100, timeout=2)
            except nats.errors.TimeoutError:
                break
            if not messages:
                break
            for msg in messages:
                if msg.subject.endswith(".dlq"):
                    results.append((msg.subject, msg.data, msg))
                else:
                    # Not a DLQ message — ack and skip
                    await msg.ack()
    except Exception:
        pass

    return results


async def reprocess(stream_filter: str | None, dry_run: bool) -> dict:
    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
    nc = await nats.connect(nats_url)
    js = nc.jetstream()

    streams = [stream_filter] if stream_filter else STREAMS
    total_found = 0
    reprocessed = 0
    errors = 0
    details: list[dict] = []

    for stream in streams:
        dlq_messages = await _get_dlq_messages(js, stream)
        total_found += len(dlq_messages)

        for subject, raw_data, msg in dlq_messages:
            original_subject = subject.removesuffix(".dlq")
            try:
                envelope = json.loads(raw_data.decode("utf-8"))
                # The DLQ envelope wraps the original event in the "data" field
                original_data = envelope.get("data", envelope)
                payload = json.dumps(original_data).encode("utf-8")

                if dry_run:
                    print(f"[DRY-RUN] Would republish {subject} -> {original_subject}")
                    details.append({"stream": stream, "subject": subject, "action": "dry-run"})
                else:
                    await js.publish(original_subject, payload)
                    await msg.ack()
                    details.append({"stream": stream, "subject": subject, "action": "reprocessed"})
                reprocessed += 1
            except Exception as exc:
                errors += 1
                details.append({"stream": stream, "subject": subject, "error": str(exc)})
                # Still ack to avoid infinite loop on poison messages
                if not dry_run:
                    try:
                        await msg.ack()
                    except Exception:
                        pass

    await nc.drain()

    return {
        "total_found": total_found,
        "reprocessed": reprocessed,
        "errors": errors,
        "dry_run": dry_run,
        "details": details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reprocess DLQ messages")
    parser.add_argument("--stream", type=str, default=None, help="Limit to a specific stream (e.g. MARKET_DATA)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without republishing")
    args = parser.parse_args()

    if args.stream and args.stream not in STREAMS:
        print(f"Unknown stream: {args.stream}. Valid: {STREAMS}")
        sys.exit(1)

    result = asyncio.run(reprocess(args.stream, args.dry_run))

    print(f"\n{'=' * 50}")
    print(f"DLQ Reprocess Report")
    print(f"{'=' * 50}")
    print(f"Total DLQ messages found: {result['total_found']}")
    print(f"Reprocessed:              {result['reprocessed']}")
    print(f"Errors:                   {result['errors']}")
    print(f"Dry run:                  {result['dry_run']}")
    if result["details"]:
        print(f"\nDetails:")
        for detail in result["details"]:
            print(f"  {detail}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
