#!/usr/bin/env python3
"""Sentiment collection daemon with adaptive multi-tier polling.

Three polling tiers with different intervals:
  fast  (3 min):  CoinDesk, Decrypt, TheBlock — low cache, frequent updates
  slow  (15 min): CoinTelegraph, TheDefiant — high CDN cache / infrequent
  rare  (30 min): NewsAPI (100/day limit), CryptoPanic, FNG, Reddit

Each cycle runs the fastest tier. Slower tiers piggyback when their
interval aligns. CryptoBERT + DB connections stay warm in memory.

Usage:
  python scripts/sentiment_pipeline.py              # daemon (default)
  python scripts/sentiment_pipeline.py --once       # single full cycle
  python scripts/sentiment_pipeline.py --fast-interval 180  # custom

Designed to run as: systemctl start sentiment-daemon
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "external-data-service"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sentiment-daemon")

ASSETS = ["BTC", "ETH", "SOL"]

# ─── Polling intervals (seconds) ─────────────────────────────
FAST_INTERVAL = int(os.getenv("POLL_FAST_INTERVAL", "180"))    # 3 min
SLOW_INTERVAL = int(os.getenv("POLL_SLOW_INTERVAL", "900"))    # 15 min
RARE_INTERVAL = int(os.getenv("POLL_RARE_INTERVAL", "1800"))   # 30 min

# ─── Pre-warmed resources ────────────────────────────────────
_repo = None
_scorer = None
_warmup_done = False


def warmup():
    """Load CryptoBERT + DB once at startup."""
    global _repo, _scorer, _warmup_done
    if _warmup_done:
        return

    t0 = time.time()
    logger.info("warming up...")

    from app.db.sentiment_repo import sentiment_repository
    _repo = sentiment_repository

    try:
        from app.core.sentiment_scorer import sentiment_scorer
        sentiment_scorer.score("warmup")  # force model load
        _scorer = sentiment_scorer
        logger.info("CryptoBERT loaded")
    except Exception as e:
        logger.warning("NLP scorer unavailable: %s", str(e)[:100])

    try:
        from app.core.embedding_client import embedding_client
        asyncio.get_event_loop().run_until_complete(embedding_client.health_check())
    except Exception:
        pass

    _warmup_done = True
    logger.info("warmup done in %.1fs", time.time() - t0)


async def run_collect(tier: str) -> dict:
    """Collect from sources of the given tier."""
    from app.core.sentiment_collector import collect_all
    return await collect_all(tier=tier)


async def run_process(items: list[dict], fng: dict | None) -> dict:
    """Score, aggregate, classify, embed, label."""
    warmup()

    # 1. Store items
    inserted = _repo.insert_items(items) if items else 0

    # 2. NLP scoring
    scored = 0
    if _scorer:
        unscored = _repo.get_unscored_items(limit=200)
        for item in unscored:
            text = item["title"]
            if item.get("body"):
                text = f"{item['title']}. {item['body'][:300]}"
            s = _scorer.score(text)
            _repo.update_nlp_score(
                item["id"], item["asset"], str(item["timestamp"]),
                s["score"], s["model"], s["confidence"],
            )
            scored += 1

    # 3. Hourly aggregation
    now = datetime.now(timezone.utc)
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    fng_val = fng["value"] if fng else None

    for asset in ASSETS:
        _repo.aggregate_hour(asset, hour_start, fng_value=fng_val)

    # 4. RAG pipeline (data accumulation + event classification)
    rag_stats = {}
    try:
        from scripts.rag_sentiment_pipeline import run_full_pipeline
        rag_stats = await run_full_pipeline()
    except Exception as e:
        logger.warning("rag pipeline error: %s", str(e)[:200])

    # 5. Market anomaly monitor (funding, OI, taker ratio)
    anomaly_count = 0
    try:
        from scripts.market_anomaly_monitor import run_monitor_cycle
        anomaly_result = await run_monitor_cycle()
        anomaly_count = anomaly_result.get("anomalies", 0)
    except Exception as e:
        logger.warning("anomaly monitor error: %s", str(e)[:200])

    return {
        "inserted": inserted,
        "scored": scored,
        "rag_classified": rag_stats.get("classified", 0),
        "rag_tier2": rag_stats.get("tier2", 0),
        "rag_embedded": rag_stats.get("embedded", 0),
        "rag_labeled": rag_stats.get("labeled", 0),
        "anomalies": anomaly_count,
    }


async def run_full_cycle() -> dict:
    """Single full cycle (all tiers). Used for --once mode."""
    result = await run_collect("all")
    stats = await run_process(result["items"], result["fng"])
    stats["collected"] = len(result["items"])
    return stats


def run_daemon(fast_interval: int = FAST_INTERVAL):
    """Adaptive polling daemon."""
    shutdown = False

    def handle_signal(sig, frame):
        nonlocal shutdown
        logger.info("signal %d received, shutting down...", sig)
        shutdown = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    warmup()

    logger.info(
        "daemon started — fast=%ds, slow=%ds, rare=%ds",
        fast_interval, SLOW_INTERVAL, RARE_INTERVAL,
    )

    cycle = 0
    last_slow = 0.0
    last_rare = 0.0

    while not shutdown:
        cycle += 1
        t0 = time.time()
        now = time.time()

        try:
            # Determine which tiers to run this cycle
            tiers_to_run = ["fast"]

            if now - last_slow >= SLOW_INTERVAL:
                tiers_to_run.append("slow")
                last_slow = now

            if now - last_rare >= RARE_INTERVAL:
                tiers_to_run.append("rare")
                last_rare = now

            # Collect from all due tiers
            all_items = []
            fng = None

            for tier in tiers_to_run:
                result = asyncio.run(run_collect(tier))
                all_items.extend(result["items"])
                if result.get("fng"):
                    fng = result["fng"]

            # Process everything together
            stats = asyncio.run(run_process(all_items, fng))

            elapsed = time.time() - t0
            logger.info(
                "#%d [%s] %.1fs — collected=%d inserted=%d scored=%d | rag: classified=%d t2=%d emb=%d labeled=%d | anomalies=%d",
                cycle,
                "+".join(tiers_to_run),
                elapsed,
                len(all_items),
                stats["inserted"],
                stats["scored"],
                stats.get("rag_classified", 0),
                stats["rag_tier2"],
                stats["rag_embedded"],
                stats["rag_labeled"],
                stats["anomalies"],
            )

        except Exception:
            logger.exception("cycle #%d failed", cycle)

        # Sleep in 1s increments for responsive shutdown
        for _ in range(fast_interval):
            if shutdown:
                break
            time.sleep(1)

    logger.info("daemon stopped after %d cycles", cycle)


def main():
    ap = argparse.ArgumentParser(description="Sentiment daemon")
    ap.add_argument("--once", action="store_true", help="Single full cycle")
    ap.add_argument("--fast-interval", type=int, default=FAST_INTERVAL,
                    help=f"Fast tier interval in seconds (default: {FAST_INTERVAL})")
    args = ap.parse_args()

    if args.once:
        result = asyncio.run(run_full_cycle())
        print(f"Done: {result}")
    else:
        run_daemon(fast_interval=args.fast_interval)


# Backward-compat lazy loader
_loader_path = os.path.join(REPO_ROOT, "services", "external_data_service_loader.py")
if not os.path.exists(_loader_path):
    with open(_loader_path, "w") as f:
        f.write('''"""Lazy loader for external-data-service modules."""
import sys, os
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_svc = os.path.join(_root, "services", "external-data-service")
if _svc not in sys.path:
    sys.path.insert(0, _svc)
if _root not in sys.path:
    sys.path.insert(0, _root)

def load_collector():
    from app.core.sentiment_collector import collect_all
    class _C:
        async def collect_all(self):
            return await collect_all()
    return _C()

def load_repo():
    from app.db.sentiment_repo import sentiment_repository
    return sentiment_repository

def load_scorer():
    try:
        from app.core.sentiment_scorer import sentiment_scorer
        return sentiment_scorer
    except Exception:
        return None
''')


if __name__ == "__main__":
    main()
