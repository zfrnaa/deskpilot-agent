# Vision Triage + LangGraph ReAct Reasoning Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple screenshot visual processing from database consolidation by using a vision model (Gemini Vision / MiniCPM-V) for OCR and a dedicated LangGraph ReAct reasoning agent (Gemini / Qwen2.5:3b) with Notion search and append tools.

**Architecture:** A two-stage pipeline where Node 1 extracts text, proposed title, rationale, and subject from the screenshot image; Node 2 runs a LangGraph ReAct agent equipped with Notion tools (`search_notion`, `append_to_page`, `create_database_page`) to reason about existing pages and decide whether to consolidate or create; Node 3 cleans up local files only if sync succeeded. If Gemini quota is exhausted (429), both stages fall back to local Ollama models (`minicpm-v` and `qwen2.5:3b`).

**Tech Stack:** Python 3.11+, LangGraph, LangChain (`langchain-google-genai`, `langchain-ollama`, `langchain-core`), Notion API client, Pytest.

## Global Constraints
- Only delete screenshots locally if `is_synced == True` is confirmed.
- If ReAct agent or tool call encounters any error, fallback cleanly to creating a new page so screenshots are never dropped.
- Maintain 100% test pass rate across `uv run pytest`.

---

### Task 1: Configuration and Ollama Reasoning LLM Helper

**Files:**
- Modify: `deskpilot/config.py:124-130`
- Modify: `deskpilot/agent_tasks/screenshot_agent/vision.py`
- Test: `tests/test_vision.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `OllamaConfig`
- Produces: `OllamaConfig.reasoning_model`, `get_ollama_reasoning_llm(ollama_model: str, ollama_url: str) -> Any`

- [ ] **Step 1: Write the failing tests**
  - Test that `OllamaConfig` contains `reasoning_model: str = "qwen2.5:3b"`.
  - Test that `get_ollama_reasoning_llm` instantiates `ChatOllama` with the reasoning model.
- [ ] **Step 2: Run pytest to verify test failure**
- [ ] **Step 3: Implement `reasoning_model` in `config.py` and helper in `vision.py`**
- [ ] **Step 4: Run pytest to verify tests pass**
- [ ] **Step 5: Commit changes**

---

### Task 2: Notion ReAct Tools and Consolidation Logic

**Files:**
- Create/Modify: `deskpilot/agent_tasks/screenshot_agent/react_agent.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`
- Test: `tests/test_screenshot_react_agent.py`

**Interfaces:**
- Consumes: `notion_client`, `ScreenshotItem`, database ID, schema
- Produces:
  - Tool `search_notion(query: str, subject: str = "") -> list[dict]`
  - Tool `append_to_page(page_id: str, section_title: str, rationale: str, file_upload_id: str) -> bool`
  - Tool `create_database_page(title: str, subject: str, rationale: str, file_upload_id: str) -> str`
  - Function `run_react_consolidation_agent(item: ScreenshotItem, notion_client: Any, database_id: str, schema: dict, llm: Any, fallback_llm: Any = None) -> dict`

- [ ] **Step 1: Write failing tests for Notion ReAct tools and agent execution**
  - Test `search_notion` returns matching pages with IDs, titles, and subject tags.
  - Test `append_to_page` appends section blocks and image block to target page.
  - Test `create_database_page` creates new database entry when no match found.
  - Test `run_react_consolidation_agent` invokes ReAct loop and records sync result.
  - Test 429 quota exception triggers fallback to `fallback_llm`.
- [ ] **Step 2: Run pytest to verify test failure**
- [ ] **Step 3: Implement tools and `run_react_consolidation_agent`**
- [ ] **Step 4: Run pytest to verify all tests pass**
- [ ] **Step 5: Commit changes**

---

### Task 3: Integration into Screenshot Workflow and CLI

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Modify: `deskpilot/cli.py`
- Test: `tests/test_screenshot_graph.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run_react_consolidation_agent`, `get_ollama_reasoning_llm`, `Settings`
- Produces: Updated `build_screenshot_triage_graph` and CLI Option 1 routing supporting dual models.

- [ ] **Step 1: Write failing tests for dual-model graph execution and fallback**
  - Test graph compiles and executes with `reasoning_llm` parameter.
  - Test CLI Option 1 passes both vision and reasoning LLMs.
- [ ] **Step 2: Run pytest to verify test failure**
- [ ] **Step 3: Wire `reasoning_llm` through `graph.py` and `cli.py`**
- [ ] **Step 4: Run full test suite (`uv run pytest`) to ensure 100% pass**
- [ ] **Step 5: Commit changes**
