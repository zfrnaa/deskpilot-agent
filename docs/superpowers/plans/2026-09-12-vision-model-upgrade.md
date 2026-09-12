# Vision Model Upgrade & Resilient Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade local Ollama vision model from `bakllava` to `minicpm-v`, uninstall `bakllava`, and introduce a resilient multi-stage parser in `vision.py` to prevent parse failures and Notion sync skips.

**Architecture:** 
1. Implement and unit test a multi-stage parser `parse_vision_response` (strict JSON -> regex key-value extraction -> keyword fallback) in `deskpilot/agent_tasks/screenshot_agent/vision.py`.
2. Update default model configuration, fallback routing, and CLI strings across `config.py`, `config.yaml`, `vision.py`, and `cli.py` to use `minicpm-v`.
3. Pull `minicpm-v` in Ollama and remove `bakllava`.
4. Update unit tests in `test_screenshot_agent.py`, `test_config.py`, and `test_cli.py` to assert against `minicpm-v`.

**Tech Stack:** Python 3.11, Ollama CLI, LangChain Core, Pydantic, pytest

## Global Constraints
- Target local vision model: `minicpm-v`
- Old model to remove: `bakllava`
- Preserve valid classification set: `{"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "LOCAL_KEEP"}`
- Always keep tests passing via `uv run pytest`

---

### Task 1: Add Unit Tests for Resilient Multi-Stage Parsing

**Files:**
- Modify: `tests/test_screenshot_agent.py`

**Interfaces:**
- Produces: Tests covering `parse_vision_response` with markdown blocks, conversational preamble, unescaped quotes, and heuristic fallback.

- [ ] **Step 1: Write the failing tests in `tests/test_screenshot_agent.py`**

Add tests to `tests/test_screenshot_agent.py`:
```python
def test_parse_vision_response_strict_json():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = '{"classification": "WORK_NOTES", "title": "System Architecture", "cluster_tag": "Dev", "rationale": "High-level diagram"}'
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "WORK_NOTES"
    assert parsed["title"] == "System Architecture"
    assert parsed["cluster_tag"] == "Dev"
    assert parsed["rationale"] == "High-level diagram"


def test_parse_vision_response_markdown_and_conversational():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = """Here is your classification:
```json
{
  "classification": "BRAINSTORM",
  "title": "Q3 Brainstorming Whiteboard",
  "cluster_tag": "Ideas",
  "rationale": "Sticky notes and diagrams"
}
```
Hope that helps!"""
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "BRAINSTORM"
    assert parsed["title"] == "Q3 Brainstorming Whiteboard"
    assert parsed["cluster_tag"] == "Ideas"


def test_parse_vision_response_regex_fuzzy_extraction_on_broken_json():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    # Malformed JSON (missing closing braces, unescaped quote in rationale)
    raw = '{"classification": "DUE_DILIGENCE", "title": "SOC2 Questionnaire", "cluster_tag": "Compliance", "rationale": "Vendor said "approved" here'
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "DUE_DILIGENCE"
    assert parsed["title"] == "SOC2 Questionnaire"
    assert parsed["cluster_tag"] == "Compliance"


def test_parse_vision_response_heuristic_fallback():
    from deskpilot.agent_tasks.screenshot_agent.vision import parse_vision_response

    raw = "Based on the image, this is clearly a WORK_NOTES screenshot showing terminal commands."
    parsed = parse_vision_response(raw)
    assert parsed["classification"] == "WORK_NOTES"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_parse_vision_response" -v`
Expected: FAIL with `ImportError: cannot import name 'parse_vision_response'`

---

### Task 2: Implement `parse_vision_response` in `vision.py`

**Files:**
- Modify: `deskpilot/agent_tasks/screenshot_agent/vision.py:250-295`

**Interfaces:**
- Produces: `parse_vision_response(content: str, fallback_tag: str = "General", filename: str = "") -> dict[str, str]`

- [ ] **Step 1: Write `parse_vision_response` and integrate into `triage_single_screenshot`**

