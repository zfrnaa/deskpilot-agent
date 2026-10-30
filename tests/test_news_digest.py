from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from deskpilot.boot_tasks.news_digest import (
    NewsDigestResult,
    NewsHeadline,
    fetch_morning_news,
    _fetch_single_topic_sync,
    _fetch_curated_news_sync,
)

SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>SpaceX successfully launches orbital test - TechCrunch</title>
      <link>https://news.google.com/rss/articles/123</link>
      <pubDate>Fri, 30 Oct 2026 05:00:00 GMT</pubDate>
      <source url="https://techcrunch.com">TechCrunch</source>
    </item>
  </channel>
</rss>
"""


def test_news_models():
    item = NewsHeadline(
        topic="Tech",
        title="Test headline",
        link="https://example.com",
        published="Fri, 30 Oct 2026",
        source="Example",
    )
    assert item.topic == "Tech"
    assert item.title == "Test headline"
    assert item.link == "https://example.com"
    res = NewsDigestResult(headlines=[item])
    assert len(res.headlines) == 1
    assert res.error is None


def test_fetch_single_topic_sync_parsing():
    mock_response = MagicMock()
    mock_response.read.return_value = SAMPLE_RSS_XML
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        item = _fetch_single_topic_sync("Technology", "technology query")
        assert item is not None
        assert item.topic == "Technology"
        assert item.title == "SpaceX successfully launches orbital test"
        assert item.source == "TechCrunch"


def test_fetch_curated_news_sync():
    mock_item = NewsHeadline(
        topic="Technology",
        title="Nvidia launches chip",
        link="https://example.com",
    )
    with patch("deskpilot.boot_tasks.news_digest._fetch_single_topic_sync", return_value=mock_item):
        res = _fetch_curated_news_sync(topics=[("Technology", "nvidia")])
        assert len(res.headlines) == 1
        assert res.headlines[0].title == "Nvidia launches chip"


@pytest.mark.asyncio
async def test_fetch_morning_news_disabled():
    res = await fetch_morning_news(enabled=False)
    assert res.headlines == []
    assert res.error is None
