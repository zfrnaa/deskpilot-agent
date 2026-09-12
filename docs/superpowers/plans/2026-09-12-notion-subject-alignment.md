# Notion Subject Alignment & Taxonomy Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introspect existing Subject/Tag options from the target Notion database schema and feed them to the vision classification model (Gemini or Ollama) so screenshots classify into existing subjects first, only creating new subject tags when no existing option is relevant.

**Architecture:**
1. In `introspect_database_schema` in `notion_sync.py`, extract option names (`tag_options: list[str]`) from the matched `Subject`/tag property (`multi_select` or `select`).
2. Add `tag_options` to `ScreenshotAgentState` and `create_initial_state` in `state.py`.
3. In `run_screenshot_triage` (`graph.py`), discover `tag_options` from the database if Notion is configured and store in initial state.
4. Update `classify_screenshot` and `triage_screenshots` in `vision.py` to accept `available_tags: list[str] = None` and inject them into the vision prompt.
5. In `classify_screenshot` / `parse_vision_response`, normalize and snap close matches to the exact casing and wording of existing Notion options.
6. Add unit tests in `tests/test_screenshot_agent.py` covering schema option extraction, prompt tag injection, and exact-option snapping.

**Tech Stack:** Python 3.11, Notion Client SDK, LangChain, pytest

## Global Constraints
- Only delete screenshots locally if `is_synced == True` is confirmed.
- If Notion is offline or unconfigured, fall back gracefully to general tagging.
- Preserve 100% test pass rate across `uv run pytest`.

---

### Task 1: Add Unit Tests for Schema Tag Options and Prompt Injection

**Files:**
- Modify: `tests/test_screenshot_agent.py`

**Interfaces:**
- Produces: Tests covering:
  - `introspect_database_schema` extracts `tag_options: list[str]` from `multi_select` and `select` properties.
  - `classify_screenshot` injects available tags into prompt and snaps tag to existing option.

- [ ] **Step 1: Write failing tests in `tests/test_screenshot_agent.py`**

```python
def test_introspect_database_schema_extracts_tag_options():
    """Verify introspect_database_schema extracts list of existing tag options."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.notion_sync import introspect_database_schema

    mock_client = MagicMock()
    mock_client.databases.retrieve.return_value = {
        "id": "db_1",
        "properties": {
            "Title": {"type": "title", "id": "title"},
            "Subject": {
                "type": "multi_select",
                "multi_select": {
                    "options": [
                        {"name": "Python & AI Engineering"},
                        {"name": "Backend/Database"},
                        {"name": "CyberSec"},
                    ]
                },
            },
        },
    }

    schema = introspect_database_schema(mock_client, "db_1")
    assert schema["tag_prop"] == "Subject"
    assert schema["tag_options"] == ["Python & AI Engineering", "Backend/Database", "CyberSec"]


def test_classify_screenshot_uses_available_tags_and_snaps(tmp_path: Path):
    """Verify classify_screenshot prompts with available tags and snaps fuzzy match."""
    from unittest.mock import MagicMock
    from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem
    from deskpilot.agent_tasks.screenshot_agent.vision import classify_screenshot

    img = tmp_path / "code.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    item = ScreenshotItem(path=img, filename="code.png")
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = (
        '{"classification": "WORK_NOTES", "title": "PyTorch Guide", "cluster_tag": "python", "rationale": "Deep learning"}'
    )

    available = ["Python & AI Engineering", "CyberSec", "WEBDEV"]
    updated = classify_screenshot(item, llm=mock_llm, available_tags=available)

    assert updated.cluster_tag == "Python & AI Engineering"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_introspect_database_schema_extracts_tag_options or test_classify_screenshot_uses_available_tags_and_snaps" -v`
Expected: FAIL

---

### Task 2: Implement Option Extraction and Tag Snapping in `notion_sync.py` and `vision.py`

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/notion_sync.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/vision.py`

- [ ] **Step 1: Extract options in `introspect_database_schema`**
  - Read `prop[tag_type]["options"]` and populate `"tag_options": [...]`.

- [ ] **Step 2: Add `available_tags` parameter and snapping to `classify_screenshot` and `triage_screenshots`**
  - Include available tags list in LLM prompt instructions.
  - Add fuzzy/substring/token-overlap matcher against `available_tags` in `parse_vision_response` to snap to exact existing option name when applicable.

- [ ] **Step 3: Run unit tests to verify they pass**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_introspect_database_schema_extracts_tag_options or test_classify_screenshot_uses_available_tags_and_snaps" -v`
Expected: PASS

- [ ] **Step 4: Commit Task 2**

```bash
git add deskpilot/agent_tasks/screenshot_agent/ tests/test_screenshot_agent.py
git commit -m "feat(notion): implement Subject option introspection and prompt alignment"
```

---

### Task 3: Wire Tag Options Through State and Graph

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/state.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/graph.py`
- Modify: `tests/test_screenshot_agent.py`

- [ ] **Step 1: Update `state.py`**
  - Add `tag_options: list[str]` to `ScreenshotAgentState` and `create_initial_state`.

- [ ] **Step 2: Update `graph.py`**
  - In `run_screenshot_triage`: if `notion_client` is configured, call `introspect_database_schema` for `database_id` and pass `tag_options` into `create_initial_state`.
  - In `vision_triage`: extract `tag_options = state.get("tag_options", [])` and pass `available_tags=tag_options` to `triage_screenshots`.

- [ ] **Step 3: Run regression tests**

Run: `uv run pytest`
Expected: 100% PASS

- [ ] **Step 4: Commit Task 3**

```bash
git add deskpilot/agent_tasks/screenshot_agent/ tests/
git commit -m "feat(graph): wire Notion Subject options into vision triage state"
```

---

### Task 4: Final Verification
- Run complete test suite: `uv run pytest`
- Verify 100% pass rate.
