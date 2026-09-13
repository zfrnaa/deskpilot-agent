"""ReAct agent tools and reasoning orchestration for Notion database consolidation."""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, tool

from deskpilot.agent_tasks.screenshot_agent.notion_sync import (
    build_database_page_payload,
    build_page_append_section_blocks,
    fetch_existing_database_pages,
    is_valid_uuid,
)
from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

logger = logging.getLogger(__name__)


def search_notion(
    query: str = "",
    subject: str = "",
    *,
    notion_client: Any,
    database_id: str,
) -> list[dict[str, Any]]:
    """Inspect existing Notion pages in the target database.

    Matches titles and/or subject/category tags.
    Returns: [{"id": page_id, "title": title, "subject": subject, "headings": [...]}, ...]
    """
    if notion_client is None or not database_id:
        return []

    try:
        pages_raw: list[dict[str, Any]] = []

        # 1. Check data_sources in database metadata
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

        # 2. Standard databases.query
        if not pages_raw and hasattr(notion_client.databases, "query"):
            try:
                resp = notion_client.databases.query(database_id=database_id, page_size=100)
                if isinstance(resp, dict):
                    pages_raw = resp.get("results") or []
            except Exception as q_err:
                logger.debug("databases.query failed: %s", q_err)

        # 3. Fallback: search endpoint
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
                        p_db_id = parent.get("database_id", "").replace("-", "")
                        clean_target_id = database_id.replace("-", "")
                        if p_db_id and p_db_id == clean_target_id:
                            pages_raw.append(p)
            except Exception as search_err:
                logger.debug("client.search failed: %s", search_err)

        query_lower = query.strip().lower()
        subject_lower = subject.strip().lower()

        results: list[dict[str, Any]] = []
        for page in pages_raw:
            page_id = page.get("id")
            if not page_id:
                continue
            properties = page.get("properties") or {}

            title_text = ""
            tag_text = ""

            for prop_name, prop in properties.items():
                if not isinstance(prop, dict):
                    continue
                p_type = prop.get("type")
                if p_type == "title":
                    parts = [
                        t.get("plain_text", "")
                        for t in (prop.get("title") or [])
                        if isinstance(t, dict) and t.get("plain_text")
                    ]
                    title_text = "".join(parts).strip()
                elif p_type == "select":
                    sel = prop.get("select") or {}
                    tag_text = sel.get("name", "")
                elif p_type == "multi_select":
                    tags = [
                        opt.get("name", "")
                        for opt in (prop.get("multi_select") or [])
                        if isinstance(opt, dict) and opt.get("name")
                    ]
                    tag_text = ", ".join(tags)

            # Filtering logic
            matches_query = True
            if query_lower:
                matches_query = query_lower in title_text.lower() or query_lower in tag_text.lower()

            matches_subject = True
            if subject_lower:
                matches_subject = subject_lower in tag_text.lower() or subject_lower in title_text.lower()

            if matches_query and matches_subject:
                results.append({
                    "id": page_id,
                    "title": title_text,
                    "subject": tag_text,
                    "headings": [],
                })

        return results
    except Exception as exc:
        logger.warning("search_notion failed for database %s: %s", database_id, exc)
        return []


def append_to_page(
    page_id: str,
    section_title: str,
    rationale: str,
    file_upload_id: str = "",
    *,
    notion_client: Any,
) -> bool:
    """Append section heading, rationale paragraph, and image block to target page."""
    if notion_client is None or not page_id:
        return False

    try:
        dummy_item = ScreenshotItem(
            path=ScreenshotItem.model_fields["path"].default or "",
            title=section_title,
            rationale=rationale,
        )
    except Exception:
        from pathlib import Path
        dummy_item = ScreenshotItem(
            path=Path("dummy.png"),
            title=section_title,
            rationale=rationale,
        )

    resolved_upload_id = file_upload_id.strip() if file_upload_id and is_valid_uuid(file_upload_id) else None
    blocks = build_page_append_section_blocks(dummy_item, file_upload_id=resolved_upload_id)
    try:
        notion_client.blocks.children.append(
            block_id=page_id,
            children=blocks,
        )
    except Exception as append_err:
        err_text = str(append_err).lower()
        if resolved_upload_id and any(kw in err_text for kw in ("file_upload", "validationerror", "uuid", "children")):
            logger.warning(
                "append_to_page failed with image block (%s). Retrying text-only section append: %s",
                resolved_upload_id,
                append_err,
            )
            fallback_blocks = build_page_append_section_blocks(dummy_item, file_upload_id=None)
            notion_client.blocks.children.append(
                block_id=page_id,
                children=fallback_blocks,
            )
        else:
            raise append_err
    return True


