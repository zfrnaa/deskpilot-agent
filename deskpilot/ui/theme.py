"""DeskPilot visual design tokens (Catppuccin Mocha palette).

Single source of truth for colours, glyphs and panel chrome so the CLI's four
visual surfaces (boot dashboard, action menu, inline prompts, agent output) stay
consistent. Importing this module touches only ``rich`` and the standard
library, so it remains safe on the sub-second boot path.
"""

from __future__ import annotations

import os
import sys

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

# --- Palette (Catppuccin Mocha) --------------------------------------------
BG = "#1e1e2e"
MANTLE = "#181825"
CRUST = "#11111b"
SURFACE = "#313244"
OVERLAY = "#6c7086"
TEXT = "#cdd6f4"
SUBTEXT = "#bac2de"

BLUE = "#89b4fa"
LAVENDER = "#b4befe"
SAPPHIRE = "#74c7ec"
TEAL = "#94e2d5"
GREEN = "#a6e3a1"
YELLOW = "#f9e2af"
PEACH = "#fab387"
RED = "#f38ba8"
MAUVE = "#cba6f7"
PINK = "#f5c2e7"

# Semantic hues. These deliberately preserve the *meaning* of the original
# palette (hygiene=cyan, bookmarks=magenta, winget=blue, calendar=green,
# errors=red) so the dashboard still reads correctly at a glance.
PRIMARY = SAPPHIRE
SUCCESS = GREEN
WARNING = YELLOW
DANGER = RED
ACCENT = MAUVE
MUTED = OVERLAY

# --- Glyphs ----------------------------------------------------------------
# Kept as escapes so this source file stays pure ASCII. Box-drawing only for
# structure; at most one emoji per panel title, never inside aligned cells.
ICON_HYGIENE = "\U0001F9F9"
ICON_WINGET = "\U0001F4E6"
ICON_CALENDAR = "\U0001F4C5"
ICON_BOOKMARKS = "\U0001F516"
ICON_ERROR = "\u26A0"
ICON_MENU = "\U0001F9ED"
ICON_SPARK = "\u2728"
GLYPH_PROMPT = "\u25B8"
GLYPH_PENDING = "\u25CB"
GLYPH_DONE = "\u2714"
GLYPH_FAILED = "\u2718"
SPINNER_FRAMES = "\u280B\u2819\u2839\u2838\u283C\u2834\u2826\u2827\u2807\u280F"
GLYPH_BLOCK = "\u2588"

# --- Panel chrome ----------------------------------------------------------
PANEL_BOX = box.ROUNDED
PANEL_STYLE = f"on {BG}"

# --- Theme -----------------------------------------------------------------
# Rich looks up the *whole* tag string ("bold cyan"), not the bare colour, so
# every markup variant used by the CLI must be registered explicitly. This is
# what lets the entire existing codebase retheme with zero call-site churn.
DESKPILOT_THEME = Theme(
    {
        "cyan": TEAL,
        "bold cyan": f"bold {TEAL}",
        "magenta": MAUVE,
        "bold magenta": f"bold {MAUVE}",
        "blue": BLUE,
        "bold blue": f"bold {BLUE}",
        "green": GREEN,
        "bold green": f"bold {GREEN}",
        "yellow": YELLOW,
        "bold yellow": f"bold {YELLOW}",
        "red": RED,
        "bold red": f"bold {RED}",
        "white": TEXT,
        "bold white": f"bold {TEXT}",
        "bright_blue": SAPPHIRE,
        "bold bright_blue": f"bold {SAPPHIRE}",
        "dim": MUTED,
        # Named design tokens for new call sites.
        "desk.primary": f"bold {PRIMARY}",
        "desk.success": SUCCESS,
        "desk.warning": WARNING,
        "desk.danger": f"bold {DANGER}",
        "desk.accent": f"bold {ACCENT}",
        "desk.text": TEXT,
        "desk.subtext": SUBTEXT,
        "desk.muted": MUTED,
    }
)


def supports_glyphs(stream=None) -> bool:
    """Return True when the stream can encode the block art and emoji we use.

    Redirected Windows output falls back to the ANSI code page (cp1252), which
    cannot encode block art or emoji. Callers use this to downgrade to ASCII
    rather than raising UnicodeEncodeError mid-boot.
    """
    target = stream if stream is not None else sys.stdout
    encoding = getattr(target, "encoding", None)
    if not encoding:
        return False
    try:
        (GLYPH_BLOCK + ICON_HYGIENE + GLYPH_DONE).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def detect_color_system(stream=None, environ=None) -> str | None:
    """Return an explicit colour system, or None to let rich decide.

    Windows Terminal supports 24-bit colour but does not advertise it through
    COLORTERM or TERM, so rich otherwise falls back to 16 colours and flattens
    the palette to the nearest ANSI names. When nothing in the environment
    describes the terminal but WT_SESSION is set on a real tty, ask for
    truecolor explicitly. Redirected output still returns None so piped and
    logon output stays free of escape codes.
    """
    env = environ if environ is not None else os.environ
    if env.get("DESKPILOT_COLOR_SYSTEM"):
        return env["DESKPILOT_COLOR_SYSTEM"]
    if env.get("COLORTERM") or env.get("TERM"):
        return None
    target = stream if stream is not None else sys.stdout
    try:
        is_tty = bool(target.isatty())
    except Exception:  # pragma: no cover - defensive against exotic streams
        is_tty = False
    if env.get("WT_SESSION") and is_tty:
        return "truecolor"
    return None


def get_console(console: Console | None = None) -> Console:
    """Return the supplied console, or a themed one when none was injected."""
    if console is not None:
        return console
    force_terminal = True if os.environ.get("DESKPILOT_FORCE_COLOR") == "1" else None
    return Console(
        theme=DESKPILOT_THEME,
        color_system=detect_color_system(),
        force_terminal=force_terminal,
    )


def panel(content, title: str, border: str, icon: str = "") -> Panel:
    """Build a consistently themed dashboard panel.

    The icon is dropped (rather than raising) when the target stream cannot
    encode emoji, which keeps redirected and logon output intact.
    """
    if icon and not supports_glyphs():
        icon = ""
    label = f"{icon} {title}" if icon else title
    return Panel(
        content,
        title=f"[bold {border}]{label}[/bold {border}]",
        border_style=border,
        box=PANEL_BOX,
        style=PANEL_STYLE,
    )