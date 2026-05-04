"""NLP sentiment scorer — CryptoBERT on CPU.

Wraps ElKulako/cryptobert for crypto-specific sentiment analysis.
Outputs continuous [-1, +1] scores via softmax weighting:
  score = P(bullish) * 1.0 + P(neutral) * 0.0 + P(bearish) * (-1.0)

Falls back to keyword scoring if model fails to load (e.g., no torch).
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("sentiment-scorer")

_MODEL_ID = "ElKulako/cryptobert"
_FALLBACK_MODEL_ID = "distilbert/distilbert-base-uncased-finetuned-sst-2-english"

# Keyword fallback (same as sentiment_collector.py)
_BULL = [
    "rally", "surge", "bull", "breakout", "adoption", "partnership",
    "approval", "etf", "institutional", "record", "all-time high",
    "accumulation", "upgrade", "buy", "long", "positive", "bullish",
    "moon", "pump", "hodl", "launch", "grow",
]
_BEAR = [
    "crash", "dump", "bear", "hack", "ban", "fraud", "lawsuit",
    "regulation", "sell-off", "liquidation", "fud", "fear",
    "collapse", "scam", "sell", "short", "negative", "bearish",
    "rug", "exploit", "vulnerability", "delay", "reject",
]


def _keyword_score(text: str) -> dict:
    lower = text.lower()
    bull = sum(1 for k in _BULL if k in lower)
    bear = sum(1 for k in _BEAR if k in lower)
    total = bull + bear
    score = (bull - bear) / total if total else 0.0
    return {"score": round(score, 4), "model": "keyword", "confidence": 0.3}


class SentimentScorer:
    """Lazy-loaded NLP sentiment scorer with keyword fallback."""

    def __init__(self) -> None:
        self._pipeline = None
        self._model_name = "keyword"
        self._loaded = False
        self._load_attempted = False

    def _try_load(self) -> bool:
        """Attempt to load CryptoBERT. Returns True on success."""
        if self._load_attempted:
            return self._loaded
        self._load_attempted = True

        try:
            from transformers import pipeline as hf_pipeline
            import torch

            logger.info("loading %s (CPU)...", _MODEL_ID)
            t0 = time.time()

            self._pipeline = hf_pipeline(
                "text-classification",
                model=_MODEL_ID,
                tokenizer=_MODEL_ID,
                device=-1,  # CPU
                top_k=None,  # return all classes with probabilities
                truncation=True,
                max_length=128,
            )
            self._model_name = "cryptobert"
            self._loaded = True
            logger.info("loaded %s in %.1fs", _MODEL_ID, time.time() - t0)
            return True

        except Exception as e:
            logger.warning("cryptobert load failed: %s — trying fallback", str(e)[:100])

        # Fallback to DistilBERT
        try:
            from transformers import pipeline as hf_pipeline

            self._pipeline = hf_pipeline(
                "sentiment-analysis",
                model=_FALLBACK_MODEL_ID,
                device=-1,
                truncation=True,
                max_length=128,
            )
            self._model_name = "distilbert_sst2"
            self._loaded = True
            logger.info("loaded fallback model: %s", _FALLBACK_MODEL_ID)
            return True

        except Exception as e:
            logger.warning("all NLP models failed: %s — using keyword only", str(e)[:100])
            return False

    def score(self, text: str) -> dict[str, Any]:
        """Score a text. Returns {score: float, model: str, confidence: float}.

        score is in [-1, +1]:
          +1 = strongly bullish
           0 = neutral
          -1 = strongly bearish
        """
        if not text or not text.strip():
            return {"score": 0.0, "model": "empty", "confidence": 0.0}

        # Try NLP
        if not self._loaded:
            self._try_load()

        if self._pipeline is None:
            return _keyword_score(text)

        try:
            result = self._pipeline(text[:512])

            if self._model_name == "cryptobert":
                # CryptoBERT returns list of {label, score} dicts
                # Labels: Bearish, Neutral, Bullish
                probs = {r["label"].lower(): r["score"] for r in result[0]}
                bullish = probs.get("bullish", 0.0)
                neutral = probs.get("neutral", 0.0)
                bearish = probs.get("bearish", 0.0)
                score = bullish * 1.0 + neutral * 0.0 + bearish * (-1.0)
                confidence = max(bullish, neutral, bearish)
                return {
                    "score": round(score, 4),
                    "model": "cryptobert",
                    "confidence": round(confidence, 4),
                }

            elif self._model_name == "distilbert_sst2":
                # DistilBERT-SST2 returns {label: POSITIVE/NEGATIVE, score: float}
                label = result[0]["label"]
                prob = result[0]["score"]
                score = prob if label == "POSITIVE" else -prob
                return {
                    "score": round(score, 4),
                    "model": "distilbert_sst2",
                    "confidence": round(prob, 4),
                }

        except Exception as e:
            logger.debug("nlp scoring failed: %s", str(e)[:100])

        return _keyword_score(text)

    def score_batch(self, texts: list[str]) -> list[dict[str, Any]]:
        """Score multiple texts efficiently."""
        return [self.score(t) for t in texts]

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_nlp_ready(self) -> bool:
        if not self._load_attempted:
            self._try_load()
        return self._loaded


# Singleton
sentiment_scorer = SentimentScorer()