def create_database_page(
    title: str,
    subject: str,
    rationale: str,
    file_upload_id: str = "",
    *,
    notion_client: Any,
    database_id: str,
    schema: dict[str, Any],
) -> str:
    """Create a new database page with given title, subject tag, rationale, and image.

    Returns the new page ID.
    """
    from pathlib import Path
    item = ScreenshotItem(
        path=Path("screenshot.png"),
        title=title,
        cluster_tag=subject,
        rationale=rationale,
    )
    resolved_upload_id = file_upload_id.strip() if file_upload_id and is_valid_uuid(file_upload_id) else None
    payload = build_database_page_payload(item, database_id, schema, file_upload_id=resolved_upload_id)
    try:
        created_page = notion_client.pages.create(**payload)
    except Exception as create_err:
        err_text = str(create_err).lower()
        if resolved_upload_id and any(kw in err_text for kw in ("file_upload", "validationerror", "uuid", "children")):
            logger.warning(
                "create_database_page failed with image block (%s). Retrying page creation without image: %s",
                resolved_upload_id,
                create_err,
            )
            fallback_payload = build_database_page_payload(item, database_id, schema, file_upload_id=None)
            created_page = notion_client.pages.create(**fallback_payload)
        else:
            raise create_err

    if isinstance(created_page, dict):
        return created_page.get("id") or ""
    return getattr(created_page, "id", "") or ""


