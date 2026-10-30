"""Tests for the visual agent pipeline widgets (deskpilot.ui.pipeline)."""

from __future__ import annotations

import io
from rich.console import Console

from deskpilot.ui.pipeline import render_pipeline
from deskpilot.ui import theme


def test_render_pipeline_unicode():
    """Verify render_pipeline produces proper connected steps in unicode mode."""
    panel = render_pipeline(
        ["Scan", "Triage", "Review", "Sync"],
        current_index=1,
        status_msg="Analyzing screenshot #3...",
        unicode_ok=True,
    )
    console = Console(file=io.StringIO(), width=100, theme=theme.DESKPILOT_THEME)
    console.print(panel)
    output = console.file.getvalue()

    assert "Scan" in output
    assert "Triage" in output
    assert "Review" in output
    assert "Sync" in output
    assert "Analyzing screenshot #3..." in output
    assert "●" in output or "◎" in output


def test_render_pipeline_ascii_fallback():
    """Verify render_pipeline gracefully downgrades to ASCII characters."""
    panel = render_pipeline(
        ["Step A", "Step B", "Step C"],
        current_index=0,
        status_msg="Starting...",
        unicode_ok=False,
    )
    console = Console(file=io.StringIO(), width=100, theme=theme.DESKPILOT_THEME)
    console.print(panel)
    output = console.file.getvalue()

    assert "Step A" in output
    assert "Step B" in output
    assert "Step C" in output
    assert "Starting..." in output
    assert "●" not in output
    assert "[*]" in output or "[x]" in output or "->" in output
