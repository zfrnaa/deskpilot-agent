"""Local Kokoro-ONNX Text-to-Speech (TTS) integration for DeskPilot.

Provides conversational voice summaries for:
- Boot sequence briefing (hygiene, winget updates, calendar)
- On-demand Floorp bookmark audit
- On-demand Screenshot triage
- On-demand Downloads hygiene
"""

from __future__ import annotations

import asyncio
import logging
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deskpilot.boot_tasks import BookmarkAuditResult
    from deskpilot.config import VoiceConfig
    from deskpilot.state import BootState

logger = logging.getLogger("deskpilot.voice")

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

_kokoro_instance: Any = None
_kokoro_lock = threading.Lock()
_audio_lock = threading.Lock()

# Minimal expected sizes in bytes to verify download integrity
MIN_MODEL_SIZE = 100_000_000   # ~325 MB full
MIN_VOICES_SIZE = 10_000_000   # ~28 MB full


def ensure_model_files(voice_cfg: VoiceConfig) -> tuple[Path, Path]:
    """Ensure Kokoro ONNX model and voices file exist locally and are valid, downloading if necessary."""
    cache_dir = voice_cfg.get_resolved_cache_dir()

    model_path = voice_cfg.model_path or (cache_dir / "kokoro-v1.0.onnx")
    voices_path = voice_cfg.voices_path or (cache_dir / "voices-v1.0.bin")

    model_path = Path(model_path).expanduser().resolve()
    voices_path = Path(voices_path).expanduser().resolve()

    if not model_path.exists() or model_path.stat().st_size < MIN_MODEL_SIZE:
        logger.info("Downloading Kokoro ONNX model (~310MB) to %s ...", model_path)
        tmp_model = model_path.with_suffix(".tmp")
        try:
            urllib.request.urlretrieve(KOKORO_MODEL_URL, tmp_model)
            if tmp_model.stat().st_size < MIN_MODEL_SIZE:
                raise IOError(f"Downloaded model file incomplete: {tmp_model.stat().st_size} bytes")
            tmp_model.replace(model_path)
            logger.info("Kokoro model download complete.")
        except Exception:
            if tmp_model.exists():
                tmp_model.unlink(missing_ok=True)
            raise

    if not voices_path.exists() or voices_path.stat().st_size < MIN_VOICES_SIZE:
        logger.info("Downloading Kokoro voices file (~27MB) to %s ...", voices_path)
        tmp_voices = voices_path.with_suffix(".tmp")
        try:
            urllib.request.urlretrieve(KOKORO_VOICES_URL, tmp_voices)
            if tmp_voices.stat().st_size < MIN_VOICES_SIZE:
                raise IOError(f"Downloaded voices file incomplete: {tmp_voices.stat().st_size} bytes")
            tmp_voices.replace(voices_path)
            logger.info("Kokoro voices download complete.")
        except Exception:
            if tmp_voices.exists():
                tmp_voices.unlink(missing_ok=True)
            raise

    return model_path, voices_path


def get_kokoro(voice_cfg: VoiceConfig) -> Any:
    """Lazily load and cache the Kokoro TTS engine."""
    global _kokoro_instance
    if _kokoro_instance is not None:
        return _kokoro_instance

    with _kokoro_lock:
        if _kokoro_instance is not None:
            return _kokoro_instance

        from kokoro_onnx import Kokoro

        model_path, voices_path = ensure_model_files(voice_cfg)
        _kokoro_instance = Kokoro(str(model_path), str(voices_path))
        return _kokoro_instance


def play_audio_samples(samples: Any, sample_rate: int) -> None:
    """Play raw audio numpy array using sounddevice with thread-safe serialization."""
    try:
        import sounddevice as sd

        with _audio_lock:
            sd.play(samples, sample_rate)
            sd.wait()
    except Exception as exc:
        logger.warning("Audio playback failed: %s", exc)


def play_chime(kind: str = "option", sample_rate: int = 24000) -> None:
    """Play a clean, subtle audio chime (kind='option' or 'done') generated with numpy."""
    try:
        import numpy as np

        def _generate_tone(freq: float, duration: float, volume: float = 0.15) -> np.ndarray:
            t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
            fade_len = int(0.01 * sample_rate)
            envelope = np.ones_like(t)
            if len(envelope) > 2 * fade_len:
                envelope[:fade_len] = np.linspace(0, 1, fade_len)
                envelope[-fade_len:] = np.linspace(1, 0, fade_len)
            return (np.sin(2 * np.pi * freq * t) * envelope * volume).astype(np.float32)

        if kind == "done":
            # Soft two-tone confirmation chime (A5 -> D6)
            tone1 = _generate_tone(880.0, 0.08, volume=0.15)
            pause = np.zeros(int(sample_rate * 0.02), dtype=np.float32)
            tone2 = _generate_tone(1174.66, 0.16, volume=0.15)
            chime = np.concatenate([tone1, pause, tone2])
        else:
            # Subtle ascending two-tone for option selection
            tone1 = _generate_tone(587.33, 0.06, volume=0.12)
            pause = np.zeros(int(sample_rate * 0.015), dtype=np.float32)
            tone2 = _generate_tone(880.0, 0.08, volume=0.12)
            chime = np.concatenate([tone1, pause, tone2])

        play_audio_samples(chime, sample_rate)
    except Exception as exc:
        logger.debug("Failed to play chime: %s", exc)


