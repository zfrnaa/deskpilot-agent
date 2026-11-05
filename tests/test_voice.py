"""Tests for Kokoro-ONNX voice prompt integration and natural speech summarizers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deskpilot.boot_tasks import (
    BookmarkAuditResult,
    CalendarAgendaResult,
    TempCleanResult,
    WingetUpdateResult,
)
from deskpilot.boot_tasks.calendar_briefing import CalendarEventItem
from deskpilot.config import VoiceConfig
from deskpilot.state import BootState
from deskpilot.voice import (
    speak_in_background,
    speak_text_sync,
    summarize_bookmarks_for_voice,
    summarize_boot_for_voice,
    summarize_downloads_for_voice,
    summarize_screenshots_for_voice,
)


def test_summarize_boot_full():
    state = BootState(
        system_hygiene=TempCleanResult(bytes_freed=52428800),  # 50 MB
        winget_updates=WingetUpdateResult(total_count=16),
        calendar_agenda=CalendarAgendaResult(
            is_configured=True,
            events=[
                CalendarEventItem(
                    id="e1",
                    summary="Design Review",
                    start_time="02:30 PM",
                    end_time="03:30 PM",
                )
            ],
        ),
    )
    summary = summarize_boot_for_voice(state)
    assert "Good day, here is your system briefing" in summary
    assert "Cleaned 50.0 MB" in summary
    assert "16 software packages are ready to be updated" in summary
    assert "Design Review at 02:30 PM" in summary


def test_summarize_boot_clean_state():
    state = BootState(
        system_hygiene=TempCleanResult(bytes_freed=0),
        winget_updates=WingetUpdateResult(total_count=0),
        calendar_agenda=CalendarAgendaResult(is_configured=True, events=[]),
    )
    summary = summarize_boot_for_voice(state)
    assert "Temporary files are completely clean" in summary
    assert "All system packages are fully up to date" in summary
    assert "Your calendar is completely clear today" in summary


def test_summarize_bookmarks():
    res = BookmarkAuditResult(
        total_bookmarks=42,
        noisy_count=2,
        duplicate_groups_count=3,
    )
    summary = summarize_bookmarks_for_voice(res)
    assert "Scanned 42 bookmarks" in summary
    assert "2 tracking or noisy links" in summary
    assert "3 duplicate groups" in summary

    clean_res = BookmarkAuditResult(
        total_bookmarks=10,
        noisy_count=0,
        duplicate_groups_count=0,
    )
    clean_summary = summarize_bookmarks_for_voice(clean_res)
    assert "zero noisy or duplicate links" in clean_summary


def test_summarize_screenshots():
    summary = summarize_screenshots_for_voice(processed_count=5, failed_count=1)
    assert "5 screenshots processed" in summary
    assert "1 item encountered errors" in summary

    empty_summary = summarize_screenshots_for_voice(processed_count=0)
    assert "No new screenshots were pending" in empty_summary


def test_summarize_downloads():
    summary = summarize_downloads_for_voice(moved_count=4, deleted_count=2)
    assert "Organized 4 files" in summary
    assert "Cleaned 2 stale files" in summary

    empty_summary = summarize_downloads_for_voice(moved_count=0, deleted_count=0)
    assert "downloads folder is clean and organized" in empty_summary


def test_speak_disabled():
    cfg = VoiceConfig(enabled=False)
    result = speak_text_sync("Hello world", cfg)
    assert result is False


@patch("deskpilot.voice.get_kokoro")
@patch("deskpilot.voice.play_audio_samples")
def test_speak_mocked(mock_play, mock_get_kokoro):
    cfg = VoiceConfig(enabled=True)
    mock_kokoro = MagicMock()
    mock_kokoro.create.return_value = (MagicMock(), 24000)
    mock_get_kokoro.return_value = mock_kokoro

    result = speak_text_sync("Hello testing", cfg)
    assert result is True
    mock_kokoro.create.assert_called_once_with(
        "Hello testing",
        voice="af_bella",
        speed=1.0,
        lang="en-us",
    )
    # Called twice: once for speech audio, once for completion chime
    assert mock_play.call_count == 2


@patch("deskpilot.voice.threading.Thread")
def test_speak_in_background(mock_thread):
    cfg = VoiceConfig(enabled=True)
    speak_in_background("Test background speech", cfg)
    mock_thread.assert_called_once()
    mock_thread.return_value.start.assert_called_once()


def test_ensure_model_files_verifies_sha256_and_rejects_tampered_download(tmp_path):
    import hashlib
    import pytest
    from deskpilot.voice import ensure_model_files

    cfg = VoiceConfig(
        enabled=True,
        cache_dir=str(tmp_path),
        model_path=str(tmp_path / "kokoro-v1.0.onnx"),
        voices_path=str(tmp_path / "voices-v1.0.bin"),
    )

    def _fake_urlretrieve(url, dest):
        dest.write_bytes(b"tampered-payload")
        return dest, None

    with (
        patch("deskpilot.voice.MIN_MODEL_SIZE", 5),
        patch("deskpilot.voice.urllib.request.urlretrieve", side_effect=_fake_urlretrieve),
    ):
        with pytest.raises(OSError, match="SHA-256"):
            ensure_model_files(cfg)
        assert not (tmp_path / "kokoro-v1.0.onnx").exists()
        assert not (tmp_path / "kokoro-v1.0.tmp").exists()

    valid_bytes = b"valid-kokoro-payload"
    valid_digest = hashlib.sha256(valid_bytes).hexdigest()

    def _valid_urlretrieve(url, dest):
        dest.write_bytes(valid_bytes)
        return dest, None

    with (
        patch("deskpilot.voice.MIN_MODEL_SIZE", 5),
        patch("deskpilot.voice.MIN_VOICES_SIZE", 5),
        patch("deskpilot.voice.KOKORO_MODEL_SHA256", valid_digest),
        patch("deskpilot.voice.KOKORO_VOICES_SHA256", valid_digest),
        patch("deskpilot.voice.urllib.request.urlretrieve", side_effect=_valid_urlretrieve),
    ):
        m_path, v_path = ensure_model_files(cfg)
        assert m_path.exists()
        assert v_path.exists()

