"""Boot-phase progress animation for the DeskPilot CLI.

The animation is driven through an ambient ContextVar sink rather than a new
function parameter, because tests assert that ``run_phase1_boot_sequence`` is
awaited with exactly ``(settings)`` and that ``main_async`` is awaited with
exactly ``(startup=...)``. With no sink installed the tracking helpers are a
pure pass-through, so calling the boot sequence directly (as the test suite
does) behaves precisely as it did before.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from deskpilot.ui import theme

REFRESH_PER_SECOND = 10

# A boot that finishes in 80ms would otherwise flash the animation for a single
# frame. Only ever paid on an interactive launch; --startup never animates.
MIN_DISPLAY_SECONDS = 0.4

BOOT_TASK_LABELS: tuple[str, str, str] = (
    "Purging %TEMP%",
    "Checking winget updates",
    "Fetching today's agenda",
)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class BootTask:
    """One row of the boot animation."""

    label: str
    status: str = PENDING
    error: str = ""


class BootProgress:
    """Mutable render state for the boot animation."""

    def __init__(self, labels: Iterable[str] = (), unicode_ok: bool = True) -> None:
        self.tasks: list[BootTask] = []
        self.started_at: float = time.perf_counter()
        self.live: Live | None = None
        self.unicode_ok = unicode_ok
        for label in labels:
            self.register(label)

    def register(self, label: str) -> None:
        """Add a task row in the pending state (no-op if already present)."""
        if self._find(label) is None:
            self.tasks.append(BootTask(label=label))

    def _find(self, label: str) -> BootTask | None:
        for task in self.tasks:
            if task.label == label:
                return task
        return None

    def _set(self, label: str, status: str, error: str = "") -> None:
        task = self._find(label)
        if task is None:
            task = BootTask(label=label)
            self.tasks.append(task)
        task.status = status
        task.error = error
        self.refresh()

    def begin(self, label: str) -> None:
        self._set(label, RUNNING)

    def finish(self, label: str) -> None:
        self._set(label, DONE)

    def fail(self, label: str, error: str = "") -> None:
        self._set(label, FAILED, error)

    def refresh(self) -> None:
        """Push current state to the live display, when one is attached."""
        if self.live is not None:
            self.live.update(self.renderable())

    def elapsed(self) -> float:
        return time.perf_counter() - self.started_at

    def _frame(self) -> str:
        if not self.unicode_ok:
            return "-" if int(time.perf_counter() * REFRESH_PER_SECOND) % 2 else "/"
        frames = theme.SPINNER_FRAMES
        return frames[int(time.perf_counter() * REFRESH_PER_SECOND) % len(frames)]

    def _glyph(self, unicode_glyph: str, ascii_glyph: str) -> str:
        """Pick the glyph variant this console can actually encode."""
        return unicode_glyph if self.unicode_ok else ascii_glyph

    def renderable(self) -> Group:
        """Build the animated renderable for the current task states."""
        frame = self._frame()
        lines = [
            Text.assemble(
                (f"{self._glyph(theme.ICON_SPARK + chr(32), chr(42) + chr(32))}", theme.SAPPHIRE),
                ("Booting DeskPilot", f"bold {theme.TEXT}"),
                (f"  {self.elapsed():0.1f}s", theme.MUTED),
            )
        ]
        for task in self.tasks:
            if task.status == DONE:
                glyph, style = self._glyph(theme.GLYPH_DONE, "v"), theme.SUCCESS
            elif task.status == FAILED:
                glyph, style = self._glyph(theme.GLYPH_FAILED, "x"), theme.DANGER
            elif task.status == RUNNING:
                glyph, style = frame, theme.SAPPHIRE
            else:
                glyph, style = self._glyph(theme.GLYPH_PENDING, "."), theme.MUTED
            label_style = theme.MUTED if task.status == PENDING else theme.TEXT
            lines.append(Text.assemble((f"  {glyph} ", style), (task.label, label_style)))
        return Group(*lines)


_sink: ContextVar[BootProgress | None] = ContextVar("deskpilot_boot_sink", default=None)


def current_sink() -> BootProgress | None:
    """Return the active boot progress sink, if any."""
    return _sink.get()


def animation_enabled(console: Console, startup: bool = False) -> bool:
    """Decide whether the boot animation may run on this console.

    Disabled entirely for --startup (the Windows logon path), when explicitly
    opted out via DESKPILOT_NO_ANIMATION=1, and whenever output is not attached
    to a terminal - which is what keeps the recorded test consoles silent.
    """
    if startup:
        return False
    if os.environ.get("DESKPILOT_NO_ANIMATION") == "1":
        return False
    try:
        return bool(console.is_terminal)
    except Exception:  # pragma: no cover - defensive against exotic consoles
        return False


@contextmanager
def boot_animation(
    console: Console,
    enabled: bool = True,
    labels: Iterable[str] = BOOT_TASK_LABELS,
) -> Iterator[BootProgress | None]:
    """Run the boot animation around a block of work, if enabled.

    Yields the BootProgress sink (or None when animation is off) and always
    restores the previous sink, so nesting or repeated calls stay safe.
    """
    if not enabled:
        token = _sink.set(None)
        try:
            yield None
        finally:
            _sink.reset(token)
        return

    progress = BootProgress(labels, unicode_ok=theme.supports_glyphs(console.file))
    token = _sink.set(progress)
    try:
        with Live(
            progress.renderable(),
            console=console,
            refresh_per_second=REFRESH_PER_SECOND,
            transient=True,
        ) as live:
            progress.live = live
            yield progress
            remaining = MIN_DISPLAY_SECONDS - progress.elapsed()
            if remaining > 0:
                # Live refreshes on its own background thread, so the spinner
                # keeps animating while the main thread waits here.
                time.sleep(remaining)
            progress.refresh()
    finally:
        progress.live = None
        _sink.reset(token)