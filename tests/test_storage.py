"""Tests for core storage layer: SQLiteStore, checkpointer, and maintenance utilities."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import GetOp, ListNamespacesOp, MatchCondition, PutOp, SearchOp

from deskpilot.storage.checkpointer import (
    get_async_sqlite_checkpointer,
    get_sqlite_checkpointer,
)
from deskpilot.storage.maintenance import (
    get_storage_stats,
    prune_stale_checkpoints,
)
from deskpilot.storage.store import SQLiteStore, get_store


# ---------------------------------------------------------------------------
# SQLiteStore Unit Tests
# ---------------------------------------------------------------------------


def test_store_put_and_get(tmp_path: Path) -> None:
    db_file = tmp_path / "storage.db"
    store = get_store(db_file)

    ns = ("screenshots", "preferences")
    key = "routing"
    val = {"preferred_app": "slack", "threshold": 0.8}

    # Put item
    store.put(ns, key, val)

    # Get item
    item = store.get(ns, key)
    assert item is not None
    assert item.key == key
    assert item.namespace == ns
    assert item.value == val
    assert isinstance(item.created_at, datetime)
    assert isinstance(item.updated_at, datetime)

    # Update item
    val2 = {"preferred_app": "teams", "threshold": 0.9}
    store.put(ns, key, val2)
    updated_item = store.get(ns, key)
    assert updated_item is not None
    assert updated_item.value == val2
    assert updated_item.created_at == item.created_at
    assert updated_item.updated_at >= item.updated_at


def test_store_delete(tmp_path: Path) -> None:
    db_file = tmp_path / "storage.db"
    store = SQLiteStore(db_file)

    ns = ("bookmarks", "tags")
    store.put(ns, "tag1", {"color": "blue"})
    assert store.get(ns, "tag1") is not None

    # Deleting via put with None value (or store.delete)
    store.delete(ns, "tag1")
    assert store.get(ns, "tag1") is None


def test_store_persistence_across_instances(tmp_path: Path) -> None:
    db_file = tmp_path / "storage.db"

    store1 = SQLiteStore(db_file)
    store1.put(("persistent",), "k1", {"foo": "bar"})

    # Fresh instance pointing to same file
    store2 = SQLiteStore(db_file)
    item = store2.get(("persistent",), "k1")
    assert item is not None
    assert item.value == {"foo": "bar"}


def test_store_search_and_filters(tmp_path: Path) -> None:
    db_file = tmp_path / "storage.db"
    store = SQLiteStore(db_file)

    store.put(("users", "alice"), "profile", {"role": "admin", "age": 30})
    store.put(("users", "bob"), "profile", {"role": "user", "age": 25})
    store.put(("users", "carol"), "profile", {"role": "user", "age": 35})
    store.put(("settings",), "system", {"mode": "auto"})

    # Search with prefix
    res = store.search(("users",))
    assert len(res) == 3
    keys = {item.key for item in res}
    assert keys == {"profile"}

    # Search with filter exact match
    res_admin = store.search(("users",), filter={"role": "admin"})
    assert len(res_admin) == 1
    assert res_admin[0].namespace == ("users", "alice")

    # Search with comparison filter
    res_age = store.search(("users",), filter={"age": {"$gt": 28}})
    assert len(res_age) == 2
    namespaces = {item.namespace for item in res_age}
    assert namespaces == {("users", "alice"), ("users", "carol")}

    # Pagination: limit and offset
    res_paged = store.search(("users",), limit=2, offset=1)
    assert len(res_paged) == 2


def test_store_list_namespaces(tmp_path: Path) -> None:
    db_file = tmp_path / "storage.db"
    store = SQLiteStore(db_file)

    store.put(("a", "b", "c"), "k1", {"v": 1})
    store.put(("a", "b", "d"), "k2", {"v": 2})
    store.put(("a", "x"), "k3", {"v": 3})
    store.put(("z", "b"), "k4", {"v": 4})

    all_ns = store.list_namespaces()
    assert ("a", "b", "c") in all_ns
    assert ("a", "b", "d") in all_ns
    assert ("a", "x") in all_ns
    assert ("z", "b") in all_ns

    # Prefix match
    prefix_ns = store.list_namespaces(prefix=("a", "b"))
    assert prefix_ns == [("a", "b", "c"), ("a", "b", "d")]

    # Suffix match via match_conditions
    op = ListNamespacesOp(
        match_conditions=(MatchCondition(match_type="suffix", path=("b",)),)
    )
    suffix_ns = store.batch([op])[0]
    assert suffix_ns == [("z", "b")]

    # Max depth
    depth_ns = store.list_namespaces(max_depth=2)
    assert depth_ns == [("a", "b"), ("a", "x"), ("z", "b")]


@pytest.mark.asyncio
async def test_store_async_operations(tmp_path: Path) -> None:
    db_file = tmp_path / "async_storage.db"
    store = SQLiteStore(db_file)

    ns = ("async", "test")
    await store.aput(ns, "key1", {"status": "ok"})
    item = await store.aget(ns, "key1")
    assert item is not None
    assert item.value == {"status": "ok"}

    items = await store.asearch(ns)
    assert len(items) == 1
    assert items[0].key == "key1"


# ---------------------------------------------------------------------------
# Checkpointer Unit Tests
# ---------------------------------------------------------------------------


class SampleState(TypedDict):
    counter: int


def test_sqlite_checkpointer_integration(tmp_path: Path) -> None:
    db_file = tmp_path / "checkpoints.db"
    cp = get_sqlite_checkpointer(db_file)

    builder = StateGraph(SampleState)
    builder.add_node("increment", lambda s: {"counter": s["counter"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)

    graph = builder.compile(checkpointer=cp)

    config = {"configurable": {"thread_id": "thread-1"}}
    result = graph.invoke({"counter": 10}, config=config)
    assert result == {"counter": 11}

    # Resume/read thread state
    state = graph.get_state(config)
    assert state.values == {"counter": 11}

    # Second step execution on same thread
    result2 = graph.invoke({"counter": 20}, config=config)
    assert result2 == {"counter": 21}
    state2 = graph.get_state(config)
    assert state2.values == {"counter": 21}

    # Verify directly from SQLite
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    count = cur.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id = 'thread-1'").fetchone()[0]
    assert count >= 2
    conn.close()


@pytest.mark.asyncio
async def test_async_sqlite_checkpointer(tmp_path: Path) -> None:
    db_file = tmp_path / "async_checkpoints.db"
    cp = await get_async_sqlite_checkpointer(db_file)

    builder = StateGraph(SampleState)
    builder.add_node("increment", lambda s: {"counter": s["counter"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)

    graph = builder.compile(checkpointer=cp)
    config = {"configurable": {"thread_id": "thread-async-1"}}

    result = await graph.ainvoke({"counter": 5}, config=config)
    assert result == {"counter": 6}

    state = await graph.aget_state(config)
    assert state.values == {"counter": 6}

    # Clean up connection
    await cp.conn.close()


# ---------------------------------------------------------------------------
# Maintenance & Stats Unit Tests
# ---------------------------------------------------------------------------


def test_prune_stale_checkpoints_guarantees_store_intact(tmp_path: Path) -> None:
    db_file = tmp_path / "hybrid_storage.db"

    # Setup store items
    store = SQLiteStore(db_file)
    store.put(("rules", "active"), "rule_1", {"enabled": True})
    store.put(("users",), "admin", {"email": "admin@example.com"})

    # Setup checkpoints
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()

    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=20)).isoformat()
    recent_ts = (now - timedelta(days=2)).isoformat()

    # Stale checkpoint
    config_old = {"configurable": {"thread_id": "old-thread", "checkpoint_ns": ""}}
    saver.put(
        config_old,
        {"v": 1, "id": "cp-old", "ts": old_ts, "channel_values": {}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
        {"source": "test"},
        {},
    )

    # Recent checkpoint
    config_recent = {"configurable": {"thread_id": "recent-thread", "checkpoint_ns": ""}}
    saver.put(
        config_recent,
        {"v": 1, "id": "cp-recent", "ts": recent_ts, "channel_values": {}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
        {"source": "test"},
        {},
    )
    conn.close()

    # Prune checkpoints older than 14 days
    pruned = prune_stale_checkpoints(db_file, max_age_days=14)
    assert pruned == 1

    # Verify surviving checkpoint
    conn_verify = sqlite3.connect(str(db_file))
    cur = conn_verify.cursor()
    surviving_cps = cur.execute("SELECT checkpoint_id FROM checkpoints").fetchall()
    assert len(surviving_cps) == 1
    assert surviving_cps[0][0] == "cp-recent"

    # CRITICAL: Verify store items were NEVER touched
    items_count = cur.execute("SELECT COUNT(*) FROM store_items").fetchone()[0]
    assert items_count == 2
    conn_verify.close()

    # Verify store can still read both items
    assert store.get(("rules", "active"), "rule_1") is not None
    assert store.get(("users",), "admin") is not None


def test_get_storage_stats(tmp_path: Path) -> None:
    db_file = tmp_path / "stats_storage.db"

    # Check non-existent DB
    stats_empty = get_storage_stats(tmp_path / "non_existent.db")
    assert stats_empty["file_size_bytes"] == 0
    assert stats_empty["checkpoint_count"] == 0
    assert stats_empty["memories_count"] == 0
    assert stats_empty["namespaces"] == {}

    # Populate data
    store = SQLiteStore(db_file)
    store.put(("memories", "general"), "m1", {"note": "test"})
    store.put(("memories", "general"), "m2", {"note": "test2"})
    store.put(("config",), "theme", {"dark": True})

    cp = get_sqlite_checkpointer(db_file)
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
    cp.put(
        config,
        {"v": 1, "id": "c1", "ts": datetime.now(timezone.utc).isoformat(), "channel_values": {}, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
        {},
        {},
    )

    stats = get_storage_stats(db_file)
    assert stats["db_path"] == str(db_file)
    assert stats["file_size_bytes"] > 0
    assert "KB" in stats["file_size_formatted"] or "B" in stats["file_size_formatted"]
    assert stats["checkpoint_count"] == 1
    assert stats["memories_count"] == 3
    assert stats["namespaces"]["memories/general"] == 2
    assert stats["namespaces"]["config"] == 1
