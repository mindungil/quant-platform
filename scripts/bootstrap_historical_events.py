#!/usr/bin/env python3
"""Bootstrap RAG with historical crypto events.

Seeds the event_embeddings table with well-known events where we already
know the price outcome. These become the initial "memory" for the RAG system.

Events are curated from major crypto milestones 2017-2026:
  - Regulatory actions (China bans, SEC lawsuits, ETF approvals)
  - Market structure (halvings, forks, exchange collapses)
  - Adoption (Tesla, MicroStrategy, El Salvador, BlackRock)
  - Security incidents (hacks, exploits, rug pulls)
  - Macro events (COVID crash, Fed rate changes, bank failures)

Each event has:
  - Timestamp, asset, source text (for embedding)
  - Actual price outcome will be auto-labeled from OHLCV data

Run once to seed, then the auto-labeler fills in price outcomes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "external-data-service"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap-events")


# ═══════════════════════════════════════════════════════════════
# Curated Historical Events
# Format: (date, asset, title, source, severity)
# ═══════════════════════════════════════════════════════════════

HISTORICAL_EVENTS = [
    # ── 2017 ──
    ("2017-09-04", "BTC", "China bans ICOs and orders immediate halt to token sales", "regulatory", 3.0),
    ("2017-09-15", "BTC", "China orders all domestic cryptocurrency exchanges to shut down", "regulatory", 3.0),
    ("2017-12-10", "BTC", "CBOE launches Bitcoin futures trading for the first time", "institutional", 2.5),
    ("2017-12-17", "BTC", "Bitcoin reaches all-time high near $20,000", "market", 2.5),
    ("2017-12-18", "BTC", "CME Group launches Bitcoin futures contracts", "institutional", 2.5),

    # ── 2018 ──
    ("2018-01-06", "ETH", "Ethereum surges past $1,000 for the first time reaching ATH", "market", 2.5),
    ("2018-01-26", "BTC", "Coincheck hack loses $530 million in NEM tokens", "hack", 3.0),
    ("2018-01-30", "BTC", "Facebook bans all cryptocurrency advertising on platform", "regulatory", 2.0),
    ("2018-03-07", "BTC", "SEC says online cryptocurrency trading platforms must register", "regulatory", 2.5),
    ("2018-06-20", "BTC", "South Korea crypto exchange Bithumb hacked for $31 million", "hack", 2.5),
    ("2018-11-14", "BTC", "Bitcoin Cash hard fork war begins between ABC and SV", "market", 2.0),
    ("2018-11-19", "BTC", "Bitcoin crashes below $5,000 amid crypto winter fears", "market", 3.0),

    # ── 2019 ──
    ("2019-04-02", "BTC", "Bitcoin surges 20% above $5,000 in sudden rally breaking bear trend", "market", 2.5),
    ("2019-06-18", "BTC", "Facebook announces Libra cryptocurrency project sparking debate", "adoption", 2.0),
    ("2019-06-26", "BTC", "Bitcoin surges above $13,000 reaching 2019 high", "market", 2.0),
    ("2019-09-23", "BTC", "Bakkt launches physically-delivered Bitcoin futures", "institutional", 2.0),
    ("2019-10-25", "BTC", "China's Xi Jinping endorses blockchain technology development", "adoption", 2.5),

    # ── 2020 ──
    ("2020-01-03", "BTC", "US airstrike kills Iranian general Soleimani raising war fears", "macro", 2.5),
    ("2020-03-12", "BTC", "Bitcoin crashes 40% in one day as COVID pandemic triggers global selloff", "macro", 3.0),
    ("2020-03-12", "ETH", "Ethereum plunges below $100 as COVID crash hits crypto markets", "macro", 3.0),
    ("2020-05-11", "BTC", "Bitcoin third halving reduces block reward from 12.5 to 6.25 BTC", "market", 2.5),
    ("2020-08-11", "BTC", "MicroStrategy announces $250 million Bitcoin purchase as treasury reserve", "institutional", 2.5),
    ("2020-10-08", "BTC", "Square invests $50 million in Bitcoin for corporate treasury", "institutional", 2.0),
    ("2020-10-21", "BTC", "PayPal announces cryptocurrency buying selling and holding for US users", "adoption", 2.5),
    ("2020-12-16", "BTC", "Bitcoin breaks all-time high surpassing $20,000 for first time since 2017", "market", 2.5),
    ("2020-12-01", "ETH", "Ethereum 2.0 Beacon Chain launches beginning proof-of-stake transition", "market", 2.5),

    # ── 2021 ──
    ("2021-01-29", "BTC", "Elon Musk adds Bitcoin to Twitter bio triggering massive price surge", "adoption", 2.0),
    ("2021-02-08", "BTC", "Tesla purchases $1.5 billion in Bitcoin and will accept BTC payments", "institutional", 3.0),
    ("2021-02-19", "BTC", "Bitcoin market cap surpasses $1 trillion for the first time", "market", 2.5),
    ("2021-04-14", "BTC", "Coinbase goes public on NASDAQ via direct listing at $86B valuation", "institutional", 2.5),
    ("2021-05-12", "BTC", "Elon Musk suspends Tesla Bitcoin payments citing energy concerns", "adoption", 3.0),
    ("2021-05-19", "BTC", "China crackdown causes Bitcoin to crash from $43K to $30K in hours", "regulatory", 3.0),
    ("2021-05-19", "ETH", "Ethereum crashes 40% alongside Bitcoin during China mining ban panic", "regulatory", 3.0),
    ("2021-06-09", "BTC", "El Salvador becomes first country to adopt Bitcoin as legal tender", "adoption", 3.0),
    ("2021-09-07", "BTC", "El Salvador officially launches Bitcoin as legal tender amid protests", "adoption", 2.5),
    ("2021-09-24", "BTC", "China declares all cryptocurrency transactions illegal in complete ban", "regulatory", 3.0),
    ("2021-10-19", "BTC", "ProShares Bitcoin Strategy ETF launches as first US Bitcoin futures ETF", "institutional", 2.5),
    ("2021-11-10", "BTC", "Bitcoin reaches new all-time high of $69,000", "market", 2.5),
    ("2021-11-10", "ETH", "Ethereum reaches all-time high above $4,800", "market", 2.5),

    # ── 2022 ──
    ("2022-01-20", "BTC", "Russia central bank proposes full ban on crypto mining and trading", "regulatory", 2.5),
    ("2022-02-24", "BTC", "Russia invades Ukraine triggering global market selloff and crypto crash", "macro", 3.0),
    ("2022-03-09", "BTC", "Biden signs executive order on responsible development of digital assets", "regulatory", 2.0),
    ("2022-05-09", "BTC", "Terra UST stablecoin depegs triggering massive crypto market selloff", "market", 3.0),
    ("2022-05-12", "BTC", "Bitcoin crashes below $27K as Terra Luna collapses to near zero", "market", 3.0),
    ("2022-05-12", "ETH", "Ethereum drops below $2,000 amid Terra contagion fears", "market", 3.0),
    ("2022-06-13", "BTC", "Celsius Network freezes withdrawals triggering crypto lending crisis", "market", 3.0),
    ("2022-06-18", "BTC", "Bitcoin crashes below $18,000 as Three Arrows Capital faces insolvency", "market", 3.0),
    ("2022-07-06", "BTC", "Voyager Digital files for bankruptcy after Three Arrows Capital default", "market", 2.5),
    ("2022-09-15", "ETH", "Ethereum successfully completes The Merge transitioning to proof-of-stake", "market", 2.5),
    ("2022-11-02", "BTC", "CoinDesk reveals Alameda Research balance sheet is mostly FTT tokens", "market", 3.0),
    ("2022-11-08", "BTC", "Binance announces intent to acquire FTX amid liquidity crisis", "market", 3.0),
    ("2022-11-09", "BTC", "Binance backs out of FTX deal as due diligence reveals massive hole", "market", 3.0),
    ("2022-11-11", "BTC", "FTX files for Chapter 11 bankruptcy Sam Bankman-Fried resigns as CEO", "market", 3.0),
    ("2022-12-12", "BTC", "Sam Bankman-Fried arrested in Bahamas on US fraud charges", "regulatory", 2.5),

    # ── 2023 ──
    ("2023-01-13", "BTC", "Bitcoin rallies above $19K breaking out of post-FTX trading range", "market", 2.0),
    ("2023-02-09", "BTC", "SEC sues Kraken over unregistered staking program forces shutdown", "regulatory", 2.5),
    ("2023-03-08", "BTC", "Silvergate Bank announces voluntary liquidation amid crypto downturn", "market", 2.5),
    ("2023-03-10", "BTC", "Silicon Valley Bank collapses triggering Bitcoin surge as banking fear spreads", "macro", 3.0),
    ("2023-03-11", "BTC", "USDC depegs to $0.88 after Circle reveals $3.3B stuck at SVB", "market", 3.0),
    ("2023-03-27", "BTC", "CFTC sues Binance and CZ for operating illegal exchange in the US", "regulatory", 2.5),
    ("2023-06-05", "BTC", "SEC sues Binance for securities violations in major enforcement action", "regulatory", 3.0),
    ("2023-06-06", "BTC", "SEC sues Coinbase for operating as unregistered exchange", "regulatory", 3.0),
    ("2023-06-15", "BTC", "BlackRock files for spot Bitcoin ETF application", "institutional", 3.0),
    ("2023-06-21", "BTC", "Fidelity refiling spot Bitcoin ETF following BlackRock momentum", "institutional", 2.0),
    ("2023-08-29", "BTC", "Court rules in Grayscale favor saying SEC wrong to reject Bitcoin ETF", "regulatory", 2.5),
    ("2023-10-16", "BTC", "Bitcoin surges on false report that BlackRock Bitcoin ETF approved", "market", 2.5),
    ("2023-10-24", "BTC", "Bitcoin rallies past $35K on growing spot ETF approval expectations", "market", 2.0),
    ("2023-11-21", "BTC", "Binance pleads guilty to criminal charges CZ resigns pays $4.3B fine", "regulatory", 3.0),

    # ── 2024 ──
    ("2024-01-10", "BTC", "SEC approves 11 spot Bitcoin ETFs including BlackRock and Fidelity", "institutional", 3.0),
    ("2024-01-10", "ETH", "Ethereum surges on Bitcoin spot ETF approval momentum and ETH ETF hopes", "institutional", 2.5),
    ("2024-02-15", "BTC", "Bitcoin breaks above $50K for first time since December 2021", "market", 2.0),
    ("2024-03-05", "BTC", "Bitcoin surges past previous all-time high of $69K setting new record", "market", 2.5),
    ("2024-03-14", "BTC", "Bitcoin reaches new all-time high above $73,000", "market", 2.5),
    ("2024-04-20", "BTC", "Bitcoin fourth halving reduces block reward from 6.25 to 3.125 BTC", "market", 2.5),
    ("2024-05-20", "ETH", "SEC unexpectedly signals approval of spot Ethereum ETFs shocking market", "institutional", 3.0),
    ("2024-05-23", "ETH", "SEC officially approves spot Ethereum ETF applications", "institutional", 3.0),
    ("2024-07-05", "BTC", "German government begins selling seized 50,000 Bitcoin crashing price", "market", 2.5),
    ("2024-07-23", "ETH", "Spot Ethereum ETFs begin trading on US exchanges", "institutional", 2.5),
    ("2024-08-05", "BTC", "Global market crash on Japan carry trade unwind Bitcoin drops to $49K", "macro", 3.0),
    ("2024-08-05", "ETH", "Ethereum crashes below $2,200 during global carry trade liquidation", "macro", 3.0),
    ("2024-09-18", "BTC", "Fed cuts interest rates by 50 basis points first cut since 2020", "macro", 2.5),
    ("2024-11-05", "BTC", "Donald Trump wins US presidential election on pro-crypto platform", "regulatory", 3.0),
    ("2024-11-13", "BTC", "Bitcoin surges past $90,000 on Trump election euphoria", "market", 2.5),
    ("2024-12-05", "BTC", "Bitcoin crosses $100,000 for the first time in history", "market", 3.0),

    # ── 2025 ──
    ("2025-01-20", "BTC", "Trump inaugurated as president promising crypto-friendly regulation", "regulatory", 2.5),
    ("2025-01-23", "BTC", "Trump signs executive order establishing strategic Bitcoin reserve", "regulatory", 3.0),
    ("2025-03-07", "BTC", "Trump signs executive order creating US Strategic Bitcoin Reserve from seized assets", "regulatory", 3.0),
    ("2025-02-21", "BTC", "Bybit exchange hacked for $1.5 billion in largest crypto theft ever", "hack", 3.0),
    ("2025-02-21", "ETH", "Ethereum drops on Bybit hack news as $1.5B in ETH stolen", "hack", 3.0),

    # ── SOL specific events ──
    ("2020-03-16", "SOL", "Solana mainnet beta launches on March 16 2020", "market", 2.0),
    ("2021-09-14", "SOL", "Solana network goes offline for 17 hours due to resource exhaustion", "hack", 2.5),
    ("2021-11-06", "SOL", "Solana reaches all-time high above $260", "market", 2.5),
    ("2022-08-03", "SOL", "Slope wallet hack drains approximately 8000 Solana wallets", "hack", 2.5),
    ("2022-11-09", "SOL", "Solana crashes 40% due to FTX collapse as SBF was major SOL backer", "market", 3.0),
    ("2022-12-29", "SOL", "Solana drops below $10 amid FTX bankruptcy contagion fears", "market", 2.5),
    ("2023-10-20", "SOL", "Solana rallies past $30 on renewed DeFi and memecoin activity", "market", 2.0),
    ("2023-12-25", "SOL", "Solana surges past $100 driven by Jito airdrop and ecosystem growth", "market", 2.0),
    ("2024-03-18", "SOL", "Solana reaches $200 for first time since 2021 on memecoin frenzy", "market", 2.0),
    ("2024-11-22", "SOL", "Solana hits new all-time high above $260 on Trump pump and meme coins", "market", 2.5),
]


async def bootstrap():
    """Insert historical events and label with actual price outcomes."""
    from app.db.event_repo import event_repository as event_repo
    from app.core.rag_pipeline import build_chunk_text, event_id
    from app.core.impact_model import extract_severity

    inserted = 0
    for date_str, asset, title, source, severity in HISTORICAL_EVENTS:
        ts = datetime.strptime(date_str, "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc  # assume noon UTC
        )
        eid = event_id(f"historical_{source}", title, asset)
        chunk = build_chunk_text(title)

        success = event_repo.insert_event(
            id=eid,
            asset=asset,
            timestamp=ts,
            source=f"historical_{source}",
            title=title,
            chunk_text=chunk,
            tier="2",  # all historical events are Tier 2 (important by definition)
            nlp_score=None,  # will be scored if CryptoBERT available
            body_preview=None,
            metadata={"severity": severity, "curated": True},
        )
        if success:
            inserted += 1

    logger.info("inserted %d/%d historical events", inserted, len(HISTORICAL_EVENTS))

    # Now auto-label with actual price outcomes
    from scripts.rag_sentiment_pipeline import label_past_events
    labeled = await label_past_events()
    logger.info("labeled %d events with price outcomes", labeled)

    # Embed historical events (if embedding server available)
    from app.core.embedding_client import embedding_client
    is_healthy = await embedding_client.health_check()
    if is_healthy:
        # Get unembedded events
        store = event_repo._get_store()
        rows = store.fetch_all(
            "SELECT id, chunk_text FROM event_embeddings WHERE embedding IS NULL",
            {},
        )
        if rows:
            texts = [r["chunk_text"] for r in rows]
            ids = [r["id"] for r in rows]
            embeddings = await embedding_client.embed(texts)
            if embeddings is not None:
                for i, eid in enumerate(ids):
                    vec_str = "[" + ",".join(str(float(v)) for v in embeddings[i]) + "]"
                    store.execute(
                        "UPDATE event_embeddings SET embedding = CAST(:vec AS vector) WHERE id = :id",
                        {"vec": vec_str, "id": eid},
                    )
                logger.info("embedded %d historical events", len(ids))
    else:
        logger.warning("embedding server unavailable — events stored without vectors")
        logger.info("re-run this script when embedding server is ready to generate vectors")

    # Stats
    stats = event_repo.stats()
    print(f"\n=== Event Store Stats ===")
    print(f"Total events: {stats['total']}")
    print(f"Tier 2: {stats['tier2']}")
    print(f"With embeddings: {stats['has_embedding']}")
    print(f"Labeled (price outcome): {stats['labeled']}")
    print(f"Date range: {stats['earliest']} → {stats['latest']}")


if __name__ == "__main__":
    asyncio.run(bootstrap())
