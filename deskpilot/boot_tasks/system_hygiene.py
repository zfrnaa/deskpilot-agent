"""Windows %TEMP% cleaner boot task with safe locked-file skip, memory dump and AI hygiene."""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field

from deskpilot.config import TempCleanerConfig


class TempCleanResult(BaseModel):
    """Execution statistics from temporary directory cleanup."""

    bytes_freed: int = 0
    files_removed: int = 0
    dirs_removed: int = 0
    privacy_traces_cleared: int = 0
    registry_entries_cleaned: int = 0
    monthly_deep_ran: bool = False
    errors: list[str] = Field(default_factory=list)

    def merge(self, other: TempCleanResult) -> None:
        """Accumulate results from another cleanup step."""
        self.bytes_freed += other.bytes_freed
        self.files_removed += other.files_removed
        self.dirs_removed += other.dirs_removed
        self.privacy_traces_cleared += other.privacy_traces_cleared
        self.registry_entries_cleaned += other.registry_entries_cleaned
        self.monthly_deep_ran = self.monthly_deep_ran or other.monthly_deep_ran
        self.errors.extend(other.errors)


def is_file_locked(path: Path) -> bool:
    """Check if a file is currently locked or in use by another process.

    On Windows, attempting to rename an open file to its existing path raises
    PermissionError if any process holds a handle without delete sharing.
    """
    if not path.is_file() and not path.is_symlink():
        return False
    try:
        os.rename(path, path)
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _is_link_or_junction(path: Path) -> bool:
    """Return True if path is a symbolic link or Windows NTFS directory junction."""
    try:
        if path.is_symlink():
            return True
        if hasattr(path, "is_junction") and path.is_junction():
            return True
    except OSError:
        return True
    return False


def _clean_directory_tree_sync(
    dir_path: Path,
    max_age_hours: int,
    dry_run: bool,
    delete_empty_dirs: bool = True,
) -> TempCleanResult:
    """Synchronously clean unlocked files older than max_age_hours in dir_path."""
    if not dir_path.exists():
        return TempCleanResult(
            errors=[f"Directory does not exist: {dir_path}"]
        )
    if not dir_path.is_dir():
        return TempCleanResult(
            errors=[f"Path is not a directory: {dir_path}"]
        )

    cutoff_time = time.time() - (max_age_hours * 3600)
    bytes_freed = 0
    files_removed = 0
    dirs_removed = 0
    errors: list[str] = []
    would_remove_files: set[Path] = set()
    would_remove_dirs: set[Path] = set()

    for root_str, _, files in os.walk(dir_path, topdown=False, followlinks=False):
        root_dir = Path(root_str)
        if root_dir != dir_path and _is_link_or_junction(root_dir):
            continue

        # 1. Clean eligible files in current directory
        for file_name in files:
            file_path = root_dir / file_name
            if _is_link_or_junction(file_path):
                continue
            try:
                stat_result = file_path.lstat()
            except OSError as e:
                errors.append(f"Failed to stat {file_path}: {e}")
                continue

            if stat_result.st_mtime > cutoff_time:
                continue

            if is_file_locked(file_path):
                continue

            file_size = stat_result.st_size

            if dry_run:
                would_remove_files.add(file_path)
                files_removed += 1
                bytes_freed += file_size
            else:
                try:
                    file_path.unlink()
                    files_removed += 1
                    bytes_freed += file_size
                except PermissionError:
                    try:
                        os.chmod(file_path, stat.S_IWRITE)
                        file_path.unlink()
                        files_removed += 1
                        bytes_freed += file_size
                    except (PermissionError, OSError):
                        pass
                except OSError as e:
                    errors.append(f"Failed to delete {file_path}: {e}")

        # 2. Clean empty directories if enabled
        if delete_empty_dirs and root_dir != dir_path and not _is_link_or_junction(root_dir):
            if dry_run:
                try:
                    remaining = [
                        c
                        for c in root_dir.iterdir()
                        if c not in would_remove_files and c not in would_remove_dirs
                    ]
                    if not remaining:
                        would_remove_dirs.add(root_dir)
                        dirs_removed += 1
                except OSError as e:
                    errors.append(f"Failed to inspect directory {root_dir}: {e}")
            else:
                try:
                    if not any(root_dir.iterdir()):
                        root_dir.rmdir()
                        dirs_removed += 1
                except (PermissionError, OSError):
                    pass

    return TempCleanResult(
        bytes_freed=bytes_freed,
        files_removed=files_removed,
        dirs_removed=dirs_removed,
        errors=errors,
    )


