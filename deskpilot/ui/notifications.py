"""Windows native Toast Notification dispatcher for DeskPilot."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deskpilot.state import BootState


def format_boot_notification(state: BootState) -> tuple[str, str]:
    """Format BootState findings into a clean multi-line Toast notification summary."""
    from deskpilot.boot_tasks import CalendarAgendaResult, TempCleanResult, WingetUpdateResult
    from deskpilot.state import format_bytes

    title = "DeskPilot Morning Briefing"
    lines: list[str] = []

    # 1. System hygiene
    sh = state.system_hygiene
    if isinstance(sh, TempCleanResult) and sh.bytes_freed > 0:
        lines.append(f"Cleaned {format_bytes(sh.bytes_freed)} (%TEMP%)")
    elif isinstance(sh, TempCleanResult):
        lines.append("%TEMP% directory clean")

    # 2. Winget updates
    wu = state.winget_updates
    if isinstance(wu, WingetUpdateResult):
        if wu.total_count > 0:
            lines.append(f"{wu.total_count} package update(s) available")
        else:
            lines.append("All packages up to date")

    # 3. Calendar agenda
    ca = state.calendar_agenda
    if isinstance(ca, CalendarAgendaResult) and ca.is_configured:
        if ca.events:
            first_event = ca.events[0]
            time_str = f" ({first_event.start_time})" if first_event.start_time else ""
            lines.append(f"{len(ca.events)} event(s) today: {first_event.summary}{time_str}")
        else:
            lines.append("No calendar events scheduled today")

    if not lines:
        lines.append("Boot sequence completed successfully.")

    return title, "\n".join(lines)


def send_windows_toast(
    title: str,
    message: str,
    action_command: str | None = None,
) -> bool:
    """Dispatch a native Windows Toast Notification using PowerShell WinRT API."""
    import shutil
    import xml.sax.saxutils as saxutils

    ps_bin = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell"

    # XML-escape title and message content
    safe_title = saxutils.escape(title)
    safe_msg = saxutils.escape(message)

    activation = ""
    action_xml = ""
    if action_command:
        safe_action = saxutils.escape(action_command)
        activation = f"launch='{safe_action}' activationType='protocol'"
        action_xml = (
            "<actions>"
            f"<action content='Open Dashboard' arguments='{safe_action}' activationType='protocol' />"
            "</actions>"
        )

    template_xml = (
        f"<toast {activation}>"
        "<visual>"
        "<binding template='ToastGeneric'>"
        f"<text>{safe_title}</text>"
        f"<text>{safe_msg}</text>"
        "</binding>"
        "</visual>"
        f"{action_xml}"
        "</toast>"
    )

    # Escape single quotes for PowerShell single-quoted string literal
    ps_xml = template_xml.replace("'", "''")

    ps_script = f"""
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml('{ps_xml}')
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('DeskPilot')
    $notifier.Show($toast)
    """

    try:
        res = subprocess.run(
            [ps_bin, "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return res.returncode == 0
    except Exception:
        return False

