"""Phase 2, slice 2 — pager tests.

The centerpiece is test_data_survives_restart: write pages, close the pager
(simulating process exit), open a brand-new pager on the same file, and read the
data back. That round trip through disk is the Phase 2 deliverable in miniature.
"""

import struct

import pytest

from queryx.storage.page import PAGE_SIZE, ColumnType, Page, deserialize_row, serialize_row
from queryx.storage.pager import Pager


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.qx")


def _page_with(*records: bytes) -> Page:
    page = Page.empty()
    for r in records:
        page.insert_record(r)
    return page


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_new_file_has_only_the_header_page(db_path):
    with Pager(db_path) as pager:
        assert pager.num_pages == 1  # page 0 = header
        assert pager.free_page_count == 0


def test_page_zero_is_reserved(db_path):
    with Pager(db_path) as pager:
        n = pager.allocate_page()
        with pytest.raises(ValueError):
            pager.read_page(0)
        with pytest.raises(ValueError):
            pager.write_page(0, Page.empty())
        assert n >= 1


def test_reopening_empty_file_does_not_truncate(db_path):
    Pager(db_path).close()
    with Pager(db_path) as pager:
        assert pager.num_pages == 1


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def test_allocate_returns_increasing_page_numbers(db_path):
    with Pager(db_path) as pager:
        assert pager.allocate_page() == 1
        assert pager.allocate_page() == 2
        assert pager.allocate_page() == 3
        assert pager.num_pages == 4  # header + 3 data pages


def test_allocated_page_is_empty(db_path):
    with Pager(db_path) as pager:
        n = pager.allocate_page()
        page = pager.read_page(n)
        assert page.num_slots == 0


def test_read_nonexistent_page_raises(db_path):
    with Pager(db_path) as pager:
        pager.allocate_page()  # page 1 exists
        with pytest.raises(IndexError):
            pager.read_page(99)


# ---------------------------------------------------------------------------
# Read / write round trip
# ---------------------------------------------------------------------------


def test_write_then_read_same_process(db_path):
    with Pager(db_path) as pager:
        n = pager.allocate_page()
        pager.write_page(n, _page_with(b"hello", b"world"))
        page = pager.read_page(n)
        assert page.get_record(0) == b"hello"
        assert page.get_record(1) == b"world"


def test_writes_to_different_pages_are_independent(db_path):
    with Pager(db_path) as pager:
        a = pager.allocate_page()
        b = pager.allocate_page()
        pager.write_page(a, _page_with(b"page-a"))
        pager.write_page(b, _page_with(b"page-b"))
        assert pager.read_page(a).get_record(0) == b"page-a"
        assert pager.read_page(b).get_record(0) == b"page-b"


# ---------------------------------------------------------------------------
# THE deliverable: durability across a restart
# ---------------------------------------------------------------------------


def test_data_survives_restart(db_path):
    schema = [ColumnType.INT, ColumnType.TEXT]
    rows = [(1, "alice"), (2, "bob"), (3, "carol")]

    # Session 1: write rows across two pages, then "exit" by closing.
    pager = Pager(db_path)
    p1 = pager.allocate_page()
    p2 = pager.allocate_page()
    pager.write_page(p1, _page_with(serialize_row(schema, rows[0]), serialize_row(schema, rows[1])))
    pager.write_page(p2, _page_with(serialize_row(schema, rows[2])))
    pager.close()

    # Session 2: a fresh pager on the same file reads the rows back from disk.
    pager2 = Pager(db_path)
    try:
        assert pager2.num_pages == 3
        page1 = pager2.read_page(p1)
        page2 = pager2.read_page(p2)
        assert deserialize_row(schema, page1.get_record(0)) == rows[0]
        assert deserialize_row(schema, page1.get_record(1)) == rows[1]
        assert deserialize_row(schema, page2.get_record(0)) == rows[2]
    finally:
        pager2.close()


# ---------------------------------------------------------------------------
# Free-page tracking
# ---------------------------------------------------------------------------


def test_freed_page_is_reused(db_path):
    with Pager(db_path) as pager:
        a = pager.allocate_page()
        pager.allocate_page()
        pager.free_page(a)
        assert pager.free_page_count == 1
        reused = pager.allocate_page()
        assert reused == a
        assert pager.free_page_count == 0


def test_reused_page_is_zeroed(db_path):
    with Pager(db_path) as pager:
        a = pager.allocate_page()
        pager.write_page(a, _page_with(b"stale-data"))
        pager.free_page(a)
        reused = pager.allocate_page()
        assert reused == a
        assert pager.read_page(reused).num_slots == 0  # no stale records


def test_double_free_is_idempotent(db_path):
    with Pager(db_path) as pager:
        a = pager.allocate_page()
        pager.free_page(a)
        pager.free_page(a)
        assert pager.free_page_count == 1


def test_free_list_persists_across_restart(db_path):
    pager = Pager(db_path)
    a = pager.allocate_page()
    pager.allocate_page()
    pager.free_page(a)
    pager.close()

    with Pager(db_path) as pager2:
        assert pager2.free_page_count == 1
        assert pager2.allocate_page() == a  # the freed page is reused after restart


# ---------------------------------------------------------------------------
# Corruption / format guards
# ---------------------------------------------------------------------------


def test_bad_magic_is_rejected(db_path):
    with open(db_path, "wb") as f:
        f.write(b"NOPE" + bytes(PAGE_SIZE - 4))
    with pytest.raises(ValueError):
        Pager(db_path)


def test_unsupported_version_is_rejected(db_path):
    data = bytearray(PAGE_SIZE)
    struct.pack_into("<4sHI", data, 0, b"QRYX", 999, 0)
    with open(db_path, "wb") as f:
        f.write(data)
    with pytest.raises(ValueError):
        Pager(db_path)
