"""Phase 3, slice 1 — B+ tree tests.

Coverage: point lookup, ordered range scan, splitting (leaf and root), duplicate
keys spanning leaves, leaf-only delete, and persistence across a restart. Most
tests use a small max_keys (so splits happen with little data and the tree gets
several levels deep), plus one larger randomized test against the default order.
"""

import random

import pytest

from queryx.index.btree import DEFAULT_MAX_KEYS, BPlusTree
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import RowId
from queryx.storage.pager import Pager


@pytest.fixture
def pager(tmp_path):
    p = Pager(str(tmp_path / "index.qx"))
    yield p
    p.close()


def make_tree(pager, max_keys=4, capacity=16):
    return BPlusTree(BufferPool(pager, capacity=capacity), max_keys=max_keys)


def rid(n: int) -> RowId:
    """A deterministic RowId derived from a key, for round-trip assertions."""
    return RowId(page_no=n + 1, slot=n % 7)


# ---------------------------------------------------------------------------
# Basic point lookup
# ---------------------------------------------------------------------------


def test_search_empty_tree(pager):
    tree = make_tree(pager)
    assert tree.search(42) == []


def test_insert_and_search_single(pager):
    tree = make_tree(pager)
    tree.insert(10, rid(10))
    assert tree.search(10) == [rid(10)]
    assert tree.search(11) == []


def test_insert_many_then_search_each(pager):
    tree = make_tree(pager, max_keys=4)
    keys = list(range(1, 101))
    for k in keys:
        tree.insert(k, rid(k))
    for k in keys:
        assert tree.search(k) == [rid(k)], f"lost key {k}"
    assert tree.search(0) == []
    assert tree.search(101) == []


def test_constructor_rejects_bad_order(pager):
    with pytest.raises(ValueError):
        make_tree(pager, max_keys=1)


# ---------------------------------------------------------------------------
# Splitting / tree growth
# ---------------------------------------------------------------------------


def test_tree_grows_in_height_after_enough_inserts(pager):
    tree = make_tree(pager, max_keys=4)
    assert tree.height() == 1  # single leaf root
    for k in range(1, 51):
        tree.insert(k, rid(k))
    assert tree.height() >= 2  # root must have split into internal levels


def test_keys_stay_sorted_across_splits(pager):
    tree = make_tree(pager, max_keys=4)
    for k in [50, 10, 30, 20, 40, 5, 60, 25, 15, 35, 45, 55, 1, 99, 70]:
        tree.insert(k, rid(k))
    scanned = [k for k, _ in tree.range_scan()]
    assert scanned == sorted(scanned)
    assert scanned == sorted([50, 10, 30, 20, 40, 5, 60, 25, 15, 35, 45, 55, 1, 99, 70])


# ---------------------------------------------------------------------------
# Range scan
# ---------------------------------------------------------------------------


def test_range_scan_bounds_inclusive(pager):
    tree = make_tree(pager, max_keys=4)
    for k in range(1, 21):
        tree.insert(k, rid(k))
    got = [k for k, _ in tree.range_scan(5, 10)]
    assert got == [5, 6, 7, 8, 9, 10]


def test_range_scan_open_ended(pager):
    tree = make_tree(pager, max_keys=4)
    for k in range(1, 11):
        tree.insert(k, rid(k))
    assert [k for k, _ in tree.range_scan(low=7)] == [7, 8, 9, 10]
    assert [k for k, _ in tree.range_scan(high=3)] == [1, 2, 3]
    assert [k for k, _ in tree.range_scan()] == list(range(1, 11))


def test_range_scan_yields_correct_rowids(pager):
    tree = make_tree(pager, max_keys=4)
    for k in range(1, 11):
        tree.insert(k, rid(k))
    assert list(tree.range_scan(3, 5)) == [(3, rid(3)), (4, rid(4)), (5, rid(5))]


# ---------------------------------------------------------------------------
# Duplicate keys
# ---------------------------------------------------------------------------


def test_duplicate_keys_return_all_rowids(pager):
    tree = make_tree(pager, max_keys=4)
    dups = [RowId(page_no=100 + i, slot=i) for i in range(10)]
    for r in dups:
        tree.insert(7, r)
    found = tree.search(7)
    assert len(found) == 10
    assert set(found) == set(dups)


def test_duplicates_interleaved_with_other_keys(pager):
    tree = make_tree(pager, max_keys=4)
    for k in range(1, 30):
        tree.insert(k, rid(k))
    extra = [RowId(page_no=500 + i, slot=i) for i in range(8)]
    for r in extra:
        tree.insert(15, r)
    found = tree.search(15)
    assert rid(15) in found
    for r in extra:
        assert r in found
    assert len(found) == 9


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete_removes_entry(pager):
    tree = make_tree(pager, max_keys=4)
    for k in range(1, 21):
        tree.insert(k, rid(k))
    assert tree.delete(10, rid(10)) is True
    assert tree.search(10) == []
    # neighbors intact
    assert tree.search(9) == [rid(9)]
    assert tree.search(11) == [rid(11)]


def test_delete_absent_returns_false(pager):
    tree = make_tree(pager, max_keys=4)
    tree.insert(5, rid(5))
    assert tree.delete(5, rid(999)) is False  # right key, wrong rowid
    assert tree.delete(6, rid(6)) is False     # absent key
    assert tree.search(5) == [rid(5)]


def test_delete_one_of_duplicate_keys(pager):
    tree = make_tree(pager, max_keys=4)
    a, b, c = RowId(1, 1), RowId(2, 2), RowId(3, 3)
    for r in (a, b, c):
        tree.insert(8, r)
    assert tree.delete(8, b) is True
    remaining = tree.search(8)
    assert set(remaining) == {a, c}


# ---------------------------------------------------------------------------
# Persistence across restart
# ---------------------------------------------------------------------------


def test_tree_survives_restart(tmp_path):
    path = str(tmp_path / "persist_index.qx")
    keys = list(range(1, 201))

    pager = Pager(path)
    tree = BPlusTree(BufferPool(pager, capacity=8), max_keys=4)
    for k in keys:
        tree.insert(k, rid(k))
    tree.flush()
    saved_root = tree.root_page
    pager.close()

    pager2 = Pager(path)
    tree2 = BPlusTree(BufferPool(pager2, capacity=8))  # order read from meta page
    try:
        assert tree2.root_page == saved_root
        assert tree2.max_keys == 4
        for k in keys:
            assert tree2.search(k) == [rid(k)], f"lost key {k} after restart"
        assert [k for k, _ in tree2.range_scan()] == keys
    finally:
        pager2.close()


# ---------------------------------------------------------------------------
# Randomized stress vs. a reference dict (default order)
# ---------------------------------------------------------------------------


def test_randomized_against_reference(pager):
    tree = make_tree(pager, max_keys=DEFAULT_MAX_KEYS, capacity=64)
    rng = random.Random(1234)
    reference: dict[int, RowId] = {}
    keys = rng.sample(range(1, 5001), 2000)
    for k in keys:
        r = RowId(page_no=k, slot=k % 11)
        tree.insert(k, r)
        reference[k] = r

    for k in keys:
        assert tree.search(k) == [reference[k]]

    # ordered full scan matches sorted reference
    scanned = list(tree.range_scan())
    assert [k for k, _ in scanned] == sorted(reference)
    assert all(v == reference[k] for k, v in scanned)
