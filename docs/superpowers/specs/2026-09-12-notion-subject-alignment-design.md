# Notion Existing Subject Classification and Alignment Design Specification

**Date:** 2026-09-12  
**Topic:** Classify screenshot categories into existing Notion Subject/Tag options before generating new ones  

## Overview
When triaging screenshots, the vision LLM currently chooses a free-form `cluster_tag` (such as `"Work"`, `"Dev"`, `"Receipts"`). In Notion databases (such as WorkNote), users already maintain predefined taxonomy options on multi-select or select properties (e.g. `Subject: ["Python & AI Engineering", "Backend/Database", "CyberSec", "WEBDEV", "Microsoft", "Google", "Design", ...]`).

Allowing unconstrained tags creates category fragmentation and bypasses the user's existing organizational structure. This design discovers the existing options configured on the target Notion database property and instructs the LLM (Gemini or Ollama) to categorize into one of these existing subjects first, only minting a new subject if none apply.

---

## 1. Architecture & Components

### 1.1 Notion Subject / Tag Option Introspection
- In `introspect_database_schema`:
  - When matching `tag_prop` (e.g. `"Subject"`), extract its declared option names from the property definition:
    - For `type == "multi_select"`, read `prop["multi_select"]["options"]` -> list of `opt["name"]`.
    - For `type == "select"`, read `prop["select"]["options"]` -> list of `opt["name"]`.
  - Store this in the returned schema dictionary:
    ```python
    "tag_options": ["Python & AI Engineering", "Backend/Database", "CyberSec", ...]
    ```

### 1.2 Threading Subject Options to Vision Triage
- In `run_screenshot_triage`:
  - Prior to running the graph, if `notion_client` is configured, introspect the database schema once or retrieve `tag_options` from the target database.
  - Pass `tag_options` into `create_initial_state(..., tag_options=...)`.
- In `vision_triage` and `classify_screenshot`:
  - Read `available_tags = state.get("tag_options", [])`.
  - If `available_tags` is present, enrich the vision classification prompt:
    > "For `cluster_tag`, you MUST choose from these existing subjects if related: [Python & AI Engineering, Backend/Database, CyberSec, ...]. Only if NONE of these subjects are conceptually relevant, provide a new concise subject."

### 1.3 Post-Processing & Normalization
- In `parse_vision_response` (or `classify_screenshot`):
  - If the model returns a tag that closely matches an existing option (e.g., case-insensitive match, substring match, or stopword-normalized match such as `"Python"` for `"Python & AI Engineering"`), snap it to the exact existing Notion option name.
- When creating the page in Notion (`build_database_page_payload`):
  - Pass the resolved tag into `properties[tag_name]` so Notion maps it to the exact existing option.

---

## 2. Constraints & Edge Cases
- **No Database / Offline / Unconfigured Notion**:
  - If Notion is disabled, unreachable, or has no options configured, fall back smoothly to the existing general classification without errors.
- **New Subject Discovery**:
  - If the screenshot genuinely represents a new topic (e.g. `"Cooking"` or `"Finance"`), the LLM can still return a new tag, preserving flexibility.
- **Model Compatibility**:
  - Works transparently with both Gemini and Ollama.
- **100% Test Pass Rate**:
  - Unit tests with mock Notion client and mock LLM verifying that existing subject options are extracted and prioritized.
