"""Visual design system for the DeskPilot terminal UI.

Intentionally free of eager submodule imports so that ``deskpilot.ui.art``
and ``deskpilot.ui.progress`` can import ``deskpilot.ui.theme`` without any
circular-import ordering concerns.
"""

from __future__ import annotations

__all__ = ["art", "progress", "theme"]