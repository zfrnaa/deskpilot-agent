# Design Spec: Vision Triage + LangGraph ReAct Reasoning Pipeline

**Date:** 2026-09-13  
**Status:** DRAFT / UNDER REVIEW  
**Scope:** `deskpilot/agent_tasks/screenshot_agent`, `deskpilot/config.py`, `deskpilot/cli.py`

---

## 1. Problem & Motivation

Currently, the screenshot agent relies on a single model instance (`selected_llm`) for both visual OCR/classification and Notion page similarity matching. When using local models via Ollama (`minicpm-v`), matching fails or behaves inconsistently because:
1. `minicpm-v` is an image-focused model, not an instruction/reasoning model for complex context matching.
2. The matching step only inspects raw database page titles, ignoring subjects, categories, and page context.
3. Strict string matching on LLM output causes valid matches with formatting variations to fail.
4. If Gemini hits a rate limit (`429 RESOURCE_EXHAUSTED`), fallback routing lacks a dedicated local reasoning engine.

## 2. Proposed Architecture

We adopt a two-stage decoupled architecture:

```text
[ Screenshot File ]
        │
        ▼
(Node 1: Vision / OCR Triage)
  • Primary: Gemini Vision (`gemini-3.8-flash`)
  • Fallback on 429 / offline: Ollama MiniCPM-V (`minicpm-v`)
  • Output: { title, cluster_tag (subject), rationale, extracted_text }
        │
        ▼
(Node 2: LangGraph ReAct Agent for Notion Consolidation)
  • Primary: Gemini (`gemini-3.8-flash`)
  • Fallback on 429 / offline: Ollama (`qwen2.5:3b`)
  • Exposed Tools:
      1. search_notion(query: str, subject: str = "") -> list[dict]
         - Searches Notion database pages matching query or subject.
         - Returns page ID, title, and subject tags.
      2. append_to_page(page_id: str, section_title: str, rationale: str, file_upload_id: str) -> bool
         - Appends section heading, rationale, and uploaded image block to an existing page.
      3. create_database_page(title: str, subject: str, rationale: str, file_upload_id: str) -> str
         - Creates a new page if no existing page matches context.
        │
        ▼
(Node 3: Cleanup / Local Deletion)
  • Deletes local screenshot file ONLY if confirmed synced (append or create succeeded).
```

---

## 3. Configuration Changes

In `deskpilot/config.py`:
```python
class OllamaConfig(BaseModel):
    """Configuration for local Ollama models."""
    model: str = "minicpm-v"                 # Vision model for OCR & image triage
    reasoning_model: str = "qwen2.5:3b"      # Local reasoning model for ReAct agent
    url: str = "http://localhost:11434"
```

In `deskpilot/agent_tasks/screenshot_agent/vision.py`:
- Add `get_ollama_reasoning_llm(ollama_model: str = "qwen2.5:3b", ollama_url: str = "http://localhost:11434")`.

---

## 4. ReAct Reasoning Workflow & Tools

### ReAct Agent Execution in `notion_sync.py` / `graph.py`
For each approved screenshot item:
1. First, upload the screenshot file to Notion (`upload_screenshot_to_notion`) to obtain `file_upload_id` (if enabled/available).
2. The ReAct agent receives:
   - Screenshot Title
   - Screenshot Subject / Category
   - Rationale & Extracted Text
   - File Upload ID
3. The ReAct Agent executes a thought/action cycle:
   - Calls `search_notion` to inspect pages in the database matching the subject or title keywords.
   - Evaluates whether the screenshot conceptually belongs inside an existing page.
   - If a relevant page exists: calls `append_to_page(page_id=..., ...)`.
   - If no existing page matches: calls `create_database_page(title=..., subject=..., ...)`.
4. Graceful Fallbacks:
   - If tool calling or LLM invocation raises any error, fallback cleanly to existing deterministic creation (`create_database_page_payload` / `pages.create`).
   - If Gemini raises a `429 RESOURCE_EXHAUSTED` or quota error during ReAct execution, it automatically retries with local `qwen2.5:3b`.

---

## 5. Dual Model Routing in `cli.py`

When selecting models:
- **Default / Cloud:**
  - `vision_llm` = Gemini Vision (`gemini-3.8-flash`)
  - `reasoning_llm` = Gemini (`gemini-3.8-flash`)
- **Fallback / Local (when Gemini quota exhausted or user chooses Ollama):**
  - `vision_llm` = Ollama `minicpm-v`
  - `reasoning_llm` = Ollama `qwen2.5:3b`

---

## 6. Verification Plan

1. **Unit Tests:**
   - Test `get_ollama_reasoning_llm` initialization and configuration.
   - Test ReAct tools (`search_notion`, `append_to_page`, `create_database_page`) with mock Notion client.
   - Test ReAct agent decision path: correctly calls `append_to_page` when matching page found, and `create_database_page` when no match found.
   - Test automatic 429 quota fallback from Gemini to `qwen2.5:3b`.
2. **Regression Check:**
   - Run `uv run pytest` to ensure all existing tests pass.