def create_notion_react_tools(
    notion_client: Any,
    database_id: str,
    schema: dict[str, Any],
    default_file_upload_id: str = "",
) -> list[StructuredTool]:
    """Create bound LangChain StructuredTool instances for the Notion ReAct agent."""

    def _search_notion(query: str = "", subject: str = "") -> list[dict[str, Any]]:
        """Search Notion database for existing pages matching title or subject."""
        return search_notion(
            query=query,
            subject=subject,
            notion_client=notion_client,
            database_id=database_id,
        )

    def _append_to_page(
        page_id: str,
        section_title: str,
        rationale: str,
        file_upload_id: str = "",
    ) -> bool:
        """Append section heading, details, and screenshot image to an existing Notion page."""
        effective_upload_id = file_upload_id if (file_upload_id and is_valid_uuid(file_upload_id)) else default_file_upload_id
        return append_to_page(
            page_id=page_id,
            section_title=section_title,
            rationale=rationale,
            file_upload_id=effective_upload_id,
            notion_client=notion_client,
        )

    def _create_database_page(
        title: str,
        subject: str,
        rationale: str,
        file_upload_id: str = "",
    ) -> str:
        """Create a brand new database page for this screenshot."""
        effective_upload_id = file_upload_id if (file_upload_id and is_valid_uuid(file_upload_id)) else default_file_upload_id
        return create_database_page(
            title=title,
            subject=subject,
            rationale=rationale,
            file_upload_id=effective_upload_id,
            notion_client=notion_client,
            database_id=database_id,
            schema=schema,
        )

    return [
        StructuredTool.from_function(
            func=_search_notion,
            name="search_notion",
            description="Search existing Notion database pages matching query or subject tag.",
        ),
        StructuredTool.from_function(
            func=_append_to_page,
            name="append_to_page",
            description="Append content and image as a new section to an existing Notion page.",
        ),
        StructuredTool.from_function(
            func=_create_database_page,
            name="create_database_page",
            description="Create a new Notion page in the database.",
        ),
    ]


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Check if exception is a 429 / quota exhaustion error."""
    msg = str(exc).lower()
    return any(k in msg for k in ("429", "resource_exhausted", "quota", "rate limit"))


def run_react_consolidation_agent(
    item: ScreenshotItem,
    notion_client: Any,
    database_id: str,
    schema: dict[str, Any],
    llm: Any,
    fallback_llm: Any = None,
    file_upload_id: str = "",
) -> dict[str, Any]:
    """Execute ReAct tool-calling agent to consolidate or create Notion database pages.

    Returns: {"action": "append" | "create", "page_id": page_id, "synced": bool, "error": str | None}
    """
    tools = create_notion_react_tools(
        notion_client=notion_client,
        database_id=database_id,
        schema=schema,
        default_file_upload_id=file_upload_id,
    )
    tools_map = {t.name: t for t in tools}

    system_prompt = (
        "You are an intelligent Notion Knowledge Base Consolidation Agent.\n"
        "Your task is to analyze a new screenshot note and decide whether to:\n"
        "1. Append it as a new section into an existing relevant page in the database (via append_to_page),\n"
        "2. OR create a new database page (via create_database_page) if no existing page is a good topical match.\n\n"
        "Guidelines:\n"
        "- First, use search_notion to find any existing pages related to the screenshot title or subject tag.\n"
        "- If a closely related page is found (e.g. same ongoing project, weekly notes, or topic), append to it.\n"
        "- If no matching page is found, call create_database_page.\n"
        f"- The file_upload_id for this screenshot is '{file_upload_id}'. Pass it to the tool call."
    )

    user_content = (
        f"Screenshot to consolidate:\n"
        f"Title: {item.title}\n"
        f"Subject / Tag: {item.cluster_tag}\n"
        f"Rationale: {item.rationale}\n"
        f"File Upload ID: {file_upload_id}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    def _execute_react_loop(active_llm: Any) -> dict[str, Any]:
        llm_with_tools = active_llm.bind_tools(tools)
        current_messages = list(messages)
        max_turns = 5
        action: str | None = None
        target_page_id: str | None = None

        for _ in range(max_turns):
            ai_msg = llm_with_tools.invoke(current_messages)
            current_messages.append(ai_msg)

            if not ai_msg.tool_calls:
                break

            for tool_call in ai_msg.tool_calls:
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("args") or {}
                tool_id = tool_call.get("id") or "call_id"

                if tool_name in tools_map:
                    tool_func = tools_map[tool_name]
                    tool_result = tool_func.invoke(tool_args)

                    if tool_name == "append_to_page":
                        action = "append"
                        target_page_id = tool_args.get("page_id")
                    elif tool_name == "create_database_page":
                        action = "create"
                        target_page_id = str(tool_result) if tool_result else ""

                    current_messages.append(
                        ToolMessage(
                            content=str(tool_result),
                            tool_call_id=tool_id,
                        )
                    )
                else:
                    current_messages.append(
                        ToolMessage(
                            content=f"Unknown tool: {tool_name}",
                            tool_call_id=tool_id,
                        )
                    )

            if action in ("append", "create"):
                break

        if action and target_page_id:
            item.is_synced = True
            return {
                "action": action,
                "page_id": target_page_id,
                "synced": True,
                "error": None,
            }
        return {"action": None, "page_id": None, "synced": False, "error": "No action taken"}

    # Attempt execution with primary LLM, fall back on 429 quota error to fallback_llm
    try:
        result = _execute_react_loop(llm)
        if result["synced"]:
            return result
    except Exception as exc:
        if _is_rate_limit_error(exc) and fallback_llm is not None:
            logger.warning("Primary LLM hit rate limit (429). Retrying with fallback reasoning LLM: %s", exc)
            try:
                result = _execute_react_loop(fallback_llm)
                if result["synced"]:
                    return result
            except Exception as fb_exc:
                logger.error("Fallback reasoning LLM also failed: %s", fb_exc)
        else:
            logger.warning("ReAct consolidation loop failed with error: %s", exc)

    # Fail open / graceful fallback: direct database page creation so screenshot sync is never dropped
    try:
        logger.info("Failing open to direct database page creation for screenshot: %s", item.title)
        resolved_upload_id = file_upload_id.strip() if file_upload_id and is_valid_uuid(file_upload_id) else None
        payload = build_database_page_payload(
            item,
            database_id,
            schema,
            file_upload_id=resolved_upload_id,
        )
        try:
            created_page = notion_client.pages.create(**payload)
        except Exception as create_err:
            err_text = str(create_err).lower()
            if resolved_upload_id and any(kw in err_text for kw in ("file_upload", "validationerror", "uuid", "children")):
                logger.warning("Direct page creation failed with image block. Retrying text-only page: %s", create_err)
                fallback_payload = build_database_page_payload(
                    item,
                    database_id,
                    schema,
                    file_upload_id=None,
                )
                created_page = notion_client.pages.create(**fallback_payload)
            else:
                raise create_err

        page_id = created_page.get("id") if isinstance(created_page, dict) else getattr(created_page, "id", "")
        item.is_synced = True
        return {
            "action": "create",
            "page_id": page_id or "",
            "synced": True,
            "error": None,
        }
    except Exception as fallback_err:
        logger.error("Graceful direct page creation also failed: %s", fallback_err)
        item.is_synced = False
        return {
            "action": "create",
            "page_id": "",
            "synced": False,
            "error": str(fallback_err),
        }