def _clean_memory_dumps_sync(dump_max_age_days: int, dry_run: bool) -> TempCleanResult:
    """Clean crash dumps (.dmp) and memory dumps older than specified days."""
    result = TempCleanResult()
    cutoff_time = time.time() - (dump_max_age_days * 86400)

    dump_locations: list[Path] = []
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        dump_locations.append(Path(local_appdata) / "CrashDumps")

    system_root = os.getenv("SystemRoot", r"C:\Windows")
    dump_locations.append(Path(system_root) / "Minidump")
    mem_dmp = Path(system_root) / "MEMORY.DMP"

    if mem_dmp.exists() and mem_dmp.is_file():
        try:
            st = mem_dmp.lstat()
            if st.st_mtime <= cutoff_time and not is_file_locked(mem_dmp):
                if dry_run:
                    result.files_removed += 1
                    result.bytes_freed += st.st_size
                else:
                    mem_dmp.unlink()
                    result.files_removed += 1
                    result.bytes_freed += st.st_size
        except (PermissionError, OSError) as e:
            result.errors.append(f"Failed to clean {mem_dmp}: {e}")

    for loc in dump_locations:
        if not loc.exists() or not loc.is_dir():
            continue
        sub_res = _clean_directory_tree_sync(
            loc,
            max_age_hours=dump_max_age_days * 24,
            dry_run=dry_run,
            delete_empty_dirs=False,
        )
        result.merge(sub_res)

    return result


def _clean_antigravity_orphans_sync(
    antigravity_dir: Path | None = None,
    dry_run: bool = False,
) -> TempCleanResult:
    """Clean orphaned conversation brains and db files no longer registered in conversation_summaries.db."""
    result = TempCleanResult()
    base_dir = antigravity_dir or (Path.home() / ".gemini" / "antigravity")

    if not base_dir.exists():
        return result

    db_path = base_dir / "conversation_summaries.db"
    if not db_path.exists():
        return result

    try:
        conn = sqlite3.connect(db_path)
        active_ids = {
            row[0]
            for row in conn.cursor()
            .execute("SELECT conversation_id FROM conversation_summaries")
            .fetchall()
        }
        conn.close()
    except Exception as e:
        result.errors.append(f"Failed to query conversation_summaries.db: {e}")
        return result

    brain_dir = base_dir / "brain"
    if brain_dir.exists() and brain_dir.is_dir() and not _is_link_or_junction(brain_dir):
        for item in brain_dir.iterdir():
            if _is_link_or_junction(item):
                continue
            if item.is_dir() and item.name not in active_ids:
                dir_bytes = 0
                dir_files = 0
                try:
                    for root, dirs, files in os.walk(item, followlinks=False):
                        dirs[:] = [d for d in dirs if not _is_link_or_junction(Path(root) / d)]
                        for f in files:
                            fp = Path(root) / f
                            if _is_link_or_junction(fp):
                                continue
                            try:
                                dir_bytes += fp.lstat().st_size
                                dir_files += 1
                            except OSError:
                                pass
                    if dry_run:
                        result.bytes_freed += dir_bytes
                        result.files_removed += dir_files
                        result.dirs_removed += 1
                    else:
                        shutil.rmtree(item, ignore_errors=True)
                        result.bytes_freed += dir_bytes
                        result.files_removed += dir_files
                        result.dirs_removed += 1
                except Exception as err:
                    result.errors.append(f"Failed to prune orphaned brain {item.name}: {err}")

    conv_dir = base_dir / "conversations"
    if conv_dir.exists() and conv_dir.is_dir() and not _is_link_or_junction(conv_dir):
        for db_file in conv_dir.glob("*.db"):
            if _is_link_or_junction(db_file):
                continue
            if db_file.stem not in active_ids:
                try:
                    size = db_file.stat().st_size
                    if dry_run:
                        result.bytes_freed += size
                        result.files_removed += 1
                    else:
                        db_file.unlink()
                        result.bytes_freed += size
                        result.files_removed += 1
                except Exception as err:
                    result.errors.append(f"Failed to prune orphaned conv db {db_file.name}: {err}")

    crashes_dir = base_dir / "crashes"
    if crashes_dir.exists() and crashes_dir.is_dir():
        crash_res = _clean_directory_tree_sync(
            crashes_dir,
            max_age_hours=24,
            dry_run=dry_run,
            delete_empty_dirs=False,
        )
        result.merge(crash_res)

    return result


