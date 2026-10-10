"""SQLite-backed long-term persistence BaseStore implementation for LangGraph."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    MatchCondition,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)


def _serialize_namespace(namespace: tuple[str, ...]) -> str:
    """Serialize a namespace tuple to JSON string for SQLite storage."""
    return json.dumps(list(namespace))


def _deserialize_namespace(raw: str) -> tuple[str, ...]:
    """Deserialize a namespace JSON string to a tuple."""
    return tuple(json.loads(raw))


def _compare_filter_value(val: Any, fval: Any) -> bool:
    """Helper to check filter condition against value."""
    if isinstance(fval, dict):
        for op_k, op_v in fval.items():
            if op_k == "$eq" and not (val == op_v):
                return False
            elif op_k == "$ne" and not (val != op_v):
                return False
            elif op_k == "$gt" and not (val is not None and val > op_v):
                return False
            elif op_k == "$gte" and not (val is not None and val >= op_v):
                return False
            elif op_k == "$lt" and not (val is not None and val < op_v):
                return False
            elif op_k == "$lte" and not (val is not None and val <= op_v):
                return False
        return True
    return val == fval


def _matches_filter(value: dict[str, Any], filter_dict: dict[str, Any] | None) -> bool:
    """Check if item value matches the given filter."""
    if not filter_dict:
        return True
    for k, v in filter_dict.items():
        if not _compare_filter_value(value.get(k), v):
            return False
    return True


def _does_match_condition(condition: MatchCondition, ns: tuple[str, ...]) -> bool:
    """Check whether a namespace matches a MatchCondition."""
    match_type = condition.match_type
    path = condition.path
    if len(ns) < len(path):
        return False
    if match_type == "prefix":
        for n_elem, p_elem in zip(ns, path, strict=False):
            if p_elem == "*":
                continue
            if n_elem != p_elem:
                return False
        return True
    elif match_type == "suffix":
        for n_elem, p_elem in zip(reversed(ns), reversed(path), strict=False):
            if p_elem == "*":
                continue
            if n_elem != p_elem:
                return False
        return True
    return False


class SQLiteStore(BaseStore):
    """Persistent SQLite store implementing LangGraph's BaseStore."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_items (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (namespace, key)
                );
                CREATE INDEX IF NOT EXISTS idx_store_namespace ON store_items (namespace);
                """
            )

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        """Execute a batch of operations synchronously."""
        results: list[Result] = []
        with self._get_connection() as conn:
            cur = conn.cursor()
            for op in ops:
                if isinstance(op, PutOp):
                    results.append(self._handle_put(cur, op))
                elif isinstance(op, GetOp):
                    results.append(self._handle_get(cur, op))
                elif isinstance(op, SearchOp):
                    results.append(self._handle_search(cur, op))
                elif isinstance(op, ListNamespacesOp):
                    results.append(self._handle_list_namespaces(cur, op))
                else:
                    raise ValueError(f"Unknown operation type: {type(op)}")
            conn.commit()
        return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        """Execute a batch of operations asynchronously."""
        # SQLite queries are local and fast; delegate to batch()
        return self.batch(ops)

    def _handle_put(self, cur: sqlite3.Cursor, op: PutOp) -> None:
        ns_str = _serialize_namespace(op.namespace)
        if op.value is None:
            cur.execute(
                "DELETE FROM store_items WHERE namespace = ? AND key = ?",
                (ns_str, op.key),
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        cur.execute(
            "SELECT created_at FROM store_items WHERE namespace = ? AND key = ?",
            (ns_str, op.key),
        )
        existing = cur.fetchone()
        created_at = existing["created_at"] if existing else now_iso
        val_str = json.dumps(op.value)

        cur.execute(
            """
            INSERT INTO store_items (namespace, key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(namespace, key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (ns_str, op.key, val_str, created_at, now_iso),
        )

    def _handle_get(self, cur: sqlite3.Cursor, op: GetOp) -> Item | None:
        ns_str = _serialize_namespace(op.namespace)
        cur.execute(
            "SELECT namespace, key, value, created_at, updated_at FROM store_items WHERE namespace = ? AND key = ?",
            (ns_str, op.key),
        )
        row = cur.fetchone()
        if not row:
            return None
        return Item(
            value=json.loads(row["value"]),
            key=row["key"],
            namespace=_deserialize_namespace(row["namespace"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _handle_search(self, cur: sqlite3.Cursor, op: SearchOp) -> list[SearchItem]:
        cur.execute("SELECT namespace, key, value, created_at, updated_at FROM store_items")
        rows = cur.fetchall()

        matched_items: list[SearchItem] = []
        prefix = op.namespace_prefix
        prefix_len = len(prefix)

        for row in rows:
            ns = _deserialize_namespace(row["namespace"])
            # Prefix check
            if prefix and (len(ns) < prefix_len or ns[:prefix_len] != prefix):
                continue

            val = json.loads(row["value"])
            if op.filter and not _matches_filter(val, op.filter):
                continue

            matched_items.append(
                SearchItem(
                    namespace=ns,
                    key=row["key"],
                    value=val,
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    score=None,
                )
            )

        offset = op.offset or 0
        limit = op.limit if op.limit is not None else len(matched_items)
        return matched_items[offset : offset + limit]

    def _handle_list_namespaces(
        self, cur: sqlite3.Cursor, op: ListNamespacesOp
    ) -> list[tuple[str, ...]]:
        cur.execute("SELECT DISTINCT namespace FROM store_items")
        rows = cur.fetchall()

        namespaces = [_deserialize_namespace(r["namespace"]) for r in rows]

        if op.match_conditions:
            namespaces = [
                ns
                for ns in namespaces
                if all(_does_match_condition(cond, ns) for cond in op.match_conditions)
            ]

        if op.max_depth is not None:
            namespaces = sorted({ns[: op.max_depth] for ns in namespaces})
        else:
            namespaces = sorted(namespaces)

        offset = op.offset or 0
        limit = op.limit if op.limit is not None else len(namespaces)
        return namespaces[offset : offset + limit]


def get_store(db_path: Path | None = None) -> BaseStore:
    """Get or create an embedded SQLiteStore instance.

    Defaults to ~/.deskpilot/storage.db if db_path is not specified.
    """
    if db_path is None:
        db_path = Path.home() / ".deskpilot" / "storage.db"
    return SQLiteStore(db_path=db_path)
