"""Embedding client for external OpenAI-compatible embedding server.

Server: 113.198.66.77:13195
API: POST /v1/embeddings (OpenAI format)
Model: Qwen/Qwen3-Embedding-0.6B (1024d)

The embedding server is managed externally. This client handles:
  - Batch embedding requests with auth
  - Retry with backoff on failure
  - Graceful fallback when server is unavailable
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict

import httpx
import numpy as np

logger = logging.getLogger("embedding-client")

EMBEDDING_BASE_URL = os.getenv(
    "EMBEDDING_BASE_URL", "http://113.198.66.77:13195"
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")  # auto-detect from /v1/models
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
_TIMEOUT = 30  # seconds per request
_MAX_RETRIES = 3
_BATCH_SIZE = 32  # items per API call
_BACKOFF_BASE = 2  # exponential backoff: 2s, 4s, 8s


class _EmbeddingCache:
    """LRU cache for text→embedding mappings, keyed by SHA256 hash."""

    def __init__(self, max_size: int = 2000):
        self._max_size = max_size
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def get(self, text: str) -> list[float] | None:
        k = self._key(text)
        if k in self._cache:
            self._cache.move_to_end(k)
            self._hits += 1
            return self._cache[k]
        self._misses += 1
        return None

    def put(self, text: str, embedding: list[float]) -> None:
        k = self._key(text)
        self._cache[k] = embedding
        self._cache.move_to_end(k)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1), 3),
        }


class EmbeddingClient:
    """Client for the external embedding server."""

    def __init__(
        self,
        base_url: str = EMBEDDING_BASE_URL,
        api_key: str = EMBEDDING_API_KEY,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model: str = EMBEDDING_MODEL
        self._dim: int | None = None
        self._available: bool | None = None
        self._cache = _EmbeddingCache()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=_TIMEOUT,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def health_check(self) -> bool:
        """Check if embedding server is alive and model is loaded."""
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/health",
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                loaded = data.get("model_loaded", False)
                if loaded:
                    dim = data.get("dimensions")
                    if dim:
                        self._dim = dim
                    logger.info(
                        "embedding server ready: model=%s dim=%s",
                        data.get("model", "?"),
                        dim,
                    )
                else:
                    logger.info("embedding server alive but model still loading")
                self._available = loaded
                return loaded
            self._available = False
            return False
        except Exception as e:
            logger.warning("embedding server unavailable: %s", str(e)[:100])
            self._available = False
            return False

    async def detect_model(self) -> str | None:
        """Auto-detect model name from /v1/models."""
        if self._model:
            return self._model
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                if models:
                    self._model = models[0].get("id", "")
                    logger.info("detected embedding model: %s", self._model)
                    return self._model
        except Exception as e:
            logger.warning("model detection failed: %s", str(e)[:80])
        return None

    async def embed(self, texts: str | list[str]) -> np.ndarray | None:
        """Embed one or more texts. Returns (N, dim) array or None on failure.

        Uses an in-memory LRU hash cache to avoid re-embedding identical texts.
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return None

        if not self._model:
            await self.detect_model()

        # Check cache for each text
        results: list[list[float] | None] = []
        texts_to_embed: list[str] = []
        miss_indices: list[int] = []

        for idx, text in enumerate(texts):
            cached = self._cache.get(text)
            if cached is not None:
                results.append(cached)
            else:
                results.append(None)
                texts_to_embed.append(text)
                miss_indices.append(idx)

        # Embed cache misses via API
        if texts_to_embed:
            fresh_embeddings: list[list[float]] = []
            for i in range(0, len(texts_to_embed), _BATCH_SIZE):
                batch = texts_to_embed[i : i + _BATCH_SIZE]
                result = await self._embed_batch(batch)
                if result is None:
                    return None
                fresh_embeddings.extend(result)

            # Store fresh embeddings in cache and fill results
            for j, miss_idx in enumerate(miss_indices):
                emb = fresh_embeddings[j]
                self._cache.put(texts_to_embed[j], emb)
                results[miss_idx] = emb

        arr = np.array(results, dtype=np.float32)
        if self._dim is None:
            self._dim = arr.shape[1]
            logger.info("embedding dimension: %d", self._dim)
        return arr

    async def _embed_batch(
        self, texts: list[str]
    ) -> list[list[float]] | None:
        """Send a single batch to the embedding server."""
        payload = {
            "input": texts,
            "model": self._model or "default",
            "encoding_format": "float",
        }

        import asyncio

        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                resp = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json=payload,
                    headers=self._headers(),
                    timeout=_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    data.sort(key=lambda x: x.get("index", 0))
                    self._available = True
                    return [d["embedding"] for d in data]
                elif resp.status_code == 429:
                    # Rate limited — longer backoff
                    wait = _BACKOFF_BASE ** (attempt + 2)
                    logger.warning("rate limited (429), waiting %ds", wait)
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code == 401:
                    logger.error("embedding auth failed (401) — check EMBEDDING_API_KEY")
                    break  # no retry on auth errors
                else:
                    logger.warning(
                        "embedding API %d: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
            except Exception as e:
                logger.warning(
                    "embedding request failed (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_RETRIES + 1,
                    str(e)[:100],
                )

            if attempt < _MAX_RETRIES:
                wait = _BACKOFF_BASE ** (attempt + 1)  # 2s, 4s, 8s
                await asyncio.sleep(wait)

        self._available = False
        return None

    @property
    def is_available(self) -> bool:
        return self._available is True

    @property
    def dimension(self) -> int | None:
        return self._dim

    @property
    def cache_stats(self) -> dict:
        return self._cache.stats

    async def close(self) -> None:
        """Close the persistent HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# Singleton
embedding_client = EmbeddingClient()
