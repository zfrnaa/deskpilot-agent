# Notion Image Upload and Page Consolidation Design Specification

**Date:** 2026-09-12  
**Topic:** Direct screenshot image uploading to Notion and topic-based page consolidation  

## Overview
Currently, the screenshot triage agent synchronizes screenshots to Notion by creating or appending text-only blocks (title heading and category/rationale paragraph) without uploading or attaching the actual screenshot image. Furthermore, every screenshot synced to a database creates a separate database page, leading to page clutter even when screenshots share a common subject.

This design adds:
1. Native screenshot image upload to Notion using Notion's `file_uploads` API and embedding image blocks below the details.
2. Smart deduplication & consolidation: matching incoming screenshots against existing pages in the target database using exact/token title similarity followed by selected LLM semantic classification (Gemini or Ollama), appending new screenshots to existing pages rather than duplicating pages unnecessarily.

---

## 1. Notion Image Upload Architecture

### 1.1 Upload Mechanism (`upload_screenshot_to_notion`)
- Uses `notion_client.file_uploads`:
  1. `file_uploads.create(filename=..., content_type="image/png" | "image/jpeg")`
  2. `file_uploads.send(file_upload_id, file=(filename, binary_data, content_type))`
  3. Returns `file_upload_id`.
- Handled defensively: if the file cannot be read, format is unsupported, or upload fails, logs a warning and returns `None` so the sync still succeeds with text metadata rather than crashing.

### 1.2 Block Insertion
- When `file_upload_id` is available, appends an image block directly below the details paragraph:
```python
{
    "object": "block",
    "type": "image",
    "image": {
        "type": "file_upload",
        "file_upload": {"id": file_upload_id},
    },
}
```

---

## 2. Existing Page Discovery & Consolidation

### 2.1 Database Page Discovery (`fetch_existing_database_pages`)
- For a target database (or data source), query recent active pages:
  - Supports both direct database queries and Notion data sources (`data_sources.query` / `search`).
  - Extracts mapping of `page_id` to cleaned `title`.
  - Caches results for the current triage run to avoid repeated round-trips.

### 2.2 Matching Engine (`match_existing_page`)
- **Stage 1: Normalized Token Overlap / Containment**
  - Standardizes title casing, strips punctuation.
  - If title exact matches or has >= 75% token overlap with an existing page, select that page immediately.
- **Stage 2: LLM Verification (Gemini or local Ollama)**
  - If no clear exact title match exists, supply the candidate titles and the screenshot title/rationale to the selected LLM (`llm.invoke`).
  - Prompt asks whether the screenshot belongs to an existing page topic or needs a new page.
  - Returns matched `page_id` or `None`.

### 2.3 Appending to Existing Pages
- If a match is found:
  - Appends to `matched_page_id` using `notion_client.blocks.children.append`:
    1. Divider block (`{"type": "divider", "divider": {}}`)
    2. Sub-heading (`heading_3`) with the specific screenshot note title / timestamp.
    3. Details paragraph (Category, Rationale, Source Screenshot).
    4. Image block with the uploaded screenshot.
- If no match is found:
  - Creates a new database page via `pages.create` with properties, heading, details, and image block.

---

## 3. Error Handling & Constraints
- Never delete a screenshot locally unless `is_synced == True` is confirmed.
- Reuses whichever LLM was selected by the user (Gemini or Ollama).
- Works with standalone Notion pages (`due_diligence_page_id`, `brainstorm_page_id`) as well as databases (`work_notes_database_id`).
- 100% test coverage with mocks for Notion API and LLM invocation.