def play_chime_in_background(kind: str = "option") -> None:
    """Play a chime non-blockingly in the background."""
    thread = threading.Thread(
        target=play_chime,
        args=(kind,),
        daemon=True,
        name="DeskPilot-Chime-Worker",
    )
    thread.start()


def speak_text_sync(text: str, voice_cfg: VoiceConfig) -> bool:
    """Synthesize text and play audio synchronously, followed by a completion chime."""
    if not text.strip() or not voice_cfg.enabled:
        return False

    try:
        with _kokoro_lock:
            kokoro = get_kokoro(voice_cfg)
            samples, sample_rate = kokoro.create(
                text,
                voice=voice_cfg.voice,
                speed=voice_cfg.speed,
                lang=voice_cfg.lang,
            )
        play_audio_samples(samples, sample_rate)
        play_chime("done")
        return True
    except Exception as exc:
        logger.warning("Voice synthesis failed for '%s': %s", text, exc)
        return False


def speak_in_background(text: str, voice_cfg: VoiceConfig) -> threading.Thread | None:
    """Dispatch voice synthesis and playback on a background thread. Returns the thread if spawned."""
    if not text.strip() or not voice_cfg.enabled:
        return None

    thread = threading.Thread(
        target=speak_text_sync,
        args=(text, voice_cfg),
        daemon=True,
        name="DeskPilot-Voice-Worker",
    )
    thread.start()
    return thread


# --- Natural Speech Summarizers ---


def summarize_boot_for_voice(state: BootState) -> str:
    """Format BootState findings into a natural, conversational spoken briefing."""
    from deskpilot.boot_tasks import CalendarAgendaResult, TempCleanResult, WingetUpdateResult
    from deskpilot.state import format_bytes

    parts: list[str] = ["Good day, here is your morning briefing."]

    # 1. System hygiene
    sh = state.system_hygiene
    if isinstance(sh, TempCleanResult):
        if sh.bytes_freed > 0:
            parts.append(f"Cleaned {format_bytes(sh.bytes_freed)} from temporary files.")
        else:
            parts.append("Temporary files are completely clean.")

    # 2. Winget updates
    wu = state.winget_updates
    if isinstance(wu, WingetUpdateResult):
        if wu.total_count == 1:
            parts.append("1 software package update is available.")
        elif wu.total_count > 1:
            parts.append(f"{wu.total_count} software packages are ready to be updated.")
        else:
            parts.append("All system packages are fully up to date.")

    # 3. Calendar agenda
    ca = state.calendar_agenda
    if isinstance(ca, CalendarAgendaResult) and ca.is_configured:
        if ca.events:
            first_event = ca.events[0]
            if first_event.start_time:
                parts.append(
                    f"You have {len(ca.events)} events scheduled today. "
                    f"Next up is {first_event.summary} at {first_event.start_time}."
                )
            else:
                parts.append(
                    f"You have {len(ca.events)} events scheduled today: {first_event.summary}."
                )
        else:
            parts.append("Your calendar is completely clear today.")

    return " ".join(parts)


def summarize_bookmarks_for_voice(result: BookmarkAuditResult) -> str:
    """Format Floorp bookmark audit results into conversational speech."""
    if result.error:
        return f"Floorp bookmark audit reported an issue: {result.error}"

    parts = [f"Floorp bookmark audit complete. Scanned {result.total_bookmarks} bookmarks."]
    issues = []
    if result.noisy_count > 0:
        issues.append(f"{result.noisy_count} tracking or noisy links")
    if result.duplicate_groups_count > 0:
        issues.append(f"{result.duplicate_groups_count} duplicate groups")

    if issues:
        parts.append(f"Identified {' and '.join(issues)}.")
    else:
        parts.append("All bookmarks look healthy with zero noisy or duplicate links.")

    return " ".join(parts)


def summarize_screenshots_for_voice(processed_count: int, failed_count: int = 0) -> str:
    """Format Screenshot triage results into conversational speech."""
    if processed_count == 0:
        return "Screenshot triage complete. No new screenshots were pending."

    summary = f"Screenshot triage complete. {processed_count} screenshot{'s' if processed_count != 1 else ''} processed and synced to Notion."
    if failed_count > 0:
        summary += f" {failed_count} item{'s' if failed_count != 1 else ''} encountered errors."
    return summary


def summarize_downloads_for_voice(moved_count: int, deleted_count: int = 0) -> str:
    """Format Downloads hygiene results into conversational speech."""
    total = moved_count + deleted_count
    if total == 0:
        return "Downloads hygiene complete. Your downloads folder is clean and organized."

    parts = ["Downloads hygiene complete."]
    if moved_count > 0:
        parts.append(f"Organized {moved_count} file{'s' if moved_count != 1 else ''}.")
    if deleted_count > 0:
        parts.append(f"Cleaned {deleted_count} stale file{'s' if deleted_count != 1 else ''}.")
    return " ".join(parts)
