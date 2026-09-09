"""Notion synchronization, schema introspection, and safe local cleanup for screenshot triage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem
from deskpilot.config import ScreenshotDestinationsConfig

SYNCABLE_CLASSIFICATIONS = {"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "NOTION_NOTE"}


def introspect_database_schema(notion_client: Any, database_id: str) -> dict[str, Any]:
    """Retrieve database metadata and introspect property schema mapping.

    Dynamically detects:
    - Title property (id or type == "title", or named Name/Title/Topic)
    - Tag/category property (type in ("select", "multi_select"), prioritizing Tags, Category, Topic, Area)
    - Date property (type == "date", prioritizing Date, Created)
    """
    db_meta = notion_client.databases.retrieve(database_id=database_id)
    properties = db_meta.get("properties", {})

    title_prop: str | None = None
    tag_prop: str | None = None
    tag_type: str | None = None
    date_prop: str | None = None

    tag_preferred = ["tags", "category", "topic", "area", "tag"]
    date_preferred = ["date", "created", "created date", "date created"]

    # 1. Find title property
    for name, prop in properties.items():
        if prop.get("type") == "title" or prop.get("id") == "title":
            title_prop = name
            break
    if not title_prop:
        for name in properties:
            if name.lower() in ("name", "title", "topic"):
                title_prop = name
                break

    # 2. Find tag/category property
    for pref in tag_preferred:
        for name, prop in properties.items():
            if name.lower() == pref and prop.get("type") in ("select", "multi_select"):
                tag_prop = name
                tag_type = prop.get("type")
                break
        if tag_prop:
            break
    if not tag_prop:
        for name, prop in properties.items():
            if prop.get("type") in ("select", "multi_select"):
                tag_prop = name
                tag_type = prop.get("type")
                break

    # 3. Find date property
    for pref in date_preferred:
        for name, prop in properties.items():
            if name.lower() == pref and prop.get("type") == "date":
                date_prop = name
                break
        if date_prop:
            break
    if not date_prop:
        for name, prop in properties.items():
            if prop.get("type") == "date":
                date_prop = name
                break

    return {
        "title_prop": title_prop or "Name",
        "tag_prop": tag_prop,
        "tag_type": tag_type,
        "date_prop": date_prop,
    }


def build_database_page_payload(
    item: ScreenshotItem,
    database_id: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Construct Notion pages.create payload matching introspected database schema."""
    title_text = item.title.strip() if item.title else item.filename
    properties: dict[str, Any] = {}

    title_name = schema.get("title_prop") or "Name"
    properties[title_name] = {
        "title": [{"type": "text", "text": {"content": title_text}}]
    }

    tag_name = schema.get("tag_prop")
    tag_type = schema.get("tag_type")
    tag_val = item.cluster_tag or "General"
    if tag_name:
        if tag_type == "multi_select":
            properties[tag_name] = {"multi_select": [{"name": tag_val}]}
        elif tag_type == "select":
            properties[tag_name] = {"select": {"name": tag_val}}

    date_name = schema.get("date_prop")
    if date_name:
        today_iso = datetime.now(timezone.utc).date().isoformat()
        properties[date_name] = {"date": {"start": today_iso}}

    children: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": title_text}}],
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": (
                                f"Category: {item.cluster_tag}\n"
                                f"Rationale: {item.rationale or 'N/A'}\n"
                                f"Source Screenshot: {item.filename}"
                            )
                        },
                    }
                ],
            },
        },
    ]

    return {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": children,
    }


def build_page_append_blocks(item: ScreenshotItem) -> list[dict[str, Any]]:
    """Build callout and text blocks for appending to a Notion page."""
    title_text = item.title.strip() if item.title else item.filename
    emoji = "💡" if item.classification == "BRAINSTORM" else "📋"
    return [
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": (
                                f"{title_text}\n"
                                f"Rationale: {item.rationale or 'N/A'}\n"
                                f"Category: {item.cluster_tag}\n"
                                f"Source: {item.filename}"
                            )
                        },
                    }
                ],
                "icon": {"type": "emoji", "emoji": emoji},
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"Classification: {item.classification} | Tag: {item.cluster_tag}"
                        },
                    }
                ],
            },
        },
    ]


def resolve_destination(
    classification: str,
    destinations: ScreenshotDestinationsConfig | None = None,
    database_id: str = "",
    parent_page_id: str = "",
) -> tuple[str, str] | tuple[None, None]:
    """Resolve target ID and target type ("database", "page", or "legacy_page") based on classification.

    Returns (target_id, target_type) or (None, None) if unconfigured or LOCAL_KEEP.
    """
    if classification == "LOCAL_KEEP":
        return None, None

    if classification == "WORK_NOTES":
        target = (destinations.work_notes_database_id if destinations else "") or database_id
        return (target, "database") if target else (None, None)

    if classification == "DUE_DILIGENCE":
        target = destinations.due_diligence_page_id if destinations else ""
        return (target, "page") if target else (None, None)

    if classification == "BRAINSTORM":
        target = destinations.brainstorm_page_id if destinations else ""
        return (target, "page") if target else (None, None)

    if classification == "NOTION_NOTE":
        target = (destinations.work_notes_database_id if destinations else "") or database_id
        if target:
            return (target, "database")
        if parent_page_id:
            return (parent_page_id, "legacy_page")
        return None, None

    return None, None


