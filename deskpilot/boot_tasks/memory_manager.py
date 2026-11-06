"""Windows native memory management (Standby List, Working Sets, Modified Page List).

Inspired by Sysinternals RAMMap, using ntdll.NtSetSystemInformation and psapi.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Any

from pydantic import BaseModel, Field

# Windows API Constants & Types
SystemMemoryListInformation = 80

# SYSTEM_MEMORY_LIST_COMMAND
MemoryEmptyWorkingSets = 2
MemoryFlushModifiedList = 3
MemoryPurgeStandbyList = 4
MemoryPurgeLowPriorityStandbyList = 5

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


class MemoryStatus(BaseModel):
    """Snapshot of system physical RAM usage."""

    total_bytes: int = 0
    available_bytes: int = 0
    used_bytes: int = 0
    memory_load_percent: int = 0

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)

    @property
    def available_gb(self) -> float:
        return self.available_bytes / (1024**3)

    @property
    def used_gb(self) -> float:
        return self.used_bytes / (1024**3)


class MemoryCleanResult(BaseModel):
    """Outcome of memory purge and trim operations."""

    standby_purged: bool = False
    modified_flushed: bool = False
    system_working_set_emptied: bool = False
    processes_trimmed: int = 0
    processes_skipped: int = 0
    game_running: bool = False
    active_game: str | None = None
    errors: list[str] = Field(default_factory=list)


def enable_privilege(privilege_name: str) -> bool:
    """Attempt to enable a specific Win32 security privilege on the current process token."""
    if os.name != "nt":
        return False

    try:
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL

        h_token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(h_token)):
            return False

        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, privilege_name, ctypes.byref(luid)):
            kernel32.CloseHandle(h_token)
            return False

        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED

        advapi32.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None)
        err = kernel32.GetLastError()
        kernel32.CloseHandle(h_token)
        return err == 0
    except Exception:
        return False


def get_system_memory_status() -> MemoryStatus:
    """Retrieve current Windows physical memory stats via GlobalMemoryStatusEx."""
    if os.name != "nt":
        return MemoryStatus()

    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total = int(stat.ullTotalPhys)
            avail = int(stat.ullAvailPhys)
            used = max(0, total - avail)
            return MemoryStatus(
                total_bytes=total,
                available_bytes=avail,
                used_bytes=used,
                memory_load_percent=int(stat.dwMemoryLoad),
            )
    except Exception:
        pass
    return MemoryStatus()


def get_running_process_names() -> set[str]:
    """Return lowercase executable names of all running processes via CreateToolhelp32Snapshot."""
    if os.name != "nt":
        return set()

    names: set[str] = set()
    kernel32 = ctypes.windll.kernel32
    h_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot == -1 or not h_snapshot:
        return names

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    try:
        if kernel32.Process32First(h_snapshot, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode("ansi", errors="ignore").lower().strip()
                if name:
                    names.add(name)
                if not kernel32.Process32Next(h_snapshot, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(h_snapshot)

    return names


DEFAULT_GAME_PROCESSES: list[str] = [
    "tslgame.exe",      # PUBG: BATTLEGROUNDS main executable
    "tslgame_uc.exe",   # PUBG Uncheater
    "tslgame_be.exe",   # PUBG BattlEye
    "pubg.exe",
    "steam.exe",
    "epicgameslauncher.exe",
    "riotclientservices.exe",
    "valorant.exe",
    "cs2.exe",
    "dota2.exe",
    "overwatch.exe",
    "r5apex.exe",       # Apex Legends
]


def detect_active_game(known_games: list[str] | None = None) -> str | None:
    """Return the name of an active game process if currently running, else None."""
    targets = [g.lower().strip() for g in (known_games or DEFAULT_GAME_PROCESSES)]
    running = get_running_process_names()
    for g in targets:
        if g in running:
            return g
    return None


def is_game_running(known_games: list[str] | None = None) -> bool:
    """Check if any specified game or launcher is currently running."""
    return detect_active_game(known_games) is not None


def purge_standby_list(low_priority_only: bool = False) -> tuple[bool, str | None]:
    """Purge Windows Standby List (Command 4 or 5) via ntdll.NtSetSystemInformation."""
    if os.name != "nt":
        return False, "Not supported on non-Windows OS"

    enable_privilege("SeProfileSingleProcessPrivilege")
    enable_privilege("SeIncreaseQuotaPrivilege")

    ntdll = ctypes.windll.ntdll
    ntdll.NtSetSystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtSetSystemInformation.restype = wintypes.LONG

    cmd_val = MemoryPurgeLowPriorityStandbyList if low_priority_only else MemoryPurgeStandbyList
    command = ctypes.c_ulong(cmd_val)

    try:
        status = ntdll.NtSetSystemInformation(
            SystemMemoryListInformation,
            ctypes.byref(command),
            ctypes.sizeof(command),
        )
        if status == 0:
            return True, None
        return False, f"NtSetSystemInformation returned status {hex(status & 0xFFFFFFFF)}"
    except Exception as e:
        return False, str(e)


def flush_modified_page_list() -> tuple[bool, str | None]:
    """Flush the Windows Modified Page List (Command 3) via ntdll.NtSetSystemInformation."""
    if os.name != "nt":
        return False, "Not supported on non-Windows OS"

    enable_privilege("SeProfileSingleProcessPrivilege")
    enable_privilege("SeIncreaseQuotaPrivilege")

    ntdll = ctypes.windll.ntdll
    ntdll.NtSetSystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtSetSystemInformation.restype = wintypes.LONG

    command = ctypes.c_ulong(MemoryFlushModifiedList)

    try:
        status = ntdll.NtSetSystemInformation(
            SystemMemoryListInformation,
            ctypes.byref(command),
            ctypes.sizeof(command),
        )
        if status == 0:
            return True, None
        return False, f"NtSetSystemInformation returned status {hex(status & 0xFFFFFFFF)}"
    except Exception as e:
        return False, str(e)


def empty_system_working_set() -> tuple[bool, str | None]:
    """Empty the system working set (Command 2) via ntdll.NtSetSystemInformation."""
    if os.name != "nt":
        return False, "Not supported on non-Windows OS"

    enable_privilege("SeIncreaseQuotaPrivilege")

    ntdll = ctypes.windll.ntdll
    ntdll.NtSetSystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    ntdll.NtSetSystemInformation.restype = wintypes.LONG

    command = ctypes.c_ulong(MemoryEmptyWorkingSets)

    try:
        status = ntdll.NtSetSystemInformation(
            SystemMemoryListInformation,
            ctypes.byref(command),
            ctypes.sizeof(command),
        )
        if status == 0:
            return True, None
        return False, f"NtSetSystemInformation returned status {hex(status & 0xFFFFFFFF)}"
    except Exception as e:
        return False, str(e)


def trim_user_working_sets(skip_self: bool = True) -> tuple[int, int]:
    """Trim working sets of accessible user-space processes via psapi.EmptyWorkingSet."""
    if os.name != "nt":
        return 0, 0

    psapi = ctypes.windll.psapi
    kernel32 = ctypes.windll.kernel32

    psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    psapi.EmptyWorkingSet.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    h_snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h_snapshot == -1 or not h_snapshot:
        return 0, 0

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    current_pid = os.getpid()

    trimmed = 0
    skipped = 0

    try:
        if kernel32.Process32First(h_snapshot, ctypes.byref(pe)):
            while True:
                pid = pe.th32ProcessID
                if pid > 4 and (not skip_self or pid != current_pid):
                    h_proc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
                    if h_proc:
                        if psapi.EmptyWorkingSet(h_proc):
                            trimmed += 1
                        else:
                            skipped += 1
                        kernel32.CloseHandle(h_proc)
                    else:
                        skipped += 1
                if not kernel32.Process32Next(h_snapshot, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(h_snapshot)

    return trimmed, skipped


def perform_game_standby_purge(known_games: list[str] | None = None) -> MemoryCleanResult:
    """Immediate purge executed upon detecting a game/heavy task launch."""
    active_game = detect_active_game(known_games)
    res = MemoryCleanResult(game_running=active_game is not None, active_game=active_game)

    ok, err = purge_standby_list(low_priority_only=False)
    res.standby_purged = ok
    if err:
        res.errors.append(f"Standby purge: {err}")

    return res


def perform_periodic_memory_cleanup(
    known_games: list[str] | None = None,
    force_even_if_gaming: bool = False,
) -> MemoryCleanResult:
    """Periodic maintenance: flushes modified list and trims working sets unless a game is running."""
    active_game = detect_active_game(known_games)
    is_gaming = active_game is not None

    res = MemoryCleanResult(game_running=is_gaming, active_game=active_game)

    # STRICT SAFEGUARD: Never trim working sets or flush pages while user is actively gaming!
    if is_gaming and not force_even_if_gaming:
        res.errors.append(f"Skipped periodic working sets & modified page flush: game '{active_game}' is actively running.")
        return res

    # 1. Flush modified page list
    m_ok, m_err = flush_modified_page_list()
    res.modified_flushed = m_ok
    if m_err:
        res.errors.append(f"Modified pages: {m_err}")

    # 2. Empty system working set
    s_ok, s_err = empty_system_working_set()
    res.system_working_set_emptied = s_ok
    if s_err:
        res.errors.append(f"System working set: {s_err}")

    # 3. Trim accessible user-space process working sets
    trimmed, skipped = trim_user_working_sets()
    res.processes_trimmed = trimmed
    res.processes_skipped = skipped

    # 4. Purge Standby List (converts trimmed pages & disk cache to 100% free RAM)
    sb_ok, sb_err = purge_standby_list(low_priority_only=False)
    res.standby_purged = sb_ok
    if sb_err:
        res.errors.append(f"Standby purge: {sb_err}")

    return res
