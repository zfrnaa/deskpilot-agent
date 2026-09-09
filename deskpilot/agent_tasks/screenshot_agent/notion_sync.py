"""Notion synchronization and safe local cleanup for screenshot triage."""

from __future__ import annotations

from typing import Any

from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem


def sync_screenshot_to_notion(
    item: ScreenshotItem,
    notion_client: Any,
    parent_page_id: str = "",
    database_id: str = "",
) -> bool:
    """Synchronize a single approved NOTION_NOTE screenshot item to Notion.

    Creates a page under the target parent_page_id or database_id.
    Sets is_synced=True strictly upon verified API success.
    """
    if notion_client is None:
        item.is_synced = False
        return False

    title_text = item.title.strip() if item.title else item.filename

    # Build parent and properties depending on whether target is a page or a database
    if database_id:
        parent: dict[str, Any] = {"database_id": database_id}
        properties: dict[str, Any] = {
            "Name": {
                "title": [{"type": "text", "text": {"content": title_text}}],
            },
        }
    elif parent_page_id:
        parent = {"page_id": parent_page_id}
        properties = {
            "title": [{"type": "text", "text": {"content": title_text}}],
        }
    else:
        item.is_synced = False
        return False

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

    try:
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


def sync_approved_items(
    items: list[ScreenshotItem],
    approved_cluster_keys: list[str],
    notion_client: Any,
    parent_page_id: str = "",
    database_id: str = "",
) -> tuple[int, list[str]]:
    """Synchronize all approved NOTION_NOTE items to Notion.

    Returns the count of successfully synced items and any error messages encountered.
    """
    synced_count = 0
    errors: list[str] = []

    if notion_client is None:
        return 0, ["Notion client is not configured or disabled"]

    for item in items:
        # Strict gate: only sync items that are classified as NOTION_NOTE and belong to approved clusters
        if item.classification == "NOTION_NOTE" and item.cluster_tag in approved_cluster_keys:
            try:
                success = sync_screenshot_to_notion(
                    item=item,
                    notion_client=notion_client,
                    parent_page_id=parent_page_id,
                    database_id=database_id,
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
    - Only delete files with is_synced == True and classification == "NOTION_NOTE".
    """
    deleted_count = 0
    errors: list[str] = []

    if not delete_synced_local:
        return 0, []

    for item in items:
        # STRICT SAFETY CHECK: Both is_synced is True AND classification is NOTION_NOTE
        if item.is_synced is True and item.classification == "NOTION_NOTE" and not item.deleted_locally:
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
