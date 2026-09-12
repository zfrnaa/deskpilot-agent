"""Multimodal vision triage module for Desktop screenshots using Gemini."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from PIL import Image

from deskpilot.agent_tasks.screenshot_agent.state import ScreenshotItem

try:
    from langsmith import traceable
except ImportError:
    # Graceful fallback decorator if langsmith is not installed
    def traceable(name_or_fn=None, **kwargs):  # type: ignore[no-untyped-def]
        if callable(name_or_fn):
            return name_or_fn
        return lambda fn: fn


def encode_image_to_base64(image_path: Path) -> str:
    """Validate image file with Pillow and encode its content to a base64 string."""
    resolved = image_path.expanduser().resolve()
    with Image.open(resolved) as img:
        img.verify()

    with open(resolved, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _suppress_afc_warning() -> None:
    """Suppress noisy google.genai AFC warning emitted by langchain-google-genai generate_content calls."""
    try:
        from google.genai import models

        models.Models._logged_afc_warning = True
    except Exception:
        pass


def get_default_vision_llm(
    gemini_api_key: str | None = None,
    model: str = "gemini-3.8-flash",
) -> Any:
    """Lazy-load and instantiate Google GenAI Chat model for multimodal vision triage."""
    key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None

    _suppress_afc_warning()

    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=key,
        temperature=0.1,
    )


@traceable(name="classify_screenshot")
def classify_screenshot(
    item: ScreenshotItem,
    llm: Any = None,
    fallback_tag: str = "General",
) -> ScreenshotItem:
    """Classify a single screenshot using the vision model or mock callable.

    Categorizes the screenshot into WORK_NOTES, DUE_DILIGENCE, BRAINSTORM, or LOCAL_KEEP,
    extracts a concise title, cluster tag, and rationale. Falls back to LOCAL_KEEP on any error.
    """
    if llm is None:
        llm = get_default_vision_llm()

    if llm is None:
        item.classification = "LOCAL_KEEP"
        item.rationale = "Vision LLM not configured (missing GEMINI_API_KEY)"
        return item

    valid_classifications = {"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "LOCAL_KEEP", "NOTION_NOTE"}

    # Direct callable support for custom functions or test mocks
    if callable(llm) and not hasattr(llm, "invoke"):
        try:
            res = llm(item)
            if isinstance(res, ScreenshotItem):
                return res
            if isinstance(res, dict):
                cls_val = res.get("classification", item.classification)
                if cls_val in valid_classifications:
                    item.classification = cls_val
                item.title = res.get("title", item.title)
                item.cluster_tag = res.get("cluster_tag", item.cluster_tag)
                item.rationale = res.get("rationale", item.rationale)
                return item
        except Exception as e:
            item.classification = "LOCAL_KEEP"
            item.rationale = f"Error in vision callable: {e}"
            return item

    # LangChain ChatModel invocation
    if hasattr(llm, "invoke"):
        try:
            b64_str = encode_image_to_base64(item.path)
            mime_type = "image/png" if item.path.suffix.lower() == ".png" else "image/jpeg"

            from langchain_core.messages import HumanMessage

            prompt = (
                "You are an AI assistant triaging Windows desktop screenshots. Analyze this screenshot.\n"
                "Determine the appropriate classification destination for this image:\n"
                "- WORK_NOTES: Useful work reference material, diagrams, architecture charts, receipts/invoices, "
                "cheat sheets, code snippets, reading notes, articles, or documentation to preserve.\n"
                "- DUE_DILIGENCE: Due diligence questionnaires, audit forms, compliance checklists, vendor assessments, "
                "security reviews, or governance sheets.\n"
                "- BRAINSTORM: Brainstorming sessions, whiteboards, mind maps, ideation notes, draft ideas, or product concepts.\n"
                "- LOCAL_KEEP: Transient desktop snips, personal photos, accidental captures, video game frames, "
                "or temporary scratch that should not be sent to Notion.\n\n"
                "Return a strict JSON object with this format:\n"
                "{\n"
                '  "classification": "WORK_NOTES" | "DUE_DILIGENCE" | "BRAINSTORM" | "LOCAL_KEEP",\n'
                '  "title": "A short, descriptive title (3-7 words)",\n'
                '  "cluster_tag": "A category tag such as Work, Dev, Receipts, Architecture, Reading, Gaming, Personal",\n'
                '  "rationale": "One-line rationale explaining the classification"\n'
                "}"
            )

            message = HumanMessage(
                content=[
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64_str}"},
                    },
                ]
            )

            response = llm.invoke([message])
            content = getattr(response, "content", response)
            if isinstance(content, list):
                parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                content = "\n".join(parts)
            elif not isinstance(content, str):
                content = str(content)

            # Strip markdown code blocks
            raw_text = content.strip()
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_text = "\n".join(lines).strip()

            parsed = None
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw_text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))

            if isinstance(parsed, dict):
                cls_val = parsed.get("classification", "LOCAL_KEEP")
                item.classification = cls_val if cls_val in valid_classifications else "LOCAL_KEEP"
                item.title = str(parsed.get("title", item.filename))
                item.cluster_tag = str(parsed.get("cluster_tag", fallback_tag))
                item.rationale = str(parsed.get("rationale", ""))
            else:
                item.classification = "LOCAL_KEEP"
                item.rationale = "Could not parse JSON response from vision model"
        except Exception as e:
            item.classification = "LOCAL_KEEP"
            item.cluster_tag = fallback_tag
            item.rationale = f"Error during vision classification: {e}"

    return item


@traceable(name="triage_screenshots")
def triage_screenshots(
    items: list[ScreenshotItem],
    llm: Any = None,
) -> tuple[list[ScreenshotItem], list[str]]:
    """Triage a list of screenshots and collect any processing errors."""
    updated: list[ScreenshotItem] = []
    errors: list[str] = []

    for item in items:
        try:
            classified = classify_screenshot(item, llm=llm)
            updated.append(classified)
        except Exception as e:
            item.classification = "LOCAL_KEEP"
            item.rationale = f"Unexpected failure in triage: {e}"
            updated.append(item)
            errors.append(f"Failed to triage {item.filename}: {e}")

    return updated, errors
