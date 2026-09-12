# Vision Model Upgrade & Resilient Parsing Spec

## Context
During screenshot triage (`cleanup_synced` and triage flows), desktop captures failed to sync to Notion because the local vision model (`bakllava`) produced responses that caused:
`Error: Could not parse JSON response from vision model`.
When parsing fails, items default to `LOCAL_KEEP`, which excludes them from Notion sync. Additionally, `bakllava` has inferior OCR, reasoning, and JSON instruction adherence on modern hardware.

## Goals
1. Upgrade the local vision model in Ollama to `minicpm-v` and remove `bakllava`.
2. Update codebase defaults and CLI routing to use `minicpm-v`.
3. Implement a resilient multi-stage response parser in `vision.py` that rescues valid classifications even when models return malformed or conversational JSON.

---

## 1. Model Management & Configuration Changes
- **Ollama Commands**:
  - Pull `minicpm-v`: `ollama run/pull minicpm-v`
  - Remove `bakllava`: `ollama rm bakllava`
- **Config & Code Updates**:
  - `config.yaml`: Update `ollama.model` to `minicpm-v`.
  - `deskpilot/config.py`: Update default `OllamaConfig.model` from `"bakllava"` to `"minicpm-v"`.
  - `deskpilot/cli.py`: Update console status messages and model lookup defaults.
  - `deskpilot/agent_tasks/screenshot_agent/vision.py`: Update default parameter values (`ollama_model="minicpm-v"`), fallback model references, and prompt formatting instructions.
  - Update unit/integration tests in `tests/test_cli.py`, `tests/test_config.py`, and `tests/test_screenshot_agent.py` to reflect the new default.

---

## 2. Resilient Multi-Stage Parsing (`deskpilot/agent_tasks/screenshot_agent/vision.py`)
Replace the brittle `json.loads` block with a layered parsing helper `parse_vision_response(content: str) -> dict[str, str]`:

1. **Stage 1: Strict JSON & Markdown Stripping**
   - Strip code fence blocks (```json ... ```).
   - Attempt direct `json.loads`.
   - If that fails, extract substring between outermost `{` and `}` and attempt `json.loads`.

2. **Stage 2: Regex Key-Value Extraction**
   - If JSON decoding fails (e.g. unescaped quotes in title or rationale, unclosed brackets, trailing commas):
     - Extract `classification` via `re.search(r'"classification"\s*:\s*"([^"]+)"', raw_text, re.I)`
     - Extract `title` via `re.search(r'"title"\s*:\s*"([^"]+)"', raw_text, re.I)`
     - Extract `cluster_tag` via `re.search(r'"cluster_tag"\s*:\s*"([^"]+)"', raw_text, re.I)`
     - Extract `rationale` via `re.search(r'"rationale"\s*:\s*"([^"]+)"', raw_text, re.I)`

3. **Stage 3: Heuristic Classification Fallback**
   - If Stage 1 & 2 fail to find a valid classification:
     - Check if the text contains explicit tokens: `WORK_NOTES`, `DUE_DILIGENCE`, `BRAINSTORM`, `LOCAL_KEEP`.
     - Infer title and summary from first non-empty lines if available.
   - If no valid classification is found or an exception occurs, fallback safely to `LOCAL_KEEP` with an informative rationale.

---

## Verification Plan
- Verify `ollama list` confirms `minicpm-v` is installed and `bakllava` is removed.
- Run `pytest tests/test_screenshot_agent.py tests/test_config.py tests/test_cli.py` to confirm all tests pass and existing mocking logic stays green.
- Add unit tests for `parse_vision_response` testing malformed JSON, markdown fences, unescaped quotes, and heuristic fallback.