def _clean_junk_caches_sync(
    max_age_days: int = 30,
    dry_run: bool = False,
    local_appdata: Path | None = None,
) -> TempCleanResult:
    """Clean stale developer caches, browser caches, Windows system logs, and app stream caches older than max_age_days."""
    result = TempCleanResult()
    base_local = local_appdata or Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    sys_root = Path(os.getenv("SystemRoot", r"C:\Windows"))
    max_age_hours = max_age_days * 24

    junk_targets = [
        # Developer & Package Manager Caches
        base_local / "npm-cache",
        base_local / "pnpm-cache",
        base_local / "uv" / "cache",
        base_local / "pip" / "Cache",
        base_local / "go-build",
        # App Stream & Updater Caches
        base_local / "Spotify" / "Data",
        base_local / "antigravity-updater" / "pending",
        base_local / "notion-updater" / "pending",
        base_local / "Discord" / "Cache",
        base_local / "Discord" / "Code Cache",
        base_local / "Discord" / "GPUCache",
        base_local / "Steam" / "htmlcache" / "Cache",
        base_local / "Steam" / "htmlcache" / "Code Cache",
        base_local / "Steam" / "htmlcache" / "GPUCache",
        # Windows System Junk, Shader Caches, & INetCache (CCleaner / SystemCare targets)
        base_local / "Microsoft" / "Windows" / "INetCache",
        base_local / "Microsoft" / "Windows" / "WebCache",
        base_local / "Microsoft" / "Windows" / "WER",
        base_local / "D3DSCache",
        base_local / "NVIDIA" / "DXCache",
        base_local / "NVIDIA" / "GLCache",
        base_local / "Temp",
        # Chromium Browser Caches (Edge, Chrome, Brave - strictly cache folders, never cookies/passwords!)
        base_local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache" / "Cache_Data",
        base_local / "Microsoft" / "Edge" / "User Data" / "Default" / "Code Cache",
        base_local / "Microsoft" / "Edge" / "User Data" / "Default" / "GPUCache",
        base_local / "Microsoft" / "Edge" / "User Data" / "ShaderCache",
        base_local / "Google" / "Chrome" / "User Data" / "Default" / "Cache" / "Cache_Data",
        base_local / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache",
        base_local / "Google" / "Chrome" / "User Data" / "Default" / "GPUCache",
        base_local / "Google" / "Chrome" / "User Data" / "ShaderCache",
        base_local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Cache" / "Cache_Data",
        base_local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Code Cache",
        base_local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "GPUCache",
        # Windows Logs & SoftwareDistribution Download Cache (if accessible)
        sys_root / "Logs" / "CBS",
        sys_root / "Logs" / "DISM",
        sys_root / "SoftwareDistribution" / "Download",
        sys_root / "ServiceProfiles" / "NetworkService" / "AppData" / "Local" / "Microsoft" / "Windows" / "DeliveryOptimization" / "Cache",
    ]

    # Also include any Floorp / Firefox profile cache2 & shader directories
    for moz_browser in ("Floorp", r"Mozilla\Firefox"):
        profiles_dir = base_local / moz_browser / "Profiles"
        if profiles_dir.exists() and profiles_dir.is_dir():
            for prof in profiles_dir.iterdir():
                if prof.is_dir():
                    junk_targets.append(prof / "cache2" / "entries")
                    junk_targets.append(prof / "startupCache")
                    junk_targets.append(prof / "jumpListCache")
                    junk_targets.append(prof / "shader-cache")

    for target in junk_targets:
        if target.exists() and target.is_dir():
            sub_res = _clean_directory_tree_sync(
                target,
                max_age_hours=max_age_hours,
                dry_run=dry_run,
                delete_empty_dirs=True,
            )
            result.merge(sub_res)

    return result


