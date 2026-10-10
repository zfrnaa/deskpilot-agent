"""Checkpointer provider utilities for LangGraph SQLite persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver


def get_sqlite_checkpointer(db_path: Path | None = None) -> BaseCheckpointSaver:
    """Get an initialized synchronous SQLite checkpointer.

    Defaults to ~/.deskpilot/storage.db if db_path is not specified.
    """
    if db_path is None:
        db_path = Path.home() / ".deskpilot" / "storage.db"
    else:
        db_path = Path(db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


async def get_async_sqlite_checkpointer(
    db_path: Path | None = None,
) -> Any:
    """Get an initialized asynchronous SQLite checkpointer using aiosqlite.

    Defaults to ~/.deskpilot/storage.db if db_path is not specified.
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if db_path is None:
        db_path = Path.home() / ".deskpilot" / "storage.db"
    else:
        db_path = Path(db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver
