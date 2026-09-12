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
    status = "To Be Read"
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
    """Query the Notion database for unread items where status equals 'To Be Read'.

    Supports both sync and async Notion clients gracefully.
    """
    if not database_id or client is None:
        return []

    async def _try_query(endpoint: Any, target_arg: str, target_val: str, filt: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            kwargs = {target_arg: target_val}
            if filt is not None:
                kwargs["filter"] = filt
            out = endpoint(**kwargs)
            if inspect.isawaitable(out):
                out = await out
            if isinstance(out, dict) and "results" in out:
                return out
        except Exception:
            return None
        return None

    # Resolve endpoint and target ID (support both databases.query and modern data_sources.query)
    target_arg = "database_id"
    target_val = database_id
    query_fn = getattr(getattr(client, "databases", None), "query", None)

    if query_fn is None or not callable(query_fn):
        # Modern notion-client SDK: query via data_sources
        if hasattr(client, "databases") and hasattr(client, "data_sources"):
            try:
                db_meta = client.databases.retrieve(database_id=database_id)
                if inspect.isawaitable(db_meta):
                    db_meta = await db_meta
                if isinstance(db_meta, dict) and db_meta.get("data_sources"):
                    ds_list = db_meta["data_sources"]
                    if isinstance(ds_list, list) and ds_list:
                        target_val = ds_list[0].get("id") or database_id
                        target_arg = "data_source_id"
                        query_fn = getattr(client.data_sources, "query", None)
            except Exception:
                pass

    if query_fn is None or not callable(query_fn):
        return []

    res: dict[str, Any] | None = None
    if query_filter is not None:
        res = await _try_query(query_fn, target_arg, target_val, query_filter)
    else:
        # Query status variant "To Be Read"
        for val in ["To Be Read"]:
            res = await _try_query(
                query_fn,
                target_arg,
                target_val,
                {"property": "Status", "status": {"equals": val}},
            )
            if res is not None:
                break
            res = await _try_query(
                query_fn,
                target_arg,
                target_val,
                {"property": "Status", "select": {"equals": val}},
            )
            if res is not None:
                break

    if res is None:
        # Fallback: query unfiltered and filter client-side
        res = await _try_query(query_fn, target_arg, target_val, None)

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


async def fetch_page_markdown(page_id: str, client: Any = None) -> str:
    """Fetch the body blocks of a Notion page and convert them to readable markdown text."""
    if not page_id or client is None:
        return ""

    try:
        blocks_fn = getattr(getattr(client, "blocks", None), "children", None)
        if blocks_fn is None:
            return ""

        list_fn = getattr(blocks_fn, "list", None)
        if list_fn is None or not callable(list_fn):
            return ""

        res = list_fn(block_id=page_id)
        if inspect.isawaitable(res):
            res = await res

        if not isinstance(res, dict):
            return ""

        blocks = res.get("results", [])
        lines: list[str] = []

        def _extract_rich_text(elements: list[dict[str, Any]]) -> str:
            buf = []
            for elem in elements:
                if isinstance(elem, dict):
                    buf.append(elem.get("plain_text") or elem.get("text", {}).get("content", ""))
            return "".join(buf)

        for block in blocks:
            if not isinstance(block, dict):
                continue
            b_type = block.get("type")
            data = block.get(b_type, {}) if isinstance(b_type, str) else {}
            if not isinstance(data, dict):
                continue

            rich_texts = data.get("rich_text", [])
            text = _extract_rich_text(rich_texts) if isinstance(rich_texts, list) else ""

            if b_type == "paragraph":
                lines.append(text + "\n")
            elif b_type == "heading_1":
                lines.append(f"# {text}\n")
            elif b_type == "heading_2":
                lines.append(f"## {text}\n")
            elif b_type == "heading_3":
                lines.append(f"### {text}\n")
            elif b_type == "bulleted_list_item":
                lines.append(f"- {text}")
            elif b_type == "numbered_list_item":
                lines.append(f"1. {text}")
            elif b_type == "to_do":
                checked = "x" if data.get("checked") else " "
                lines.append(f"- [{checked}] {text}")
            elif b_type == "quote":
                lines.append(f"> {text}\n")
            elif b_type == "code":
                lang = data.get("language", "")
                lines.append(f"```{lang}\n{text}\n```\n")
            elif b_type == "callout":
                icon = data.get("icon", {}).get("emoji", "💡") if isinstance(data.get("icon"), dict) else "💡"
                lines.append(f"> {icon} {text}\n")
            elif text:
                lines.append(text)

        return "\n".join(lines).strip()
    except Exception:
        return ""
