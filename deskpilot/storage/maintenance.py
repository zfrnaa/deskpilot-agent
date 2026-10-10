"""Maintenance and statistics utilities for DeskPilot storage SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver


def _format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def prune_stale_checkpoints(db_path: Path, max_age_days: int = 14) -> int:
    """Prune checkpoints older than max_age_days.

    Deletes matching entries from 'checkpoints' and associated 'writes' tables.
    Runs VACUUM on the SQLite database to reclaim free space.
    Guarantees that the 'store_items' table is NEVER touched or deleted.

    Returns the count of pruned checkpoints.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    pruned_count = 0

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        # Check if checkpoints table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'")
        if not cur.fetchone():
            return 0

        # Create dummy saver to deserialize checkpoints
        saver = SqliteSaver(conn)

        cur.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, type, checkpoint, metadata FROM checkpoints"
        )
        rows = cur.fetchall()

        stale_keys: list[tuple[str, str, str]] = []
        for row in rows:
            ts_dt: datetime | None = None

            # 1. Try parsing metadata JSON
            raw_meta = row["metadata"]
            if raw_meta:
                try:
                    if isinstance(raw_meta, (bytes, bytearray)):
                        meta = json.loads(raw_meta.decode("utf-8"))
                    elif isinstance(raw_meta, str):
                        meta = json.loads(raw_meta)
                    else:
                        meta = raw_meta
                    if isinstance(meta, dict) and "ts" in meta:
                        ts_dt = datetime.fromisoformat(meta["ts"])
                except Exception:
                    pass

            # 2. Try deserializing checkpoint blob if metadata had no valid ts
            if ts_dt is None and row["checkpoint"] is not None and row["type"] is not None:
                try:
                    cp_obj = saver.serde.loads_typed((row["type"], row["checkpoint"]))
                    if isinstance(cp_obj, dict) and "ts" in cp_obj:
                        ts_val = cp_obj["ts"]
                        if isinstance(ts_val, str):
                            ts_dt = datetime.fromisoformat(ts_val)
                        elif isinstance(ts_val, datetime):
                            ts_dt = ts_val
                except Exception:
                    pass

            # Compare with cutoff
            if ts_dt is not None:
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt < cutoff:
                    stale_keys.append((row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"]))

        if stale_keys:
            # Delete associated writes and checkpoints
            for thread_id, checkpoint_ns, checkpoint_id in stale_keys:
                cur.execute(
                    """
                    DELETE FROM writes
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                )
                cur.execute(
                    """
                    DELETE FROM checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                )
            conn.commit()
            pruned_count = len(stale_keys)

        # VACUUM to reclaim disk space (outside transaction)
        conn.execute("VACUUM")
    finally:
        conn.close()

    return pruned_count


def get_storage_stats(db_path: Path) -> dict[str, Any]:
    """Get metrics and statistics about the SQLite storage file.

    Returns:
        {
            "db_path": str,
            "file_size_bytes": int,
            "file_size_formatted": str,
            "checkpoint_count": int,
            "memories_count": int,
            "namespaces": dict[str, int]
        }
    """
    db_path = Path(db_path)
    file_size_bytes = db_path.stat().st_size if db_path.exists() else 0
    file_size_formatted = _format_size(file_size_bytes)

    checkpoint_count = 0
    memories_count = 0
    namespaces: dict[str, int] = {}

    if db_path.exists():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            cur = conn.cursor()

            # Checkpoints count
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) FROM checkpoints")
                checkpoint_count = cur.fetchone()[0]

            # Store memories count & namespace breakdown
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='store_items'")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) FROM store_items")
                memories_count = cur.fetchone()[0]

                cur.execute("SELECT namespace, COUNT(*) FROM store_items GROUP BY namespace")
                for ns_str, cnt in cur.fetchall():
                    try:
                        ns_tuple = tuple(json.loads(ns_str))
                        ns_key = "/".join(ns_tuple) if ns_tuple else "/"
                    except Exception:
                        ns_key = ns_str
                    namespaces[ns_key] = cnt
        finally:
            conn.close()

    return {
        "db_path": str(db_path),
        "file_size_bytes": file_size_bytes,
        "file_size_formatted": file_size_formatted,
        "checkpoint_count": checkpoint_count,
        "memories_count": memories_count,
        "namespaces": namespaces,
    }
