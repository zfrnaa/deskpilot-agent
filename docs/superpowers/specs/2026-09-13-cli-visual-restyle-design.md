# DeskPilot CLI Visual Restyle - Design

Date: 2026-09-13
Status: implemented

## Problem

The CLI rendered a flat 3-panel dashboard with default Rich styling: no theme, no
motion, no visual hierarchy. The boot phase sat frozen for the whole duration of
`asyncio.gather` with zero feedback.

## Goals

- A real visual identity: palette, panel chrome, typographic hierarchy, wordmark.
- Motion during the boot phase only, where the app is genuinely waiting.
- Painted panel backgrounds without hijacking the user terminal.
- Zero new third-party dependencies.
- No change to any existing public function contract.

## Non-goals

- Full TUI (Textual, alt-screen, mouse, arrow-key navigation).
- Half-block PNG rendering (deferred until a logo asset exists).
- Restyling `deskpilot/agent_tasks/**` (documented as follow-up work).

## Hard constraints discovered

1. `tests/test_cli.py` asserts on `console.export_text()` substrings, so every
   user-visible literal must survive byte-for-byte: the panel titles, `15.0 MB`,
   `[1]`-`[5]`, `Dismiss & Exit`, `DeskPilot`, `Floorp`, and the negative boot
   assertion that `Floorp` is absent. Restyling therefore lives in styles,
   borders, glyphs and colour - never in rewritten copy.
2. `mock_main_async.assert_awaited_once_with(startup=True)` and three instances of
   `mock_boot.assert_awaited_once_with(settings)` pin the call signatures. The
   boot animation cannot arrive as a parameter, and `main_async` cannot gain new
   keyword arguments.
3. `Console(record=True)` reports `is_terminal == False`, which is what lets the
   animation be gated on real terminal detection instead of a test-only flag.
4. `test_no_utf8_bom_in_cli_files` plus Windows PowerShell 5.1 defaults (UTF-16
   with BOM) mean every write must be explicit UTF-8 without BOM.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Styling library | Rich only | Already a dependency; Textual breaks prompt injection tests and the instant-boot promise |
| Palette | Catppuccin Mocha | Built for long sessions; success/warning/danger stay distinguishable under deuteranopia |
| Background | Painted panels (`on #1e1e2e`) | The requested different-background look without the alt-screen scrollback cost |
| Colour delivery | Theme override of built-in style names | Retints every existing markup tag with zero call-site churn |
| Animation wiring | Ambient `ContextVar` sink | Preserves the `(settings)`-only call assertion |
| Glyphs | Box drawing plus at most one emoji per panel title | No Nerd Font private-use codepoints; none inside aligned cells |
| Narrow terminals | Wordmark drops below 76 columns | Prevents wrap damage at small widths |
| Encoding safety | UTF-8 stdio plus ASCII fallbacks | cp1252 output cannot encode block art or emoji |

## Risks accepted

- One-column gaps between painted panels show the terminal default background.
- Emoji render double-width on some fonts; mitigated by keeping them out of
  aligned table cells.
- `agent_tasks/**` keeps its own hardcoded Rich markup, so those surfaces are only
themed where they print through a console created by `cli.py`.

## Late findings

Two defects were found during implementation, both invisible to the original
plan, and both are now covered by tests:

1. `sys.stdout.encoding` is cp1252 whenever output is not a console. Block art and
   emoji raised `UnicodeEncodeError` mid-boot - including on the `--startup` logon
   path. Fixed by `_configure_stdio()` plus ASCII degradation.
2. With `TERM` and `COLORTERM` both unset, Rich falls back to 16-colour standard
   output and flattens the palette to the nearest ANSI names. Fixed by
   `detect_color_system()`, which requests truecolor when `WT_SESSION` is present
   on a tty.
