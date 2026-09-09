"""Notion API reader, property parser, and status updater for Read Later items."""

from __future__ import annotations

import inspect
from typing import Any

from deskpilot.agent_tasks.read_later_agent.state import ReadLaterItem


def parse_notion_page(page: dict[str, Any]) -> ReadLaterItem:
    """Parse a Notion page object into a structured ReadLaterItem.

    Robustly handles various Notion property types:
    - Title: title property or rich_text property fallback
    - URL: url property or rich_text URL fallback
    - Status: status property or select property
    - Added Date: date property, created_time property, or page created_time
    - Tags: multi_select property or select property
    """
    page_id = str(page.get("id", "")).strip()
    properties = page.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    # 1. Extract Title
    title = ""
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            parts = prop.get("title", [])
            if isinstance(parts, list):
                title = "".join(
                    t.get("plain_text") or t.get("text", {}).get("content", "")
                    for t in parts
                    if isinstance(t, dict)
                ).strip()
            if title:
                break

    if not title:
        for name in ("Title", "title", "Name", "name"):
            prop = properties.get(name)
            if isinstance(prop, dict) and prop.get("type") == "rich_text":
                parts = prop.get("rich_text", [])
                if isinstance(parts, list):
                    title = "".join(
                        t.get("plain_text") or t.get("text", {}).get("content", "")
                        for t in parts
                        if isinstance(t, dict)
                    ).strip()
                if title:
                    break

    if not title:
        title = "Untitled"

    # 2. Extract URL
    url: str | None = None
    for name in ("URL", "url", "Link", "link"):
        prop = properties.get(name)
        if isinstance(prop, dict):
            if prop.get("type") == "url" and prop.get("url"):
                url = str(prop["url"]).strip()
                break
            elif prop.get("type") == "rich_text":
                parts = prop.get("rich_text", [])
                if isinstance(parts, list):
                    text = "".join(
                        t.get("plain_text") or t.get("text", {}).get("content", "")
                        for t in parts
                        if isinstance(t, dict)
                    ).strip()
                    if text.startswith("http://") or text.startswith("https://"):
                        url = text
                        break

    if url is None:
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "url" and prop.get("url"):
                url = str(prop["url"]).strip()
                break

    # 3. Extract Status
    status = "to be read"
    for name in ("Status", "status"):
        prop = properties.get(name)
        if isinstance(prop, dict):
            p_type = prop.get("type")
            if p_type == "status" and isinstance(prop.get("status"), dict):
                status = prop["status"].get("name", status)
                break
            elif p_type == "select" and isinstance(prop.get("select"), dict):
                status = prop["select"].get("name", status)
                break
            elif p_type == "rich_text":
                parts = prop.get("rich_text", [])
                if isinstance(parts, list):
                    text = "".join(
                        t.get("plain_text") or t.get("text", {}).get("content", "")
                        for t in parts
                        if isinstance(t, dict)
                    ).strip()
                    if text:
                        status = text
                        break
    else:
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "status" and isinstance(prop.get("status"), dict):
                status = prop["status"].get("name", status)
                break

    # 4. Extract Added Date
    added_date: str | None = None
    for name in ("Added", "Added Date", "Date", "date", "Created", "Created Date"):
        prop = properties.get(name)
        if isinstance(prop, dict):
            p_type = prop.get("type")
            if p_type == "date" and isinstance(prop.get("date"), dict):
                added_date = prop["date"].get("start")
                break
            elif p_type == "created_time" and prop.get("created_time"):
                added_date = str(prop["created_time"])
                break

    if added_date is None:
        for prop in properties.values():
            if isinstance(prop, dict):
                if prop.get("type") == "date" and isinstance(prop.get("date"), dict):
                    added_date = prop["date"].get("start")
                    break
                elif prop.get("type") == "created_time" and prop.get("created_time"):
                    added_date = str(prop["created_time"])
                    break

    if added_date is None and page.get("created_time"):
        added_date = str(page["created_time"])

    # 5. Extract Tags
    tags: list[str] = []
    for name in ("Tags", "tags", "Category", "Labels"):
        prop = properties.get(name)
        if isinstance(prop, dict):
            p_type = prop.get("type")
            if p_type == "multi_select" and isinstance(prop.get("multi_select"), list):
                tags = [
                    str(item.get("name", "")).strip()
                    for item in prop["multi_select"]
                    if isinstance(item, dict) and item.get("name")
                ]
                break
            elif p_type == "select" and isinstance(prop.get("select"), dict):
                val = prop["select"].get("name")
                if val:
                    tags = [str(val).strip()]
                break

    if not tags:
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "multi_select" and isinstance(prop.get("multi_select"), list):
                tags = [
                    str(item.get("name", "")).strip()
                    for item in prop["multi_select"]
                    if isinstance(item, dict) and item.get("name")
                ]
                if tags:
                    break

    return ReadLaterItem(
        page_id=page_id,
        title=title,
        url=url,
        status=status,
        added_date=added_date,
        tags=tags,
    )


async def query_unread_items(
    database_id: str,
    client: Any = None,
    query_filter: dict[str, Any] | None = None,
) -> list[ReadLaterItem]:
    """Query the Notion database for unread items where status equals 'to be read'.

    Supports both sync and async Notion clients gracefully.
    """
    if not database_id or client is None:
        return []

    if query_filter is None:
        query_filter = {
            "property": "Status",
            "status": {
                "equals": "to be read",
            },
        }

    try:
        res = client.databases.query(
            database_id=database_id,
            filter=query_filter,
        )
        if inspect.isawaitable(res):
            res = await res
    except Exception:
        # Fallback 1: Try with select property type filter
        try:
            fallback_filter = {
                "property": "Status",
                "select": {
                    "equals": "to be read",
                },
            }
            res = client.databases.query(
                database_id=database_id,
                filter=fallback_filter,
            )
            if inspect.isawaitable(res):
                res = await res
        except Exception:
            # Fallback 2: Try unfiltered query and filter client-side
            try:
                res = client.databases.query(database_id=database_id)
                if inspect.isawaitable(res):
                    res = await res
            except Exception:
                return []

    results = res.get("results", []) if isinstance(res, dict) else []
    items: list[ReadLaterItem] = []

    for page in results:
        if isinstance(page, dict):
            try:
                item = parse_notion_page(page)
                if item.status.strip().lower() == "to be read":
                    items.append(item)
            except Exception:
                continue

    return items


async def update_item_status(
    page_id: str,
    new_status: str = "read",
    client: Any = None,
) -> bool:
    """Patch the Notion page property Status -> new_status.

    Attempts status property type first, then falls back to select property type.
    """
    if not page_id or client is None:
        return False

    try:
        res = client.pages.update(
            page_id=page_id,
            properties={
                "Status": {
                    "status": {"name": new_status},
                },
            },
        )
        if inspect.isawaitable(res):
            await res
        return True
    except Exception:
        try:
            res = client.pages.update(
                page_id=page_id,
                properties={
                    "Status": {
                        "select": {"name": new_status},
                    },
                },
            )
            if inspect.isawaitable(res):
                await res
            return True
        except Exception:
            return False