def _clean_privacy_sweep_sync(
    max_age_days: int = 30,
    dry_run: bool = False,
    appdata_roaming: Path | None = None,
    local_appdata: Path | None = None,
) -> TempCleanResult:
    """Clean stale Windows Recent jump lists, Explorer MRU search/typed history, and crash traces."""
    result = TempCleanResult()
    roaming = appdata_roaming or Path(os.getenv("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    local = local_appdata or Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    max_age_hours = max_age_days * 24

    privacy_dirs = [
        roaming / "Microsoft" / "Windows" / "Recent",
        roaming / "Microsoft" / "Windows" / "Recent" / "AutomaticDestinations",
        roaming / "Microsoft" / "Windows" / "Recent" / "CustomDestinations",
        roaming / "Microsoft" / "Office" / "Recent",
        local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
        local / "Microsoft" / "Windows" / "WER" / "ReportQueue",
        local / "Microsoft" / "Terminal Server Client" / "Cache",
        local / "CrashDumps",
    ]

    for pdir in privacy_dirs:
        if pdir.exists() and pdir.is_dir():
            sub_res = _clean_directory_tree_sync(
                pdir,
                max_age_hours=max_age_hours,
                dry_run=dry_run,
                delete_empty_dirs=False,
            )
            result.bytes_freed += sub_res.bytes_freed
            result.files_removed += sub_res.files_removed
            result.privacy_traces_cleared += sub_res.files_removed

    # Also sweep Explorer Registry MRU privacy lists (RunMRU, TypedPaths, WordWheelQuery search box history)
    # Only runs when using real system paths (not isolated tmp_path unit test mocks)
    if appdata_roaming is None and local_appdata is None:
        try:
            import winreg

            mru_subkeys = [
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths",
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\WordWheelQuery",
                r"Software\Microsoft\Windows\CurrentVersion\Applets\Paint\Recent File List",
                r"Software\Microsoft\Windows\CurrentVersion\Applets\Wordpad\Recent File List",
            ]
            for subkey in mru_subkeys:
                try:
                    access = winreg.KEY_READ if dry_run else (winreg.KEY_READ | winreg.KEY_SET_VALUE)
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, access) as key:
                        num_vals = winreg.QueryInfoKey(key)[1]
                        val_names = []
                        for i in range(num_vals):
                            try:
                                v_name, _, _ = winreg.EnumValue(key, i)
                                if v_name:
                                    val_names.append(v_name)
                            except OSError:
                                continue
                        for v_name in val_names:
                            if dry_run:
                                result.privacy_traces_cleared += 1
                            else:
                                try:
                                    winreg.DeleteValue(key, v_name)
                                    result.privacy_traces_cleared += 1
                                except OSError:
                                    pass
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        except ImportError:
            pass

    return result


def _extract_exe_from_command(cmd_str: str) -> Path | None:
    """Extract executable file path from a Windows registry command or path string."""
    s = cmd_str.strip()
    if not s:
        return None
    if s.startswith('"'):
        end_quote = s.find('"', 1)
        if end_quote > 1:
            candidate = s[1:end_quote]
        else:
            candidate = s.strip('"')
    else:
        lower_s = s.lower()
        exe_idx = lower_s.find(".exe")
        if exe_idx != -1:
            candidate = s[: exe_idx + 4]
        else:
            candidate = s.split()[0]

    expanded = os.path.expandvars(candidate)
    p = Path(expanded)
    if p.is_absolute():
        return p
    return None


def _clean_registry_hygiene_sync(dry_run: bool = False) -> TempCleanResult:
    """Safely prune orphaned Windows Registry references (MuiCache, Application Compatibility Store, broken HKCU Run entries).

    Never touches file associations, CLSIDs, or shared system DLLs.
    """
    result = TempCleanResult()
    try:
        import winreg
    except ImportError:
        return result

    registry_targets = [
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
            "key_name",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store",
            "key_name",
        ),
        (
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            "value_data",
        ),
    ]

    for root_hive, subkey_path, mode in registry_targets:
        try:
            access = winreg.KEY_READ if dry_run else (winreg.KEY_READ | winreg.KEY_SET_VALUE)
            with winreg.OpenKey(root_hive, subkey_path, 0, access) as key:
                num_values = winreg.QueryInfoKey(key)[1]
                to_delete: list[str] = []
                for idx in range(num_values):
                    try:
                        val_name, val_data, _ = winreg.EnumValue(key, idx)
                    except OSError:
                        continue

                    if not val_name or val_name == "LangID":
                        continue

                    if mode == "key_name":
                        # MuiCache often appends .FriendlyAppName or .ApplicationCompany
                        raw_path = val_name
                        for suffix in (".FriendlyAppName", ".ApplicationCompany"):
                            if raw_path.endswith(suffix):
                                raw_path = raw_path[: -len(suffix)]
                                break
                        exe_path = _extract_exe_from_command(raw_path)
                    else:
                        exe_path = _extract_exe_from_command(str(val_data))

                    if exe_path is not None and exe_path.drive and not exe_path.exists():
                        to_delete.append(val_name)

                for val_name in to_delete:
                    if dry_run:
                        result.registry_entries_cleaned += 1
                    else:
                        try:
                            winreg.DeleteValue(key, val_name)
                            result.registry_entries_cleaned += 1
                        except OSError:
                            pass
        except FileNotFoundError:
            continue
        except PermissionError:
            continue
        except OSError as e:
            result.errors.append(f"Registry scan warning on {subkey_path}: {e}")

    return result


