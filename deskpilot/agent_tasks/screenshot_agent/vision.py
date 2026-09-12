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


def get_ollama_vision_llm(
    ollama_model: str = "minicpm-v",
    ollama_url: str = "http://localhost:11434",
) -> Any:
    """Instantiate and return Ollama Chat model for vision classification."""
    try:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=ollama_model or "minicpm-v",
            base_url=ollama_url or "http://localhost:11434",
            temperature=0.1,
        )
    except ImportError:
        try:
            from langchain_community.chat_models import ChatOllama

            return ChatOllama(
                model=ollama_model or "minicpm-v",
                base_url=ollama_url or "http://localhost:11434",
                temperature=0.1,
            )
        except Exception:
            return None


def check_gemini_quota(
    gemini_api_key: str | None = None,
    model: str = "gemini-3.8-flash",
) -> bool:
    """Pre-flight check whether Gemini API key is valid and has available quota/credits.

    Sends a lightweight invocation ("ping") with a strict timeout.
    Returns True if response received successfully, False if quota/credits exhausted,
    rate-limited, model unavailable, or key missing/invalid.
    """
    key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return False

    try:
        _suppress_afc_warning()
        from langchain_google_genai import ChatGoogleGenerativeAI

        probe_llm = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=key,
            temperature=0.0,
            timeout=8,
            max_retries=1,
        )
        # Perform minimal probe
        probe_llm.invoke("ping")
        return True
    except Exception as e:
        err_msg = str(e).lower()
        quota_keywords = (
            "quota",
            "resource_exhausted",
            "429",
            "exhausted",
            "credit",
            "rate limit",
            "not_found",
            "invalid",
            "unauthenticated",
            "permission",
            "403",
            "404",
        )
        if any(kw in err_msg for kw in quota_keywords):
            return False
        # Treat any network failure or unexpected exception during probe as unavailable
        return False


def get_default_vision_llm(
    gemini_api_key: str | None = None,
    model: str = "gemini-3.8-flash",
    ollama_model: str | None = None,
    ollama_url: str | None = None,
) -> Any:
    """Lazy-load and instantiate vision Chat model (Gemini or Ollama) for multimodal vision triage."""
    key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if key:
        _suppress_afc_warning()
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=key,
            temperature=0.1,
        )

    # If no Gemini key is provided, check if Ollama is configured
    if ollama_model or ollama_url:
        return get_ollama_vision_llm(
            ollama_model=ollama_model or "minicpm-v",
            ollama_url=ollama_url or "http://localhost:11434",
        )

    return None


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
        item.rationale = "Vision LLM not configured (missing GEMINI_API_KEY and Ollama configuration)"
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

            try:
                response = llm.invoke([message])
            except Exception as invoke_err:
                err_msg = str(invoke_err).lower()
                is_gemini_llm = "google" in llm.__class__.__module__.lower() or "gemini" in llm.__class__.__name__.lower()
                is_quota = any(
                    kw in err_msg
                    for kw in ("quota", "resource_exhausted", "429", "exhausted", "credit", "rate limit")
                )

                if is_gemini_llm and is_quota:
                    # Attempt automatic fallback to Ollama MiniCPM-V
                    ollama_llm = get_default_vision_llm(gemini_api_key="", ollama_model="minicpm-v")
                    if ollama_llm is not None:
                        try:
                            response = ollama_llm.invoke([message])
                        except Exception as fallback_err:
                            raise RuntimeError(
                                f"Gemini failed ({invoke_err}); Ollama fallback also failed: {fallback_err}"
                            ) from invoke_err
                    else:
                        raise invoke_err
                else:
                    raise invoke_err

            content = getattr(response, "content", response)
            if isinstance(content, list):
                parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                content = "\n".join(parts)
            elif not isinstance(content, str):
                content = str(content)

            parsed = parse_vision_response(
                content=content,
                fallback_tag=fallback_tag,
                filename=item.filename,
            )
            item.classification = parsed["classification"]
            item.title = parsed["title"]
            item.cluster_tag = parsed["cluster_tag"]
            item.rationale = parsed["rationale"]
        except Exception as e:
            item.classification = "LOCAL_KEEP"
            item.cluster_tag = fallback_tag
            item.rationale = f"Error during vision classification: {e}"

    return item


def parse_vision_response(
    content: str,
    fallback_tag: str = "General",
    filename: str = "",
) -> dict[str, str]:
    """Parse model response using strict JSON, regex fuzzy matching, or heuristic fallback."""
    valid_classifications = {"WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "LOCAL_KEEP", "NOTION_NOTE"}
    raw_text = content.strip()

    # 1. Strip markdown code blocks if present
    if "```" in raw_text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
        if match:
            raw_text = match.group(1).strip()
        else:
            lines = raw_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw_text = "\n".join(lines).strip()

    parsed_dict: dict[str, Any] = {}

    # Stage 1: Strict JSON parsing
    try:
        loaded = json.loads(raw_text)
        if isinstance(loaded, dict):
            parsed_dict = loaded
    except Exception:
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
        for candidate in ("WORK_NOTES", "DUE_DILIGENCE", "BRAINSTORM", "NOTION_NOTE", "LOCAL_KEEP"):
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
