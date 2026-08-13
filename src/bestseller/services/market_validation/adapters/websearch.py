"""Web-search dedup adapter: title occupancy lookup via search_client.

Reuses the framework search abstraction (Tavily/Serper with Noop fallback);
without API keys this degrades to "no web evidence" instead of failing.
"""


from __future__ import annotations

import logging

from bestseller.services.market_validation.analyzer import normalize_title
from bestseller.services.search_client import WebSearchClient

logger = logging.getLogger(__name__)


async def search_title_occupancy(
    client: WebSearchClient,
    *,
    title: str,
    site_filters: tuple[str, ...],
) -> list[str]:
    """Return human-readable web hits suggesting the title is already taken.

    A hit counts only when the normalized candidate appears in the result
    title or snippet — plain topic overlap is not occupancy evidence.
    """

    candidate = (title or "").strip()
    if not candidate or getattr(client, "provider", "noop") == "noop":
        return []
    normalized = normalize_title(candidate)
    hits: list[str] = []
    for site in site_filters or ("",):
        query = f'"{candidate}" site:{site}' if site else f'"{candidate}" 小说'
        try:
            response = await client.search(query)
        except Exception:
            logger.debug("Web search failed for %r", query, exc_info=True)
            continue
        for hit in getattr(response, "hits", []) or []:
            haystack = normalize_title(f"{hit.title} {hit.snippet}")
            if normalized and normalized in haystack:
                hits.append(f"{hit.title} — {hit.url}")
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique = [hit for hit in hits if not (hit in seen or seen.add(hit))]
    return unique[:10]