def sync_screenshot_to_notion(
    item: ScreenshotItem,
    notion_client: Any,
    parent_page_id: str = "",
    database_id: str = "",
    destinations: ScreenshotDestinationsConfig | None = None,
    schema_cache: dict[str, Any] | None = None,
) -> bool:
    """Synchronize a single screenshot item to its designated Notion target.

    Sets is_synced=True strictly upon verified API success.
    """
    if notion_client is None:
        item.is_synced = False
        return False

    if item.classification == "LOCAL_KEEP":
        item.is_synced = False
        return False

    target_id, target_type = resolve_destination(
        classification=item.classification,
        destinations=destinations,
        database_id=database_id,
        parent_page_id=parent_page_id,
    )

    if not target_id:
        item.is_synced = False
        return False

    title_text = item.title.strip() if item.title else item.filename

    try:
        if target_type == "database":
            schema = schema_cache.get(target_id) if schema_cache is not None else None
            if schema is None:
                try:
                    schema = introspect_database_schema(notion_client, target_id)
                except Exception:
                    schema = {"title_prop": "Name"}
                if schema_cache is not None:
                    schema_cache[target_id] = schema

            payload = build_database_page_payload(item, target_id, schema)
            notion_client.pages.create(**payload)
            item.is_synced = True
            return True

        elif target_type == "page":
            blocks = build_page_append_blocks(item)
            notion_client.blocks.children.append(
                block_id=target_id,
                children=blocks,
            )
            item.is_synced = True
            return True

        elif target_type == "legacy_page":
            parent = {"page_id": target_id}
            properties = {
                "title": [{"type": "text", "text": {"content": title_text}}],
            }
            children = [
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": title_text}}],
                    },
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": (
                                        f"Category: {item.cluster_tag}\n"
                                        f"Rationale: {item.rationale or 'N/A'}\n"
                                        f"Source Screenshot: {item.filename}"
                                    )
                                },
                            }
                        ],
                    },
                },
            ]
            notion_client.pages.create(
                parent=parent,
                properties=properties,
                children=children,
            )
            item.is_synced = True
            return True

    except Exception:
        item.is_synced = False
        return False

    item.is_synced = False
    return False


def sync_approved_items(
    items: list[ScreenshotItem],
    approved_cluster_keys: list[str],
    notion_client: Any,
    parent_page_id: str = "",
    database_id: str = "",
    destinations: ScreenshotDestinationsConfig | None = None,
) -> tuple[int, list[str]]:
    """Synchronize all approved screenshots to their Notion destinations.

    Returns the count of successfully synced items and any error messages encountered.
    """
    synced_count = 0
    errors: list[str] = []
    schema_cache: dict[str, Any] = {}

    if notion_client is None:
        return 0, ["Notion client is not configured or disabled"]

    for item in items:
        if item.classification in SYNCABLE_CLASSIFICATIONS and item.cluster_tag in approved_cluster_keys:
            target_id, _ = resolve_destination(
                classification=item.classification,
                destinations=destinations,
                database_id=database_id,
                parent_page_id=parent_page_id,
            )
            if not target_id:
                item.is_synced = False
                errors.append(f"Destination ID not configured for category {item.classification}")
                continue

            try:
                success = sync_screenshot_to_notion(
                    item=item,
                    notion_client=notion_client,
                    parent_page_id=parent_page_id,
                    database_id=database_id,
                    destinations=destinations,
                    schema_cache=schema_cache,
                )
                if success:
                    synced_count += 1
                else:
                    errors.append(f"Failed to sync {item.filename} to Notion")
            except Exception as e:
                item.is_synced = False
                errors.append(f"Unexpected error syncing {item.filename}: {e}")
        else:
            item.is_synced = False

    return synced_count, errors


def cleanup_synced_files(
    items: list[ScreenshotItem],
    delete_synced_local: bool = True,
) -> tuple[int, list[str]]:
    """Safely unlink local files for successfully synced screenshots.

    CRITICAL SAFETY GUARANTEES:
    - Never delete files if delete_synced_local is False.
    - Never delete files where is_synced is False.
    - Never delete files classified as LOCAL_KEEP or unapproved.
    - Only delete files with is_synced == True and classification != LOCAL_KEEP.
    """
    deleted_count = 0
    errors: list[str] = []

    if not delete_synced_local:
        return 0, []

    for item in items:
        # STRICT SAFETY CHECK: Both is_synced is True AND classification is NOT LOCAL_KEEP
        if (
            item.is_synced is True
            and item.classification in SYNCABLE_CLASSIFICATIONS
            and item.classification != "LOCAL_KEEP"
            and not item.deleted_locally
        ):
            try:
                if item.path.exists():
                    item.path.unlink()
                item.deleted_locally = True
                deleted_count += 1
            except Exception as e:
                errors.append(f"Failed to delete {item.filename}: {e}")
        else:
            # Strictly untouched
            pass

    return deleted_count, errors

