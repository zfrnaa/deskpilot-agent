from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from deskpilot.boot_tasks.news_digest import (
    NewsDigestResult,
    NewsHeadline,
    fetch_morning_news,
    _fetch_feed_items_sync,
    _hash_title,
    get_seen_titles,
    record_seen_titles,
)
from deskpilot.storage.store import SQLiteStore

SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>SpaceX launches new rocket - TechCrunch</title>
      <link>https://news.google.com/rss/articles/123</link>
      <pubDate>Fri, 06 Nov 2026 05:00:00 GMT</pubDate>
      <source url="https://techcrunch.com">TechCrunch</source>
    </item>
    <item>
      <title>New AI model released - Reuters</title>
      <link>https://news.google.com/rss/articles/456</link>
      <pubDate>Fri, 06 Nov 2026 04:30:00 GMT</pubDate>
      <source url="https://reuters.com">Reuters</source>
    </item>
  </channel>
</rss>
"""


def test_news_models():
    item = NewsHeadline(
        topic="Tech",
        title="Test headline",
        link="https://example.com",
        published="Fri, 06 Nov 2026",
        source="Example",
    )
    assert item.topic == "Tech"
    assert item.title == "Test headline"
    assert item.link == "https://example.com"
    res = NewsDigestResult(headlines=[item])
    assert len(res.headlines) == 1
    assert res.error is None


def test_fetch_feed_items_sync_parsing():
    mock_response = MagicMock()
    mock_response.read.return_value = SAMPLE_RSS_XML
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        items = _fetch_feed_items_sync("Technology", "https://example.com/rss")
        assert len(items) == 2
        assert items[0].title == "SpaceX launches new rocket"
        assert items[0].source == "TechCrunch"
        assert items[1].title == "New AI model released"


def test_seen_titles_deduplication(tmp_path):
    db_file = tmp_path / "news_store.db"
    store = SQLiteStore(db_path=db_file)

    # Initially empty
    assert len(get_seen_titles(store)) == 0

    # Record two hashes
    h1 = _hash_title("SpaceX launches new rocket")
    h2 = _hash_title("Another headline")
    record_seen_titles(store, {h1, h2})

    seen = get_seen_titles(store)
    assert h1 in seen
    assert h2 in seen


@pytest.mark.asyncio
async def test_fetch_morning_news_skips_seen(tmp_path):
    db_file = tmp_path / "news_skip_store.db"
    store = SQLiteStore(db_path=db_file)

    # Mark the first article as already seen yesterday
    seen_hash = _hash_title("SpaceX launches new rocket")
    record_seen_titles(store, {seen_hash})

    mock_candidates = [
        NewsHeadline(topic="Tech", title="SpaceX launches new rocket", link="https://1"),
        NewsHeadline(topic="Tech", title="New AI model released", link="https://2"),
    ]

    with patch("deskpilot.boot_tasks.news_digest._fetch_topic_with_fallback_sync", return_value=mock_candidates):
        res = await fetch_morning_news(
            topic_configs=[("Tech", "https://mock", None)],
            store=store,
            enabled=True,
        )

        # It should automatically skip the first already-seen article and pick the fresh second one!
        assert len(res.headlines) == 1
        assert res.headlines[0].title == "New AI model released"


@pytest.mark.asyncio
async def test_fetch_morning_news_disabled():
    res = await fetch_morning_news(enabled=False)
    assert res.headlines == []
    assert res.error is None
