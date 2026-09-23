# DeskPilot CLI Visual Restyle - Implementation Plan

Date: 2026-09-13
Status: implemented, all gates green

## Files added

| File | Purpose |
|---|---|
| `deskpilot/ui/__init__.py` | Package marker; no eager submodule imports, so no import ordering hazards |
| `deskpilot/ui/theme.py` | Catppuccin Mocha tokens, glyph constants, Rich `Theme`, panel factory, console factory, glyph and colour-system detection |
| `deskpilot/ui/art.py` | ASCII wordmark masks, gradient renderer, header banner with narrow-terminal degradation |
| `deskpilot/ui/progress.py` | `BootProgress` state, `BootTask` rows, ContextVar sink, `boot_animation` context manager |
| `tests/test_ui_theme.py` | 22 tests covering tokens, glyph degradation, animation gating, sink contract, contract strings |
| `docs/superpowers/specs/2026-09-13-cli-visual-restyle-design.md` | This design record |

## Files changed

`deskpilot/cli.py` - 12 asserted-exactly-once edits:

1. Imports for `deskpilot.ui.art`, `deskpilot.ui.theme`, `deskpilot.ui.progress`.
2. `run_phase1_boot_sequence` wraps each of the three boot coroutines in a local
   `_tracked` helper that reports to `current_sink()`. Signature unchanged, and
   with no sink installed the helper is a pass-through.
3. Six panel builders now call `ui_theme.panel(...)`, gaining a themed border, the
   painted background, a rounded box and an icon.
4. `render_dashboard` builds its header through `ui_art.make_header`.
5. The boot error panel uses `ui_theme.panel` with the warning icon.
6. `prompt_action_menu` themes the menu panel and prefixes the prompt with a glyph,
   falling back to `>` on non-Unicode streams. The `prompt_func` contract is
   unchanged.
7. All four `c = console or Console()` sites became `ui_theme.get_console(console)`.
8. `main_async` wraps the boot await in `boot_animation(...)`, gated by
   `animation_enabled(c, startup)`.
9. `main` calls the new `_configure_stdio()` to force UTF-8 stdio.

## Verification gates

| Gate | Result |
|---|---|
| `tests/test_cli.py` before and after | 28 passed, 28 passed |
| `tests/test_ui_theme.py` | 22 passed |
| Full suite | 228 passed, 5 failed |
| The 5 failures vs pristine `HEAD` | identical 5 failures on a clean `git archive` checkout, so pre-existing and unrelated |
| BOM and ASCII audit | all new files ASCII, none with a BOM |
| Truecolor audit | 60 distinct SGR escapes, all RGB, background `48;2;30;30;46` present |
| Narrow-terminal audit | header fits at widths 100 and 120; degrades to one line below 76 |

## Manual verification left to the owner

Run `uv run deskpilot` in Windows Terminal. This plan does not run it unattended
because the boot sequence deletes `%TEMP%` contents.
