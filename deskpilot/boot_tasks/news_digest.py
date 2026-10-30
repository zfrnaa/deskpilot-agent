"""Fast morning news digest boot task via Google News RSS across curated topics."""

from __future__ import annotations

import asyncio
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pydantic import BaseModel, Field


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


# Curated topics tailored to personal interests:
# 1. Technology breakthroughs and developments
# 2. Malaysia national & local headlines
# 3. Malaysia Live Concerts & Tours (K-Pop & international acts in KL/Malaysia)
# 4. K-Pop & Music releases (new albums and artist updates)
# 5. Art & Contemporary Design exhibitions
CURATED_TOPICS: list[tuple[str, str]] = [
    ("Technology", "technology"),
    ("Malaysia", "Malaysia"),
    ("MY Concerts", 'concert Malaysia OR "Kuala Lumpur" (K-pop OR music OR tour OR artist)'),
    ("K-Pop & Music", 'kpop OR "new album" music artist release'),
    ("Art", "art exhibition contemporary design"),
]


def _fetch_single_topic_sync(
    topic_name: str,
    query: str,
    timeout_seconds: float = 3.0,
) -> NewsHeadline | None:
    """Fetch the top single headline for a search query."""
    encoded_q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=en-MY&gl=MY&ceid=MY:en"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DeskPilot/1.0 (Windows NT 10.0; Win64; x64)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw_data = response.read()
            # Decode using utf-8 with replacement for clean text
            tree = ET.fromstring(raw_data)
            first_item = tree.find(".//item")
            if first_item is None:
                return None

            title_elem = first_item.find("title")
            link_elem = first_item.find("link")
            pub_elem = first_item.find("pubDate")
            source_elem = first_item.find("source")

            title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
            link = link_elem.text.strip() if link_elem is not None and link_elem.text else ""
            pub_date = pub_elem.text.strip() if pub_elem is not None and pub_elem.text else ""
            source = source_elem.text.strip() if source_elem is not None and source_elem.text else ""

            # Normalize common smart quotes and hyphens to ASCII equivalents
            title = (
                title.replace("\u2019", "'")
                .replace("\u2018", "'")
                .replace("\u201c", '"')
                .replace("\u201d", '"')
                .replace("\u2014", " - ")
                .replace("\u2013", "-")
            )

            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title = parts[0].strip()
                if not source:
                    source = parts[1].strip()

            if title:
                return NewsHeadline(
                    topic=topic_name,
                    title=title,
                    link=link,
                    published=pub_date,
                    source=source,
                )
    except Exception:
        pass
    return None


def _fetch_curated_news_sync(
    topics: list[tuple[str, str]] | None = None,
    timeout_seconds: float = 3.0,
) -> NewsDigestResult:
    """Synchronously fetch top 5 headlines across curated topics."""
    target_topics = topics or CURATED_TOPICS
    headlines: list[NewsHeadline] = []

    for topic_label, query in target_topics:
        item = _fetch_single_topic_sync(topic_label, query, timeout_seconds=timeout_seconds)
        if item is not None:
            headlines.append(item)

    return NewsDigestResult(headlines=headlines)


async def fetch_morning_news(
    topics: list[tuple[str, str]] | None = None,
    timeout_seconds: float = 3.0,
    enabled: bool = True,
) -> NewsDigestResult:
    """Asynchronously fetch top headlines in parallel during sub-second boot."""
    if not enabled:
        return NewsDigestResult(headlines=[])

    target_topics = topics or CURATED_TOPICS
    tasks = [
        asyncio.to_thread(_fetch_single_topic_sync, label, query, timeout_seconds)
        for label, query in target_topics
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    headlines: list[NewsHeadline] = []
    for res in results:
        if isinstance(res, NewsHeadline):
            headlines.append(res)

    return NewsDigestResult(headlines=headlines)