async def run_deep_systemcare_hygiene(
    config: TempCleanerConfig | None = None,
    dry_run: bool = False,
) -> TempCleanResult:
    """Execute the full 3-in-1 SystemCare suite (Junk Clean, Privacy Sweep, and Safe Registry Clean)."""
    cfg = config or TempCleanerConfig()
    result = TempCleanResult(monthly_deep_ran=True)

    # 1. Standard 24h Temp + Memory Dumps + Antigravity Orphans
    base_res = await clean_temp_directory(dry_run=dry_run, config=cfg)
    result.merge(base_res)

    max_days = cfg.monthly_cadence_days

    # 2. Junk Caches Clean
    if cfg.monthly_junk_clean:
        junk_res = await asyncio.to_thread(_clean_junk_caches_sync, max_days, dry_run)
        result.merge(junk_res)

    # 3. Privacy Sweep
    if cfg.monthly_privacy_sweep:
        priv_res = await asyncio.to_thread(_clean_privacy_sweep_sync, max_days, dry_run)
        result.merge(priv_res)

    # 4. Safe Registry Hygiene
    if cfg.monthly_registry_clean:
        reg_res = await asyncio.to_thread(_clean_registry_hygiene_sync, dry_run)
        result.merge(reg_res)

    return result


async def clean_temp_directory(
    temp_path: Path | None = None,
    max_age_hours: int = 24,
    dry_run: bool = False,
    config: TempCleanerConfig | None = None,
) -> TempCleanResult:
    """Safely clean unlocked files and empty directories in temporary and cache locations."""
    if config is not None and not config.enabled:
        return TempCleanResult()

    result = TempCleanResult()

    if config is not None:
        primary_temp = temp_path or config.get_resolved_temp_path()
        clean_win_temp = config.clean_windows_temp
        clean_dumps = config.clean_memory_dumps
        clean_ag = config.clean_antigravity_orphans
        dump_days = config.dump_max_age_days
        if max_age_hours == 24 and config.max_age_hours != 24:
            max_age_hours = config.max_age_hours
    else:
        primary_temp = temp_path or Path(tempfile.gettempdir()).resolve()
        clean_win_temp = False
        clean_dumps = False
        clean_ag = False
        dump_days = 7

    user_temp_res = await asyncio.to_thread(
        _clean_directory_tree_sync,
        primary_temp,
        max_age_hours,
        dry_run,
    )
    result.merge(user_temp_res)

    if clean_win_temp:
        sys_temp = Path(os.getenv("SystemRoot", r"C:\Windows")) / "Temp"
        if sys_temp.exists() and sys_temp != primary_temp:
            win_temp_res = await asyncio.to_thread(
                _clean_directory_tree_sync,
                sys_temp,
                max_age_hours,
                dry_run,
            )
            result.merge(win_temp_res)

    if clean_dumps:
        dump_res = await asyncio.to_thread(_clean_memory_dumps_sync, dump_days, dry_run)
        result.merge(dump_res)

    if clean_ag:
        ag_res = await asyncio.to_thread(_clean_antigravity_orphans_sync, None, dry_run)
        result.merge(ag_res)

    return result
