"""Notion synchronization, schema introspection, and safe local cleanup for screenshot triage."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem
from deskpilot.config import ScreenshotDestinationsConfig

logger = logging.getLogger(__name__)

SYNCABLE_CLASSIFICATIONS = {"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "NOTION_NOTE"}


def upload_screenshot_to_notion(notion_client: Any, image_path: Path) -> str | None:
    """Upload a screenshot image to Notion using the file_uploads endpoint.

    Returns the file upload ID if successful, or None if the file is missing or upload fails.
    """
    if not image_path.exists():
        logger.warning("Cannot upload screenshot to Notion: file not found at %s", image_path)
        return None

    ext = image_path.suffix.lower()
    if ext == ".png":
        content_type = "image/png"
    elif ext in (".jpg", ".jpeg"):
        content_type = "image/jpeg"
    else:
        content_type = "application/octet-stream"

    try:
        fu = notion_client.file_uploads.create(filename=image_path.name, content_type=content_type)
        upload_id = fu.get("id") if isinstance(fu, dict) else getattr(fu, "id", None)
        if not upload_id:
            logger.warning("Notion file_uploads.create returned no id for %s", image_path.name)
            return None

        file_bytes = image_path.read_bytes()
        notion_client.file_uploads.send(upload_id, file=(image_path.name, file_bytes, content_type))
        return upload_id
    except Exception as exc:
        logger.warning("Failed to upload screenshot %s to Notion: %s", image_path.name, exc)
        return None


def introspect_database_schema(notion_client: Any, database_id: str) -> dict[str, Any]:
    """Retrieve database metadata and introspect property schema mapping.

    Dynamically detects:
    - Title property (id or type == "title", or named Name/Title/Topic)
    - Tag/category property (type in ("select", "multi_select"), prioritizing Tags, Category, Topic, Area)
    - Date property (type == "date", prioritizing Date, Created)
    """
    properties: dict[str, Any] = {}
    db_meta = notion_client.databases.retrieve(database_id=database_id)
    if isinstance(db_meta, dict):
        properties = db_meta.get("properties") or {}
        if not properties and db_meta.get("data_sources"):
            data_sources = db_meta["data_sources"]
            if isinstance(data_sources, list) and data_sources:
                ds_id = data_sources[0].get("id")
                if ds_id and hasattr(notion_client, "data_sources"):
                    try:
                        ds_meta = notion_client.data_sources.retrieve(data_source_id=ds_id)
                        if isinstance(ds_meta, dict):
                            properties = ds_meta.get("properties") or {}
                    except Exception:
                        pass

    title_prop: str | None = None
    tag_prop: str | None = None
    tag_type: str | None = None
    date_prop: str | None = None

    tag_preferred = ["subject", "tags", "category", "topic", "area", "tag"]
    date_preferred = ["date", "created", "created date", "date created"]

    # 1. Find title property
    for name, prop in properties.items():
        if isinstance(prop, dict) and (prop.get("type") == "title" or prop.get("id") == "title"):
            title_prop = name
            break
    if not title_prop:
        for name in properties:
            if name.lower() in ("name", "title", "topic", "question", "idea"):
                title_prop = name
                break

    # 2. Find tag/category property
    for pref in tag_preferred:
        for name, prop in properties.items():
            if isinstance(prop, dict) and name.lower() == pref and prop.get("type") in ("select", "multi_select"):
                tag_prop = name
                tag_type = prop.get("type")
                break
        if tag_prop:
            break
    if not tag_prop:
        for name, prop in properties.items():
            if isinstance(prop, dict) and prop.get("type") in ("select", "multi_select"):
                tag_prop = name
                tag_type = prop.get("type")
                break

    # 3. Find date property
    for pref in date_preferred:
        for name, prop in properties.items():
            if isinstance(prop, dict) and name.lower() == pref and prop.get("type") == "date":
                date_prop = name
                break
        if date_prop:
            break
    if not date_prop:
        for name, prop in properties.items():
            if isinstance(prop, dict) and prop.get("type") == "date":
                date_prop = name
                break

    tag_options: list[str] = []
    if tag_prop and tag_type:
        prop_obj = properties.get(tag_prop, {})
        if isinstance(prop_obj, dict):
            options = prop_obj.get(tag_type, {}).get("options", [])
            if isinstance(options, list):
                tag_options = [
                    opt.get("name")
                    for opt in options
                    if isinstance(opt, dict) and opt.get("name")
                ]

    return {
        "title_prop": title_prop or "Name",
        "tag_prop": tag_prop,
        "tag_type": tag_type,
        "date_prop": date_prop,
        "tag_options": tag_options,
    }


def build_database_page_payload(
    item: ScreenshotItem,
    database_id: str,
    schema: dict[str, Any],
    file_upload_id: str | None = None,
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
                                f"Rationale: {item.rationale or 'N/A'}"
                            )
                        },
                    }
                ],
            },
        },
    ]

    if file_upload_id:
        children.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {"id": file_upload_id},
                },
            }
        )

    return {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": children,
    }


def build_page_append_blocks(
    item: ScreenshotItem,
    file_upload_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build callout and text blocks for appending to a Notion page."""
    title_text = item.title.strip() if item.title else item.filename
    emoji = "💡" if item.classification == "BRAINSTORM" else "📋"
    blocks: list[dict[str, Any]] = [
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
                                f"Rationale: {item.rationale or 'N/A'}"
                            )
                        },
                    }
                ],
                "icon": {"type": "emoji", "emoji": emoji},
            },
        },
    ]

    if file_upload_id:
        blocks.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {"id": file_upload_id},
                },
            }
        )

    return blocks


