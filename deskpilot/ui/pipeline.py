"""Visual execution pipeline widgets for multi-agent workflows.

Provides clean node step rendering:
    [●───●───○───○] Scan → Vision Triage → Human Review → Notion Sync
with automatic fallback to ASCII when unicode glyphs are unsupported.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from deskpilot.ui import theme


def render_pipeline(
    steps: list[str],
    current_index: int,
    status_msg: str = "",
    unicode_ok: bool = True,
    title: str = "Agent Execution Pipeline",
) -> Panel:
    """Render a horizontal node pipeline with active and pending states."""
    node_done = "●" if unicode_ok else "*"
    node_active = "◎" if unicode_ok else "O"
    node_pending = "○" if unicode_ok else "o"
    connector = "──" if unicode_ok else "--"

    pipeline_text = Text()

    for idx, name in enumerate(steps):
        if idx > 0:
            if idx <= current_index:
                pipeline_text.append(f" {connector} ", style=f"bold {theme.TEAL}")
            else:
                pipeline_text.append(f" {connector} ", style="dim")

        if idx < current_index:
            pipeline_text.append(f"{node_done} ", style=f"bold {theme.GREEN}")
            pipeline_text.append(name, style=f"bold {theme.GREEN}")
        elif idx == current_index:
            pipeline_text.append(f"{node_active} ", style=f"bold {theme.SAPPHIRE}")
            pipeline_text.append(name, style=f"bold {theme.SAPPHIRE}")
        else:
            pipeline_text.append(f"{node_pending} ", style="dim")
            pipeline_text.append(name, style="dim")

    content = [pipeline_text]
    if status_msg:
        status_text = Text()
        indicator = "▸" if unicode_ok else ">"
        status_text.append(f"\n{indicator} ", style=f"bold {theme.MAUVE}")
        status_text.append(status_msg, style="dim")
        content.append(status_text)

    panel_body = Text("\n").join(content) if len(content) > 1 else content[0]

    return theme.panel(
        panel_body,
        title,
        "cyan",
        icon=theme.ICON_SPARK,
    )
