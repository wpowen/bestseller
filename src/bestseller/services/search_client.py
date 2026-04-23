"""Web-search client abstractions for the Research Agent + Library Curator.

The material-library refactor (plan ``twinkly-rolling-pnueli.md``) needs a
"research" channel that can pull facts from the open web — taxonomies of
cultivation ranks, real-world references for urban-fantasy settings,
authoritative Wikipedia entries, etc.  The MCP bridge (see
:mod:`bestseller.services.mcp_bridge`) covers MCP-hosted data sources;
this module covers plain HTTP search APIs (Tavily, Serper) and provides
a no-op implementation so the rest of the pipeline always has a working
client.

Design
------

* ``WebSearchClient`` is a Protocol — the Research Agent / Curator only
  depend on the shape.  Swapping providers is just wiring.
* Every concrete client:
    * validates its API key at construction time (``NoopSearchClient``
      as a graceful fallback when the key is missing),
    * respects a per-invocation ``max_results`` (capped server-side +
      truncated client-side for safety),
    * applies an in-process LRU cache keyed by ``(query, max_results)``
      to absorb duplicate calls from multiple skills that all ask for
      the same seed query,
    * enforces a lightweight token-bucket rate limit to protect the
      Tavily/Serper free tiers.
* **Failure mode**: any network / HTTP / JSON decode error is caught
  and surfaced as an empty result list with ``error`` set on the
  :class:`SearchResponse`.  The Research Agent keeps running — gating
  the *whole* run on a flaky search endpoint would block the library
  from ever filling.

Cache & rate-limit tuning lives in :class:`SearchClientConfig` and
defaults are intentionally conservative so the free tiers last.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, Sequence

import httpx

logger = logging.getLogger(__name__)


# ── DTOs ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SearchHit:
    """One result item returned by a ``WebSearchClient``."""

    title: str
    url: str
    snippet: str
    source: str = ""
    published_at: str | None = None
    score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResponse:
    """Top-level container — zero or more :class:`SearchHit` plus meta."""

    query: str
    hits: tuple[SearchHit, ...]
    provider: str
    cached: bool = False
    error: str | None = None
    elapsed_ms: int | None = None

    def __bool__(self) -> bool:
        return bool(self.hits)


@dataclass
class SearchClientConfig:
    """Per-client tuning knobs.

    Any value can be overridden per-request (for example a curator doing
    deep research may temporarily raise ``max_results``), but these are
    the defaults.
    """

    max_results: int = 5
    timeout_seconds: float = 20.0
    cache_size: int = 256
    rate_limit_per_minute: int = 30


# ── Protocol ────────────────────────────────────────────────────────────


class WebSearchClient(Protocol):
    """Shape the Research Agent / Curator depend on."""

    provider: str

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse: ...

    async def close(self) -> None: ...


# ── Shared cache + rate limiter ─────────────────────────────────────────


class _LruAsyncCache:
    """Thread-unsafe (asyncio-safe) LRU keyed by a hash of ``query``."""

    def __init__(self, capacity: int) -> None:
        self._capacity = max(capacity, 0)
        self._items: OrderedDict[str, SearchResponse] = OrderedDict()

    def _key(self, query: str, max_results: int) -> str:
        base = f"{query}::{max_results}".encode("utf-8")
        return hashlib.blake2b(base, digest_size=20).hexdigest()

    def get(self, query: str, max_results: int) -> SearchResponse | None:
        if self._capacity == 0:
            return None
        key = self._key(query, max_results)
        if key not in self._items:
            return None
        # bump to MRU
        value = self._items.pop(key)
        self._items[key] = value
        # mark copy as cached so callers can tell
        return SearchResponse(
            query=value.query,
            hits=value.hits,
            provider=value.provider,
            cached=True,
            error=value.error,
            elapsed_ms=value.elapsed_ms,
        )

    def put(self, query: str, max_results: int, response: SearchResponse) -> None:
        if self._capacity == 0 or response.error is not None:
            return
        key = self._key(query, max_results)
        if key in self._items:
            self._items.pop(key)
        elif len(self._items) >= self._capacity:
            self._items.popitem(last=False)
        self._items[key] = response


class _RateLimiter:
    """Token-bucket style limiter — sleeps until under budget.

    Uses a monotonic deque of timestamps and trims entries older than the
    ``period`` window.  Simple, single-process, good enough for CLI + a
    small worker fleet.  If you need cross-process limiting, wrap this
    with a redis-backed implementation.
    """

    def __init__(self, max_calls: int, period_seconds: float = 60.0) -> None:
        self._max = max(max_calls, 1)
        self._period = period_seconds
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # drop anything older than the period
            cutoff = now - self._period
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            if len(self._events) >= self._max:
                sleep_for = self._period - (now - self._events[0])
                if sleep_for > 0:
                    logger.debug(
                        "search rate-limit hit; sleeping %.2fs", sleep_for
                    )
                    await asyncio.sleep(sleep_for)
                now = time.monotonic()
                cutoff = now - self._period
                while self._events and self._events[0] < cutoff:
                    self._events.popleft()
            self._events.append(now)


# ── Noop implementation ────────────────────────────────────────────────


class NoopSearchClient:
    """Always returns zero hits — used when no API key is configured.

    The Research Agent still runs end-to-end (just emits library entries
    derived purely from LLM-synth + pgvector recall).  This keeps the
    Curator / CLI usable in local development without any API keys.
    """

    provider: str = "noop"

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse:
        return SearchResponse(query=query, hits=(), provider=self.provider)

    async def close(self) -> None:
        return None


# ── Tavily ──────────────────────────────────────────────────────────────


class TavilySearchClient:
    """Thin async wrapper around the Tavily REST API.

    Tavily exposes a POST endpoint that takes a JSON body with the query
    + tuning knobs.  We keep the payload minimal so the client stays
    robust across their schema revisions.
    """

    provider: str = "tavily"
    _ENDPOINT = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str,
        *,
        config: SearchClientConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("TavilySearchClient requires a non-empty api_key")
        self._api_key = api_key
        self._config = config or SearchClientConfig()
        self._cache = _LruAsyncCache(self._config.cache_size)
        self._rate_limiter = _RateLimiter(self._config.rate_limit_per_minute)
        self._http = http_client or httpx.AsyncClient(
            timeout=self._config.timeout_seconds
        )
        self._owns_http = http_client is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse:
        limit = _clamp_max_results(max_results or self._config.max_results)
        cached = self._cache.get(query, limit)
        if cached is not None:
            return cached
        await self._rate_limiter.acquire()
        t0 = time.monotonic()
        try:
            resp = await self._http.post(
                self._ENDPOINT,
                json={
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                    "include_answer": False,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001  — defensive envelope
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("Tavily search failed: %s", exc)
            return SearchResponse(
                query=query,
                hits=(),
                provider=self.provider,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed_ms,
            )
        hits = tuple(
            _tavily_result_to_hit(item)
            for item in (payload.get("results") or [])[:limit]
        )
        response = SearchResponse(
            query=query,
            hits=hits,
            provider=self.provider,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
        self._cache.put(query, limit, response)
        return response


def _tavily_result_to_hit(item: dict[str, Any]) -> SearchHit:
    return SearchHit(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        source="tavily",
        published_at=item.get("published_date"),
        score=_coerce_float(item.get("score")),
        extra={"raw": item},
    )


# ── Serper ──────────────────────────────────────────────────────────────


class SerperSearchClient:
    """serper.dev REST wrapper — Google-style results at a flat rate."""

    provider: str = "serper"
    _ENDPOINT = "https://google.serper.dev/search"

    def __init__(
        self,
        api_key: str,
        *,
        config: SearchClientConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SerperSearchClient requires a non-empty api_key")
        self._api_key = api_key
        self._config = config or SearchClientConfig()
        self._cache = _LruAsyncCache(self._config.cache_size)
        self._rate_limiter = _RateLimiter(self._config.rate_limit_per_minute)
        self._http = http_client or httpx.AsyncClient(
            timeout=self._config.timeout_seconds
        )
        self._owns_http = http_client is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse:
        limit = _clamp_max_results(max_results or self._config.max_results)
        cached = self._cache.get(query, limit)
        if cached is not None:
            return cached
        await self._rate_limiter.acquire()
        t0 = time.monotonic()
        try:
            resp = await self._http.post(
                self._ENDPOINT,
                headers={
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": limit},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("Serper search failed: %s", exc)
            return SearchResponse(
                query=query,
                hits=(),
                provider=self.provider,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=elapsed_ms,
            )
        organic: Iterable[dict[str, Any]] = payload.get("organic") or []
        hits = tuple(_serper_result_to_hit(item) for item in list(organic)[:limit])
        response = SearchResponse(
            query=query,
            hits=hits,
            provider=self.provider,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )
        self._cache.put(query, limit, response)
        return response


def _serper_result_to_hit(item: dict[str, Any]) -> SearchHit:
    return SearchHit(
        title=str(item.get("title") or ""),
        url=str(item.get("link") or ""),
        snippet=str(item.get("snippet") or ""),
        source="serper",
        published_at=item.get("date"),
        score=None,
        extra={"raw": item},
    )


# ── Multi-client dispatcher (optional) ─────────────────────────────────


@dataclass
class MultiSearchClient:
    """Run every underlying client in parallel and merge top-k hits.

    Useful when multiple providers are configured — e.g. Tavily *and*
    Serper — so a miss on one is compensated by the other.  Ordering
    preserves per-client rank (interleaved round-robin); duplicates by
    URL are dropped keeping the earliest occurrence.
    """

    clients: Sequence[WebSearchClient]
    provider: str = "multi"

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse:
        if not self.clients:
            return SearchResponse(query=query, hits=(), provider="multi")
        limit = _clamp_max_results(max_results or 5)
        responses = await asyncio.gather(
            *(c.search(query, max_results=limit) for c in self.clients),
            return_exceptions=True,
        )
        merged: list[SearchHit] = []
        seen_urls: set[str] = set()
        # Round-robin through providers so a single fast-but-low-quality
        # client can't dominate the final ranking.
        max_depth = max(
            (
                len(r.hits)
                for r in responses
                if isinstance(r, SearchResponse)
            ),
            default=0,
        )
        for depth in range(max_depth):
            for resp in responses:
                if not isinstance(resp, SearchResponse):
                    continue
                if depth >= len(resp.hits):
                    continue
                hit = resp.hits[depth]
                if not hit.url or hit.url in seen_urls:
                    continue
                seen_urls.add(hit.url)
                merged.append(hit)
                if len(merged) >= limit:
                    break
            if len(merged) >= limit:
                break
        return SearchResponse(
            query=query,
            hits=tuple(merged),
            provider=self.provider,
        )

    async def close(self) -> None:
        for client in self.clients:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("close() on %s raised: %s", client.provider, exc)


# ── Factory ────────────────────────────────────────────────────────────


def build_search_client(
    *,
    env: dict[str, str] | None = None,
    config: SearchClientConfig | None = None,
) -> WebSearchClient:
    """Build the best client we can given the current environment.

    Preference order:
    1. ``TAVILY_API_KEY`` → :class:`TavilySearchClient`
    2. ``SERPER_API_KEY`` → :class:`SerperSearchClient`
    3. Otherwise ``NoopSearchClient`` (so callers never crash).

    When *both* Tavily and Serper keys are present a
    :class:`MultiSearchClient` fan-out wraps them — higher recall is
    worth the extra quota consumption during a Curator fill-gap run.
    """

    env = env if env is not None else dict(os.environ)
    config = config or SearchClientConfig()
    tavily_key = (env.get("TAVILY_API_KEY") or "").strip()
    serper_key = (env.get("SERPER_API_KEY") or "").strip()
    clients: list[WebSearchClient] = []
    if tavily_key:
        clients.append(TavilySearchClient(tavily_key, config=config))
    if serper_key:
        clients.append(SerperSearchClient(serper_key, config=config))
    if not clients:
        logger.info("No search API keys set; using NoopSearchClient")
        return NoopSearchClient()
    if len(clients) == 1:
        return clients[0]
    return MultiSearchClient(clients=clients)


# ── Helpers ─────────────────────────────────────────────────────────────


def _clamp_max_results(value: int, *, lower: int = 1, upper: int = 20) -> int:
    return max(lower, min(upper, int(value)))


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "SearchHit",
    "SearchResponse",
    "SearchClientConfig",
    "WebSearchClient",
    "NoopSearchClient",
    "TavilySearchClient",
    "SerperSearchClient",
    "MultiSearchClient",
    "build_search_client",
]