def build_page_append_section_blocks(
    item: ScreenshotItem,
    file_upload_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build section blocks (divider, heading_3, details paragraph, optional image) for appending to an existing page."""
    title_text = item.title.strip() if item.title else item.filename
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "divider",
            "divider": {},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {
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
                                f"Rationale: {item.rationale or 'N/A'}"
                            )
                        },
                    }
                ],
            },
        },
    ]

    if file_upload_id:
        blocks.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {"id": file_upload_id},
                },
            }
        )

    return blocks


def fetch_existing_database_pages(notion_client: Any, database_id: str) -> list[dict[str, str]]:
    """Fetch existing pages from a Notion database to enable consolidation.

    Returns a list of dicts: [{"id": page_id, "title": page_title}, ...]
    Returns [] cleanly on any error or if none found.
    """
    if notion_client is None or not database_id:
        return []

    try:
        pages_raw: list[dict[str, Any]] = []

        # 1. Check database metadata for data_sources
        db_meta = notion_client.databases.retrieve(database_id=database_id)

        data_sources = db_meta.get("data_sources") if isinstance(db_meta, dict) else None
        if data_sources and isinstance(data_sources, list) and hasattr(notion_client, "data_sources"):
            ds_id = data_sources[0].get("id")
            if ds_id and hasattr(notion_client.data_sources, "query"):
                try:
                    resp = notion_client.data_sources.query(data_source_id=ds_id, page_size=100)
                    if isinstance(resp, dict):
                        pages_raw = resp.get("results") or []
                except Exception as ds_err:
                    logger.debug("data_sources.query failed: %s", ds_err)

        # 2. Check if client.databases.query exists (standard Notion API)
        if not pages_raw and hasattr(notion_client.databases, "query"):
            try:
                resp = notion_client.databases.query(database_id=database_id, page_size=100)
                if isinstance(resp, dict):
                    pages_raw = resp.get("results") or []
            except Exception as q_err:
                logger.debug("databases.query failed: %s", q_err)

        # 3. Fallback: use search endpoint if available
        if not pages_raw and hasattr(notion_client, "search"):
            try:
                search_resp = notion_client.search(
                    filter={"value": "page", "property": "object"},
                    page_size=100,
                )
                if isinstance(search_resp, dict):
                    all_results = search_resp.get("results") or []
                    for p in all_results:
                        if not isinstance(p, dict):
                            continue
                        parent = p.get("parent") or {}
                        # Match parent database_id or data_source_id
                        p_db_id = parent.get("database_id", "").replace("-", "")
                        clean_target_id = database_id.replace("-", "")
                        if p_db_id and p_db_id == clean_target_id:
                            pages_raw.append(p)
                        elif data_sources and isinstance(data_sources, list):
                            ds_ids = {
                                ds.get("id", "").replace("-", "")
                                for ds in data_sources
                                if isinstance(ds, dict)
                            }
                            p_ds_id = parent.get("data_source_id", "").replace("-", "")
                            if p_ds_id and p_ds_id in ds_ids:
                                pages_raw.append(p)
            except Exception as search_err:
                logger.debug("client.search failed: %s", search_err)

        # Extract titles from pages_raw
        existing_pages: list[dict[str, str]] = []
        for page in pages_raw:
            page_id = page.get("id")
            if not page_id:
                continue
            properties = page.get("properties") or {}
            title_text = ""
            for prop in properties.values():
                if isinstance(prop, dict) and prop.get("type") == "title":
                    title_list = prop.get("title") or []
                    if isinstance(title_list, list):
                        parts = [
                            t.get("plain_text", "")
                            for t in title_list
                            if isinstance(t, dict) and t.get("plain_text")
                        ]
                        title_text = "".join(parts).strip()
                    break

            existing_pages.append({"id": page_id, "title": title_text})

        return existing_pages
    except Exception as exc:
        err_text = str(exc).lower()
        # If unauthorized (401) or object_not_found (404), re-raise to trigger interactive prompt recovery
        if any(k in err_text for k in ("unauthorized", "401", "invalid_token", "object_not_found", "404", "could not find")):
            raise exc
        logger.warning("Failed to fetch existing database pages for %s: %s", database_id, exc)
        return []


def match_existing_page(
    item: ScreenshotItem,
    existing_pages: list[dict[str, str]],
    llm: Any = None,
) -> str | None:
    """Find a matching existing database page for item consolidation.

    Stage 1: Normalized exact match, guarded long substring, or token overlap (filtering stopwords).
    Stage 2: LLM verification if token overlap is inconclusive.
    Returns matched page ID or None.
    """
    if not existing_pages:
        return None

    item_title = (item.title or "").strip()
    if not item_title:
        return None

    stopwords = {
        "a", "an", "the", "and", "or", "for", "of", "in", "to", "on", "with", "at", "by", "from",
    }

    def _tokenize(text: str) -> set[str]:
        cleaned = re.sub(r"[^\w\s]", " ", text.lower())
        return {tok for tok in cleaned.split() if tok and tok not in stopwords}

    item_tokens = _tokenize(item_title)
    item_title_clean = re.sub(r"[^\w\s]", " ", item_title.lower()).strip()

    # Stage 1: Exact or token overlap
    for page in existing_pages:
        p_title = (page.get("title") or "").strip()
        if not p_title:
            continue
        p_title_clean = re.sub(r"[^\w\s]", " ", p_title.lower()).strip()

        # Exact match
        if item_title_clean == p_title_clean:
            return page["id"]

        # Only allow substring match if both titles are substantial (at least 3 words) to avoid false positives
        if len(item_title_clean.split()) >= 3 and len(p_title_clean.split()) >= 3:
            if item_title_clean in p_title_clean or p_title_clean in item_title_clean:
                return page["id"]

        p_tokens = _tokenize(p_title)
        if item_tokens and p_tokens:
            intersection = item_tokens & p_tokens
            min_len = min(len(item_tokens), len(p_tokens))
            if min_len > 0 and (len(intersection) / min_len) >= 0.75:
                return page["id"]

    # Stage 2: LLM semantic verification
    if llm is not None:
        try:
            page_titles = [p.get("title", "").strip() for p in existing_pages if p.get("title")]
            titles_formatted = "\n".join(f"- {t}" for t in page_titles)
            prompt = (
                f"You are a Notion database organizer.\n"
                f"We have a new screenshot item with:\n"
                f"Title: {item.title}\n"
                f"Rationale: {item.rationale or 'N/A'}\n\n"
                f"Existing page titles in database:\n"
                f"{titles_formatted}\n\n"
                f"Does this screenshot conceptually belong as an addition to one of these existing pages?\n"
                f"If YES, respond with ONLY the exact title of the matching page (no markdown, no quotes, no extra words).\n"
                f"If NO existing page matches, respond with 'NONE'."
            )
            response = llm.invoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            clean_content = content.strip().strip("'\"`")

            if clean_content and clean_content.upper() != "NONE":
                for page in existing_pages:
                    if clean_content.lower() == (page.get("title") or "").strip().lower():
                        return page["id"]
        except Exception as llm_err:
            logger.debug("LLM page matching failed: %s", llm_err)

    return None


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
    raise_on_error: bool = False,
    llm: Any = None,
    page_cache: dict[str, list[dict[str, str]]] | None = None,
    consolidate_pages: bool = True,
    reasoning_llm: Any = None,
    fallback_reasoning_llm: Any = None,
) -> bool:
    """Synchronize a single screenshot item to its designated Notion target.

    Sets is_synced=True strictly upon verified API success.
    Automatically adapts between page appending and database creation if target is a database.
    Uploads screenshot image and consolidates into existing database pages when appropriate.
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

    # Upload screenshot image
    file_upload_id = upload_screenshot_to_notion(notion_client, item.path)

    def _create_database_page(db_id: str) -> bool:
        schema = schema_cache.get(db_id) if schema_cache is not None else None
        if schema is None:
            schema = introspect_database_schema(notion_client, db_id)
            if schema_cache is not None:
                schema_cache[db_id] = schema
        payload = build_database_page_payload(item, db_id, schema, file_upload_id=file_upload_id)
        created_page = notion_client.pages.create(**payload)
        if page_cache is not None and isinstance(created_page, dict):
            created_id = created_page.get("id")
            if created_id:
                page_cache.setdefault(db_id, []).append({"id": created_id, "title": title_text})
        item.is_synced = True
        return True

    try:
        if target_type == "database":
            if reasoning_llm is not None:
                from deskpilot.agent_tasks.screenshot_agent.react_agent import run_react_consolidation_agent

                schema = schema_cache.get(target_id) if schema_cache is not None else None
                if schema is None:
                    schema = introspect_database_schema(notion_client, target_id)
                    if schema_cache is not None:
                        schema_cache[target_id] = schema

                react_res = run_react_consolidation_agent(
                    item=item,
                    notion_client=notion_client,
                    database_id=target_id,
                    schema=schema,
                    llm=reasoning_llm,
                    fallback_llm=fallback_reasoning_llm,
                    file_upload_id=file_upload_id or "",
                )
                if react_res.get("synced"):
                    item.is_synced = True
                    return True
                else:
                    item.is_synced = False
                    if raise_on_error and react_res.get("error"):
                        raise RuntimeError(react_res.get("error"))
                    return False

            if consolidate_pages:
                if page_cache is not None and target_id in page_cache:
                    existing_pages = page_cache[target_id]
                else:
                    existing_pages = fetch_existing_database_pages(notion_client, target_id)
                    if page_cache is not None:
                        page_cache[target_id] = existing_pages

                matched_page_id = match_existing_page(item, existing_pages, llm=llm)
                if matched_page_id:
                    blocks = build_page_append_section_blocks(item, file_upload_id=file_upload_id)
                    notion_client.blocks.children.append(
                        block_id=matched_page_id,
                        children=blocks,
                    )
                    item.is_synced = True
                    return True

            return _create_database_page(target_id)

        elif target_type == "page":
            try:
                blocks = build_page_append_blocks(item, file_upload_id=file_upload_id)
                notion_client.blocks.children.append(
                    block_id=target_id,
                    children=blocks,
                )
                item.is_synced = True
                return True
            except Exception as page_err:
                err_msg = str(page_err).lower()
                # If target is actually a database or block does not support child blocks, fall back to database creation
                if "database" in err_msg or "does not support children" in err_msg:
                    return _create_database_page(target_id)
                raise page_err

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
                                        f"Rationale: {item.rationale or 'N/A'}"
                                    )
                                },
                            }
                        ],
                    },
                },
            ]
            if file_upload_id:
                children.append(
                    {
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "file_upload",
                            "file_upload": {"id": file_upload_id},
                        },
                    }
                )
            notion_client.pages.create(
                parent=parent,
                properties=properties,
                children=children,
            )
            item.is_synced = True
            return True

    except Exception as exc:
        item.is_synced = False
        if raise_on_error:
            raise exc
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
    prompt_func: Any = None,
    console_print: Any = None,
    llm: Any = None,
    consolidate_pages: bool = True,
    reasoning_llm: Any = None,
    fallback_reasoning_llm: Any = None,
) -> tuple[int, list[str]]:
    """Synchronize all approved screenshots to their Notion destinations.

    Supports interactive recovery when prompt_func is provided:
    - On 401/unauthorized error: prompts user for a corrected Notion API token.
    - On 404/not_found error: prompts user for a corrected Database or Target ID.
    - If user enters empty string (presses Enter), gracefully finishes without passing to Notion.
    """
    synced_count = 0
    errors: list[str] = []
    schema_cache: dict[str, Any] = {}
    page_cache: dict[str, list[dict[str, str]]] = {}
    current_notion_client = notion_client
    current_database_id = database_id
    current_parent_page_id = parent_page_id
    current_destinations = destinations
    skip_all_remaining_notion = False

    if current_notion_client is None:
        return 0, ["Notion client is not configured or disabled"]

    def _print(msg: str) -> None:
        if callable(console_print):
            console_print(msg)
        else:
            print(msg)

    for item in items:
        if skip_all_remaining_notion:
            item.is_synced = False
            continue

        if item.classification in SYNCABLE_CLASSIFICATIONS and item.cluster_tag in approved_cluster_keys:
            target_id, _ = resolve_destination(
                classification=item.classification,
                destinations=current_destinations,
                database_id=current_database_id,
                parent_page_id=current_parent_page_id,
            )
            if not target_id:
                item.is_synced = False
                errors.append(f"Destination ID not configured for category {item.classification}")
                continue

            max_attempts = 2 if callable(prompt_func) else 1
            for attempt in range(max_attempts):
                try:
                    success = sync_screenshot_to_notion(
                        item=item,
                        notion_client=current_notion_client,
                        parent_page_id=current_parent_page_id,
                        database_id=current_database_id,
                        destinations=current_destinations,
                        schema_cache=schema_cache,
                        raise_on_error=True,
                        llm=llm,
                        page_cache=page_cache,
                        consolidate_pages=consolidate_pages,
                        reasoning_llm=reasoning_llm,
                        fallback_reasoning_llm=fallback_reasoning_llm,
                    )
                    if success:
                        synced_count += 1
                        break
                    else:
                        errors.append(f"Failed to sync {item.filename} to Notion target {target_id}")
                        break
                except Exception as sync_err:
                    err_text = str(sync_err).lower()
                    is_unauth = any(k in err_text for k in ("unauthorized", "401", "invalid_token", "restricted_service"))
                    is_not_found = any(k in err_text for k in ("object_not_found", "404", "could not find", "validation_error"))

                    if attempt == 0 and callable(prompt_func) and (is_unauth or is_not_found):
                        if is_unauth:
                            _print(f"Failed to send {item.filename} to Notion: Invalid or unauthorized Notion API token.")
                            new_token = prompt_func(
                                "Enter corrected Notion API token (or press Enter to finish without passing to Notion): "
                            ).strip()
                            if new_token:
                                try:
                                    from notion_client import Client
                                    current_notion_client = Client(auth=new_token)
                                    schema_cache.clear()
                                    continue
                                except Exception as init_err:
                                    errors.append(f"Failed to initialize Notion client with new token: {init_err}")
                            else:
                                item.is_synced = False
                                errors.append(f"Skipped Notion sync for {item.filename} (finished without passing to Notion).")
                                skip_all_remaining_notion = True
                                break
                        elif is_not_found:
                            _print(f"Failed to send {item.filename} to Notion target {target_id}: Target not found or no access.")
                            new_target = prompt_func(
                                f"Check if the key is correct or enter the correct database/page ID for {item.classification} (press Enter to finish without passing to Notion): "
                            ).strip()
                            if new_target:
                                if item.classification == "WORK_NOTES":
                                    current_database_id = new_target
                                    if current_destinations is not None:
                                        current_destinations.work_notes_database_id = new_target
                                elif current_destinations is not None:
                                    if item.classification == "DUE_DILIGENCE":
                                        current_destinations.due_diligence_page_id = new_target
                                    elif item.classification == "BRAINSTORM":
                                        current_destinations.brainstorm_page_id = new_target
                                else:
                                    current_database_id = new_target
                                schema_cache.clear()
                                continue
                            else:
                                item.is_synced = False
                                errors.append(f"Skipped Notion sync for {item.filename} (finished without passing to Notion).")
                                skip_all_remaining_notion = True
                                break

                    item.is_synced = False
                    errors.append(f"Failed to sync {item.filename} to Notion target {target_id}: {sync_err}")
                    break
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

