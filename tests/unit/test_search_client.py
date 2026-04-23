"""Unit tests for ``bestseller.services.search_client``.

We do **not** hit real Tavily / Serper endpoints.  Instead we inject a
``MockTransport`` into ``httpx.AsyncClient`` so every network call is
intercepted in-process.  Tests cover:

* Noop client shape.
* Tavily + Serper response parsing.
* LRU cache hit/miss behaviour.
* Rate limiter basic back-pressure.
* Error envelope on HTTP failure.
* ``MultiSearchClient`` merge + dedup.
* ``build_search_client`` preference order.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bestseller.services.search_client import (
    MultiSearchClient,
    NoopSearchClient,
    SearchClientConfig,
    SearchHit,
    SearchResponse,
    SerperSearchClient,
    TavilySearchClient,
    _clamp_max_results,
    _LruAsyncCache,
    _RateLimiter,
    build_search_client,
)

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────────


def _tavily_payload(urls: list[str]) -> dict[str, object]:
    return {
        "results": [
            {
                "title": f"Title {i}",
                "url": url,
                "content": f"Snippet {i}",
                "score": 0.5 + 0.1 * i,
                "published_date": "2025-01-01",
            }
            for i, url in enumerate(urls)
        ]
    }


def _serper_payload(urls: list[str]) -> dict[str, object]:
    return {
        "organic": [
            {
                "title": f"Title {i}",
                "link": url,
                "snippet": f"Snippet {i}",
                "date": "2025-02-02",
            }
            for i, url in enumerate(urls)
        ]
    }


def _make_handler(mapping: dict[str, dict[str, object]]) -> httpx.MockTransport:
    """Return an ``httpx`` MockTransport routed by request URL."""

    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        payload = mapping.get(url)
        if payload is None:
            return httpx.Response(404, json={"error": "unrouted", "url": url})
        if payload.get("_raise_status"):
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(_handler)


# ── Noop ───────────────────────────────────────────────────────────────


class TestNoopSearchClient:
    async def test_returns_empty_hits(self) -> None:
        client = NoopSearchClient()
        resp = await client.search("anything")
        assert resp.provider == "noop"
        assert resp.hits == ()
        assert bool(resp) is False
        await client.close()


# ── Tavily ─────────────────────────────────────────────────────────────


class TestTavilyClient:
    @pytest.fixture
    def http(self) -> httpx.AsyncClient:
        transport = _make_handler(
            {
                TavilySearchClient._ENDPOINT: _tavily_payload(
                    ["https://a.test/1", "https://a.test/2"]
                ),
            }
        )
        return httpx.AsyncClient(transport=transport)

    async def test_parses_results(self, http: httpx.AsyncClient) -> None:
        client = TavilySearchClient("k", http_client=http)
        resp = await client.search("query", max_results=2)
        assert resp.provider == "tavily"
        assert resp.error is None
        assert [h.url for h in resp.hits] == ["https://a.test/1", "https://a.test/2"]
        assert resp.hits[0].snippet == "Snippet 0"
        assert resp.cached is False
        await client.close()

    async def test_lru_cache_hits_on_repeat(self, http: httpx.AsyncClient) -> None:
        client = TavilySearchClient("k", http_client=http)
        first = await client.search("q", max_results=2)
        second = await client.search("q", max_results=2)
        assert first.cached is False
        assert second.cached is True
        assert [h.url for h in first.hits] == [h.url for h in second.hits]
        await client.close()

    async def test_error_envelope_on_http_failure(self) -> None:
        transport = _make_handler(
            {TavilySearchClient._ENDPOINT: {"_raise_status": True}}
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = TavilySearchClient("k", http_client=http)
            resp = await client.search("q")
            assert resp.hits == ()
            assert resp.error is not None
            assert "HTTPStatusError" in resp.error or "500" in resp.error

    async def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            TavilySearchClient("")


# ── Serper ─────────────────────────────────────────────────────────────


class TestSerperClient:
    async def test_parses_results(self) -> None:
        transport = _make_handler(
            {
                SerperSearchClient._ENDPOINT: _serper_payload(
                    ["https://b.test/1", "https://b.test/2"]
                )
            }
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = SerperSearchClient("k", http_client=http)
            resp = await client.search("q", max_results=2)
            assert resp.provider == "serper"
            assert [h.url for h in resp.hits] == [
                "https://b.test/1",
                "https://b.test/2",
            ]
            assert resp.hits[0].source == "serper"

    async def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ValueError):
            SerperSearchClient("")


# ── Primitives ─────────────────────────────────────────────────────────


class TestLruCache:
    def test_zero_capacity_never_caches(self) -> None:
        cache = _LruAsyncCache(0)
        resp = SearchResponse(
            query="q",
            hits=(SearchHit(title="t", url="u", snippet="s"),),
            provider="x",
        )
        cache.put("q", 1, resp)
        assert cache.get("q", 1) is None

    def test_mru_eviction(self) -> None:
        cache = _LruAsyncCache(2)
        hits = (SearchHit(title="t", url="u", snippet="s"),)
        for i in range(3):
            cache.put(f"q{i}", 1, SearchResponse(query=f"q{i}", hits=hits, provider="x"))
        # q0 evicted
        assert cache.get("q0", 1) is None
        assert cache.get("q1", 1) is not None
        assert cache.get("q2", 1) is not None

    def test_errors_are_not_cached(self) -> None:
        cache = _LruAsyncCache(4)
        cache.put(
            "q",
            1,
            SearchResponse(query="q", hits=(), provider="x", error="boom"),
        )
        assert cache.get("q", 1) is None


class TestRateLimiter:
    async def test_first_calls_do_not_block(self) -> None:
        limiter = _RateLimiter(max_calls=3, period_seconds=5.0)
        loop = asyncio.get_running_loop()
        start = loop.time()
        for _ in range(3):
            await limiter.acquire()
        elapsed = loop.time() - start
        assert elapsed < 0.1

    async def test_limit_triggers_sleep(self) -> None:
        # max 2 calls / 0.2s; 3rd must wait at least ~0.2s.
        limiter = _RateLimiter(max_calls=2, period_seconds=0.2)
        loop = asyncio.get_running_loop()
        await limiter.acquire()
        await limiter.acquire()
        start = loop.time()
        await limiter.acquire()
        elapsed = loop.time() - start
        assert elapsed >= 0.1  # conservative lower bound for CI jitter


class TestClamp:
    def test_clamps_within_range(self) -> None:
        assert _clamp_max_results(0) == 1
        assert _clamp_max_results(100) == 20
        assert _clamp_max_results(7) == 7


# ── Multi ──────────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, provider: str, hits: list[SearchHit]) -> None:
        self.provider = provider
        self._hits = tuple(hits)
        self.closed = False

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> SearchResponse:
        cap = max_results or 10
        return SearchResponse(
            query=query, hits=self._hits[:cap], provider=self.provider
        )

    async def close(self) -> None:
        self.closed = True


class TestMultiSearchClient:
    async def test_interleaves_and_dedups_by_url(self) -> None:
        a = _FakeClient(
            "a",
            [
                SearchHit(title="a1", url="https://u1", snippet=""),
                SearchHit(title="a2", url="https://u2", snippet=""),
            ],
        )
        b = _FakeClient(
            "b",
            [
                SearchHit(title="b1", url="https://u1", snippet=""),  # dup
                SearchHit(title="b2", url="https://u3", snippet=""),
            ],
        )
        multi = MultiSearchClient(clients=[a, b])
        resp = await multi.search("q", max_results=4)
        urls = [h.url for h in resp.hits]
        assert urls == ["https://u1", "https://u2", "https://u3"]

    async def test_close_propagates(self) -> None:
        a = _FakeClient("a", [])
        b = _FakeClient("b", [])
        multi = MultiSearchClient(clients=[a, b])
        await multi.close()
        assert a.closed and b.closed

    async def test_empty_clients_returns_empty(self) -> None:
        multi = MultiSearchClient(clients=[])
        resp = await multi.search("q")
        assert resp.hits == ()


# ── Factory ────────────────────────────────────────────────────────────


class TestBuildSearchClient:
    def test_no_keys_returns_noop(self) -> None:
        client = build_search_client(env={})
        assert isinstance(client, NoopSearchClient)

    def test_tavily_only(self) -> None:
        client = build_search_client(env={"TAVILY_API_KEY": "abc"})
        assert isinstance(client, TavilySearchClient)

    def test_serper_only(self) -> None:
        client = build_search_client(env={"SERPER_API_KEY": "abc"})
        assert isinstance(client, SerperSearchClient)

    def test_both_keys_returns_multi(self) -> None:
        client = build_search_client(
            env={"TAVILY_API_KEY": "a", "SERPER_API_KEY": "b"}
        )
        assert isinstance(client, MultiSearchClient)
        providers = [c.provider for c in client.clients]
        assert "tavily" in providers and "serper" in providers
