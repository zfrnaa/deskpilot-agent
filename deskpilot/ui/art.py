"""DeskPilot ASCII wordmark and dashboard header banner.

Pure text art: no binary assets, no Pillow, no new dependency. Glyphs are held
as escapes so this source file stays ASCII-only, and the layout degrades to a
single-line banner on narrow terminals instead of wrapping into garbage.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from deskpilot.ui import theme

BLOCK = theme.GLYPH_BLOCK
ASCII_BLOCK = "#"

# 5-row block masks. "#" is swapped for BLOCK at render time so the art is
# authored in ASCII and stays readable in any editor.
_LETTERS: dict[str, list[str]] = {
    "D": ["#### ", "#   #", "#   #", "#   #", "#### "],
    "E": ["#####", "#    ", "#### ", "#    ", "#####"],
    "S": ["#####", "#    ", "#####", "    #", "#####"],
    "K": ["#   #", "#  # ", "###  ", "#  # ", "#   #"],
    "P": ["#### ", "#   #", "#### ", "#    ", "#    "],
    "I": ["#####", "  #  ", "  #  ", "  #  ", "#####"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#####"],
    "O": ["#####", "#   #", "#   #", "#   #", "#####"],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  "],
}

WORDMARK_TEXT = "DESKPILOT"
WORDMARK_GAP = " "

# Below this console width the wordmark is dropped in favour of one banner line.
WORDMARK_MIN_WIDTH = 76

TAGLINE = "DeskPilot Morning Command Center"


def wordmark_lines() -> list[str]:
    """Return the block-art rows for WORDMARK_TEXT (ASCII masks, un-substituted)."""
    return [
        WORDMARK_GAP.join(_LETTERS[ch][row] for ch in WORDMARK_TEXT)
        for row in range(5)
    ]


def wordmark_width() -> int:
    """Width in cells of the widest wordmark row."""
    return max(len(line) for line in wordmark_lines())


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _mix(start: str, end: str, ratio: float) -> str:
    sr, sg, sb = _hex_to_rgb(start)
    er, eg, eb = _hex_to_rgb(end)
    return "#{:02x}{:02x}{:02x}".format(
        round(sr + (er - sr) * ratio),
        round(sg + (eg - sg) * ratio),
        round(sb + (eb - sb) * ratio),
    )


def gradient(count: int) -> list[str]:
    """Return ``count`` hex colours sweeping sapphire -> lavender -> mauve."""
    if count <= 1:
        return [theme.SAPPHIRE]
    stops = [theme.SAPPHIRE, theme.LAVENDER, theme.MAUVE]
    span = len(stops) - 1
    colors: list[str] = []
    for index in range(count):
        position = index / (count - 1) * span
        lower = min(int(position), span - 1)
        colors.append(_mix(stops[lower], stops[lower + 1], position - lower))
    return colors


def render_wordmark(unicode_ok: bool = True) -> Text:
    """Render the DeskPilot wordmark with a horizontal colour sweep.

    Falls back to ASCII "#" blocks when the target stream cannot encode
    U+2588 (e.g. redirected cp1252 output), so logs stay readable.
    """
    lines = wordmark_lines()
    colors = gradient(wordmark_width())
    body = Text(justify="center")
    block = BLOCK if unicode_ok else ASCII_BLOCK
    for row_index, line in enumerate(lines):
        if row_index:
            body.append("\n")
        for column, char in enumerate(line):
            if char == "#":
                body.append(block, style=f"bold {colors[column]}")
            else:
                body.append(" ")
    return body


def make_header(timestamp: str, width: int = 80, unicode_ok: bool = True) -> Panel:
    """Build the dashboard header, degrading to one line on narrow terminals."""
    if width >= WORDMARK_MIN_WIDTH:
        body = render_wordmark(unicode_ok)
        body.append("\n\n")
        body.append(TAGLINE, style="bold white")
    else:
        body = Text(TAGLINE, justify="center", style="bold white")

    return Panel(
        body,
        subtitle=timestamp,
        border_style=theme.PRIMARY,
        box=theme.PANEL_BOX,
        style=theme.PANEL_STYLE,
    )