In `deskpilot/agent_tasks/screenshot_agent/vision.py`:
Implement `parse_vision_response` helper:
```python
def parse_vision_response(
    content: str,
    fallback_tag: str = "General",
    filename: str = "",
) -> dict[str, str]:
    """Parse model response using strict JSON, regex fuzzy matching, or heuristic fallback."""
    valid_classifications = {"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "LOCAL_KEEP"}
    raw_text = content.strip()

    # 1. Strip markdown fences if present
    if "```" in raw_text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
        if match:
            raw_text = match.group(1).strip()

    parsed_dict: dict[str, Any] = {}

    # Stage 1: Strict JSON parsing
    try:
        loaded = json.loads(raw_text)
        if isinstance(loaded, dict):
            parsed_dict = loaded
    except Exception:
        # Try finding outer braces
        brace_match = re.search(r"\{[\s\S]*\}", raw_text)
        if brace_match:
            try:
                loaded = json.loads(brace_match.group(0))
                if isinstance(loaded, dict):
                    parsed_dict = loaded
            except Exception:
                parsed_dict = {}

    # Stage 2: Regex fuzzy key-value extraction if strict parse failed
    if not parsed_dict:
        cls_m = re.search(r'"?classification"?\s*[:=]\s*"?([A-Z_]+)"?', raw_text, re.IGNORECASE)
        title_m = re.search(r'"?title"?\s*[:=]\s*"([^"\n]+)"', raw_text, re.IGNORECASE)
        tag_m = re.search(r'"?cluster_tag"?\s*[:=]\s*"([^"\n]+)"', raw_text, re.IGNORECASE)
        rat_m = re.search(r'"?rationale"?\s*[:=]\s*"([^"\n]+)"', raw_text, re.IGNORECASE)

        if cls_m:
            parsed_dict["classification"] = cls_m.group(1).upper()
        if title_m:
            parsed_dict["title"] = title_m.group(1).strip()
        if tag_m:
            parsed_dict["cluster_tag"] = tag_m.group(1).strip()
        if rat_m:
            parsed_dict["rationale"] = rat_m.group(1).strip()

    # Stage 3: Heuristic token scan if still unclassified
    if "classification" not in parsed_dict or parsed_dict["classification"] not in valid_classifications:
        for candidate in ("WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "LOCAL_KEEP"):
            if candidate in raw_text.upper():
                parsed_dict["classification"] = candidate
                break

    # Build final standardized result
    classification = parsed_dict.get("classification")
    if classification in valid_classifications:
        return {
            "classification": classification,
            "title": str(parsed_dict.get("title") or filename or "Screenshot Note"),
            "cluster_tag": str(parsed_dict.get("cluster_tag") or fallback_tag),
            "rationale": str(parsed_dict.get("rationale") or "Classified from vision response"),
        }

    return {
        "classification": "LOCAL_KEEP",
        "title": filename or "Screenshot Note",
        "cluster_tag": fallback_tag,
        "rationale": "Error: Could not parse JSON response from vision model",
    }
```
Update `triage_single_screenshot` to use `parse_vision_response`.

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_screenshot_agent.py -k "test_parse_vision_response" -v`
Expected: PASS

- [ ] **Step 3: Commit parser improvement**

```bash
git add deskpilot/agent_tasks/screenshot_agent/vision.py tests/test_screenshot_agent.py
git commit -m "feat(vision): add resilient multi-stage JSON parser for screenshot triage"
```

---

### Task 3: Update Default Model Configuration & Fallback to `minicpm-v`

**Files:**
- Modify: `config.yaml`
- Modify: `deskpilot/config.py`
- Modify: `deskpilot/agent_tasks/screenshot_agent/vision.py`
- Modify: `deskpilot/cli.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_screenshot_agent.py`

- [ ] **Step 1: Update configuration and references from `bakllava` to `minicpm-v`**
  - Update `config.yaml`: `ollama.model: minicpm-v`
  - Update `deskpilot/config.py`: `model: str = "minicpm-v"`
  - Update `deskpilot/cli.py`: Console log messages replacing `"bakllava"` with `"minicpm-v"`
  - Update `deskpilot/agent_tasks/screenshot_agent/vision.py`: default arguments `ollama_model: str = "minicpm-v"` and fallback `ollama_model="minicpm-v"`
  - Update tests checking `"bakllava"` to check `"minicpm-v"`

- [ ] **Step 2: Run full test suite to verify all unit tests pass**

Run: `uv run pytest tests/test_screenshot_agent.py tests/test_config.py tests/test_cli.py`
Expected: All tests PASS

- [ ] **Step 3: Commit configuration changes**

```bash
git add config.yaml deskpilot/ tests/
git commit -m "feat: upgrade default Ollama vision model to minicpm-v"
```

---

### Task 4: Ollama Model Management (Pull `minicpm-v` & Remove `bakllava`)

**Commands:**
- [ ] **Step 1: Pull `minicpm-v` in Ollama**
  Run: `ollama pull minicpm-v`
- [ ] **Step 2: Remove `bakllava` in Ollama**
  Run: `ollama rm bakllava`
- [ ] **Step 3: Verify with `ollama list`**
  Run: `ollama list`
  Expected: `minicpm-v` is present and `bakllava` is not present.

---

### Task 5: Final End-to-End Verification

- [ ] **Step 1: Run complete repository test suite**
  Run: `uv run pytest`
  Expected: 100% PASS
