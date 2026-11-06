"""Fast, dynamic morning news digest via Google News RSS with recency filtering and deduplication."""

from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from deskpilot.storage.store import SQLiteStore


class NewsHeadline(BaseModel):
    """Structured representation of a single morning news item."""

    topic: str = "General"
    title: str
    link: str
    published: str = ""
    source: str = ""


class NewsDigestResult(BaseModel):
    """Aggregate result from fetching top morning headlines."""

    headlines: list[NewsHeadline] = Field(default_factory=list)
    error: str | None = None


# Curated topics using official Google topic feeds + fresh search queries (when:2d/when:3d)
CURATED_TOPIC_CONFIGS: list[tuple[str, str, str | None]] = [
    (
        "Technology",
        "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-MY&gl=MY&ceid=MY:en",
        None,
    ),
    (
        "Malaysia",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=en-MY&gl=MY&ceid=MY:en",
        None,
    ),
    (
        "MY Concerts",
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote('concert Malaysia OR "Kuala Lumpur" (K-pop OR music OR tour OR artist) when:3d')
        + "&hl=en-MY&gl=MY&ceid=MY:en",
        'concert Malaysia OR "Kuala Lumpur" (K-pop OR music OR tour)',  # fallback without when:3d
    ),
    (
        "K-Pop & Music",
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote('(kpop OR "new album" OR comeback OR "music video") when:2d')
        + "&hl=en-MY&gl=MY&ceid=MY:en",
        'kpop OR "new album" music artist release',
    ),
    (
        "Art & Culture",
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote('(art exhibition OR "contemporary art" OR "design exhibition") when:3d')
        + "&hl=en-MY&gl=MY&ceid=MY:en",
        'art exhibition contemporary design gallery',
    ),
]


def _hash_title(title: str) -> str:
    """Create normalized hash of headline title for deduplication."""
    clean = "".join(ch for ch in title.lower() if ch.isalnum() or ch.isspace()).strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]


def get_seen_titles(store: SQLiteStore | None) -> set[str]:
    """Retrieve set of previously shown article hashes from SQLite store."""
    if store is None:
        return set()
    try:
        item = store.get(("news", "history"), "seen_hashes")
        if item is not None and isinstance(item.value, dict):
            # Prune entries older than 5 days
            now = time.time()
            cutoff = now - (5 * 86400)
            valid_hashes = {
                h
                for h, ts in item.value.get("hashes", {}).items()
                if isinstance(ts, (int, float)) and ts > cutoff
            }
            return valid_hashes
    except Exception:
        pass
    return set()


def record_seen_titles(store: SQLiteStore | None, new_hashes: set[str]) -> None:
    """Record newly shown article hashes in SQLite store."""
    if store is None or not new_hashes:
        return
    try:
        current_item = store.get(("news", "history"), "seen_hashes")
        existing: dict[str, float] = {}
        now = time.time()
        cutoff = now - (5 * 86400)

        if current_item is not None and isinstance(current_item.value, dict):
            existing = {
                h: ts
                for h, ts in current_item.value.get("hashes", {}).items()
                if isinstance(ts, (int, float)) and ts > cutoff
            }
        for h in new_hashes:
            existing[h] = now

        store.put(("news", "history"), "seen_hashes", {"hashes": existing})
    except Exception:
        pass


def _fetch_feed_items_sync(
    topic_name: str,
    url: str,
    timeout_seconds: float = 3.0,
    max_candidates: int = 6,
) -> list[NewsHeadline]:
    """Fetch top candidate headlines from a Google News RSS feed URL."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DeskPilot/1.0 (Windows NT 10.0; Win64; x64)"},
    )
    candidates: list[NewsHeadline] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            tree = ET.fromstring(response.read())
            items = tree.findall(".//item")[:max_candidates]

            for item in items:
                title_elem = item.find("title")
                link_elem = item.find("link")
                pub_elem = item.find("pubDate")
                source_elem = item.find("source")

                title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
                link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
                pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""
                source = source_elem.text.strip() if source_elem is not None and source_elem.text else ""

                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    if not source:
                        source = parts[1].strip()

                if title:
                    candidates.append(
                        NewsHeadline(
                            topic=topic_name,
                            title=title,
                            link=link,
                            published=pub_date,
                            source=source,
                        )
                    )
    except Exception:
        pass
    return candidates


def _fetch_topic_with_fallback_sync(
    topic_name: str,
    primary_url: str,
    fallback_query: str | None = None,
    timeout_seconds: float = 3.0,
) -> list[NewsHeadline]:
    """Fetch candidates from primary URL; if empty and fallback query provided, try fallback."""
    items = _fetch_feed_items_sync(topic_name, primary_url, timeout_seconds=timeout_seconds)
    if not items and fallback_query:
        fallback_url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(fallback_query)
            + "&hl=en-MY&gl=MY&ceid=MY:en"
        )
        items = _fetch_feed_items_sync(topic_name, fallback_url, timeout_seconds=timeout_seconds)
    return items


async def fetch_morning_news(
    topic_configs: list[tuple[str, str, str | None]] | None = None,
    store: SQLiteStore | None = None,
    timeout_seconds: float = 3.5,
    enabled: bool = True,
) -> NewsDigestResult:
    """Asynchronously fetch fresh, non-duplicate headlines across curated topics in parallel."""
    if not enabled:
        return NewsDigestResult(headlines=[])

    configs = topic_configs or CURATED_TOPIC_CONFIGS

    # Fetch candidate lists in parallel threads
    tasks = [
        asyncio.to_thread(_fetch_topic_with_fallback_sync, label, url, fallback, timeout_seconds)
        for label, url, fallback in configs
    ]
    topic_candidates_list = await asyncio.gather(*tasks, return_exceptions=True)

    seen_hashes = get_seen_titles(store)
    chosen_headlines: list[NewsHeadline] = []
    new_seen: set[str] = set()

    for candidates in topic_candidates_list:
        if not isinstance(candidates, list) or not candidates:
            continue

        # Find the first candidate that hasn't been seen in the last 5 days
        chosen = None
        for candidate in candidates:
            h = _hash_title(candidate.title)
            if h not in seen_hashes:
                chosen = candidate
                new_seen.add(h)
                break

        # If all candidates in this batch were seen, take the newest candidate as fallback
        if chosen is None and candidates:
            chosen = candidates[0]
            new_seen.add(_hash_title(chosen.title))

        if chosen is not None:
            chosen_headlines.append(chosen)

    # Persist chosen headlines to store so tomorrow gets different stories!
    if store is not None and new_seen:
        record_seen_titles(store, new_seen)

    return NewsDigestResult(headlines=chosen_headlines)
