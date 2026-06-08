"""Phase 2, slice 4 — heap file tests, including the full Phase 2 deliverable.

test_full_phase2_deliverable is the proof the storage engine works end to end:
insert typed rows, close everything (process exit), reopen from scratch, and
read every row back from disk in order.
"""

import pytest

from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import MAX_RECORD_SIZE, HeapFile, RowId
from queryx.storage.page import ColumnType, deserialize_row, serialize_row
from queryx.storage.pager import Pager


@pytest.fixture
def heap(tmp_path):
    pager = Pager(str(tmp_path / "heap.qx"))
    pool = BufferPool(pager, capacity=8)
    yield HeapFile(pool)
    pager.close()


# ---------------------------------------------------------------------------
# Insert / get
# ---------------------------------------------------------------------------


def test_insert_returns_rowid_and_get_round_trips(heap):
    rid = heap.insert(b"hello")
    assert isinstance(rid, RowId)
    assert heap.get(rid) == b"hello"


def test_inserts_get_distinct_rowids(heap):
    rids = [heap.insert(f"row-{i}".encode()) for i in range(5)]
    assert len(set(rids)) == 5
    for i, rid in enumerate(rids):
        assert heap.get(rid) == f"row-{i}".encode()


def test_insert_empty_record_rejected(heap):
    with pytest.raises(ValueError):
        heap.insert(b"")


def test_insert_oversized_record_rejected(heap):
    with pytest.raises(ValueError):
        heap.insert(b"x" * (MAX_RECORD_SIZE + 1))


def test_insert_spills_onto_new_pages(heap):
    # Each record is ~half a page, so a handful forces multiple pages.
    big = b"x" * (MAX_RECORD_SIZE // 2)
    rids = [heap.insert(big) for _ in range(5)]
    pages_used = {rid.page_no for rid in rids}
    assert len(pages_used) >= 2  # spilled past a single page
    for rid in rids:
        assert heap.get(rid) == big


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_yields_every_live_row(heap):
    inserted = {}
    for i in range(20):
        rid = heap.insert(f"v{i}".encode())
        inserted[rid] = f"v{i}".encode()
    scanned = dict(heap.scan())
    assert scanned == inserted


def test_scan_empty_heap_is_empty(heap):
    assert list(heap.scan()) == []


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_from_get_and_scan(heap):
    keep = heap.insert(b"keep")
    drop = heap.insert(b"drop")
    assert heap.delete(drop) is True
    assert heap.get(drop) is None
    assert dict(heap.scan()) == {keep: b"keep"}


def test_delete_absent_row_is_false(heap):
    rid = heap.insert(b"x")
    heap.delete(rid)
    assert heap.delete(rid) is False


# ---------------------------------------------------------------------------
# THE Phase 2 deliverable: insert -> restart -> read back
# ---------------------------------------------------------------------------


def test_full_phase2_deliverable(tmp_path):
    path = str(tmp_path / "users.qx")
    schema = [ColumnType.INT, ColumnType.TEXT, ColumnType.INT]
    rows = [(i, f"user{i}", 20 + i) for i in range(50)]

    # --- Session 1: insert 50 typed rows, then "exit". ---
    pager = Pager(path)
    pool = BufferPool(pager, capacity=4)  # small pool -> forces eviction/I/O
    heap = HeapFile(pool)
    rids = [heap.insert(serialize_row(schema, row)) for row in rows]
    heap.flush()
    pager.close()

    # --- Session 2: brand-new process state, read everything back from disk. ---
    pager2 = Pager(path)
    pool2 = BufferPool(pager2, capacity=4)
    heap2 = HeapFile(pool2)
    try:
        # Point reads by RowId survive the restart.
        for rid, row in zip(rids, rows):
            record = heap2.get(rid)
            assert record is not None
            assert deserialize_row(schema, record) == row

        # A full scan recovers exactly the 50 rows we stored.
        recovered = sorted(deserialize_row(schema, rec) for _, rec in heap2.scan())
        assert recovered == sorted(rows)
    finally:
        pager2.close()


def test_new_inserts_after_restart_append(tmp_path):
    path = str(tmp_path / "append.qx")

    pager = Pager(path)
    heap = HeapFile(BufferPool(pager, capacity=8))
    first = heap.insert(b"before-restart")
    heap.flush()
    pager.close()

    pager2 = Pager(path)
    heap2 = HeapFile(BufferPool(pager2, capacity=8))
    try:
        second = heap2.insert(b"after-restart")
        heap2.flush()
        # Both rows coexist; the old one was not clobbered.
        assert heap2.get(first) == b"before-restart"
        assert heap2.get(second) == b"after-restart"
        assert dict(heap2.scan()) == {first: b"before-restart", second: b"after-restart"}
    finally:
        pager2.close()
