"""Phase 3, slice 2 — hash index tests.

Coverage: point lookup, duplicates, delete, overflow-chain handling (forced with
a single bucket), persistence across a restart, and the deliberate absence of
ordering (a hash index has no range scan — search results carry no key order).
"""

import random

import pytest

from queryx.index.hash_index import CAPACITY, HashIndex
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import RowId
from queryx.storage.pager import Pager


@pytest.fixture
def pager(tmp_path):
    p = Pager(str(tmp_path / "hash.qx"))
    yield p
    p.close()


def make_index(pager, num_buckets=64, capacity=64):
    return HashIndex(BufferPool(pager, capacity=capacity), num_buckets=num_buckets)


def rid(n: int) -> RowId:
    return RowId(page_no=n + 1, slot=n % 7)


# ---------------------------------------------------------------------------
# Point lookup
# ---------------------------------------------------------------------------


def test_search_empty(pager):
    idx = make_index(pager)
    assert idx.search(123) == []


def test_insert_and_search(pager):
    idx = make_index(pager)
    idx.insert(10, rid(10))
    assert idx.search(10) == [rid(10)]
    assert idx.search(11) == []


def test_many_keys_across_buckets(pager):
    idx = make_index(pager, num_buckets=16)
    keys = list(range(1, 501))
    for k in keys:
        idx.insert(k, rid(k))
    for k in keys:
        assert idx.search(k) == [rid(k)], f"lost key {k}"
    assert idx.search(0) == []
    assert idx.search(999) == []


def test_negative_keys(pager):
    idx = make_index(pager, num_buckets=8)
    for k in (-5, -100, -1, -99999):
        idx.insert(k, rid(abs(k)))
    for k in (-5, -100, -1, -99999):
        assert idx.search(k) == [rid(abs(k))]


def test_constructor_rejects_zero_buckets(pager):
    with pytest.raises(ValueError):
        make_index(pager, num_buckets=0)


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_duplicate_keys(pager):
    idx = make_index(pager, num_buckets=8)
    dups = [RowId(page_no=200 + i, slot=i) for i in range(12)]
    for r in dups:
        idx.insert(42, r)
    found = idx.search(42)
    assert len(found) == 12
    assert set(found) == set(dups)


# ---------------------------------------------------------------------------
# Overflow chaining (single bucket forces a long chain)
# ---------------------------------------------------------------------------


def test_overflow_chain(pager):
    idx = make_index(pager, num_buckets=1)  # everything collides into one bucket
    n = CAPACITY * 2 + 5  # spans at least three pages in the chain
    for k in range(n):
        idx.insert(k, rid(k))
    for k in range(n):
        assert idx.search(k) == [rid(k)], f"lost key {k} in overflow chain"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def test_delete(pager):
    idx = make_index(pager, num_buckets=8)
    for k in range(1, 21):
        idx.insert(k, rid(k))
    assert idx.delete(10, rid(10)) is True
    assert idx.search(10) == []
    assert idx.search(9) == [rid(9)]


def test_delete_absent(pager):
    idx = make_index(pager, num_buckets=8)
    idx.insert(5, rid(5))
    assert idx.delete(5, rid(999)) is False  # right key, wrong rowid
    assert idx.delete(6, rid(6)) is False     # absent key
    assert idx.search(5) == [rid(5)]


def test_delete_from_overflow_chain(pager):
    idx = make_index(pager, num_buckets=1)
    n = CAPACITY + 10  # forces an overflow page
    for k in range(n):
        idx.insert(k, rid(k))
    # delete a key that must live on the overflow page
    target = n - 1
    assert idx.delete(target, rid(target)) is True
    assert idx.search(target) == []
    # everything else still present
    for k in range(n - 1):
        assert idx.search(k) == [rid(k)]


def test_delete_one_of_duplicates(pager):
    idx = make_index(pager, num_buckets=4)
    a, b, c = RowId(1, 1), RowId(2, 2), RowId(3, 3)
    for r in (a, b, c):
        idx.insert(7, r)
    assert idx.delete(7, b) is True
    assert set(idx.search(7)) == {a, c}


# ---------------------------------------------------------------------------
# Persistence across restart
# ---------------------------------------------------------------------------


def test_index_survives_restart(tmp_path):
    path = str(tmp_path / "persist_hash.qx")
    keys = list(range(1, 301))

    pager = Pager(path)
    idx = HashIndex(BufferPool(pager, capacity=16), num_buckets=16)
    for k in keys:
        idx.insert(k, rid(k))
    idx.flush()
    pager.close()

    pager2 = Pager(path)
    idx2 = HashIndex(BufferPool(pager2, capacity=16))  # num_buckets from meta
    try:
        assert idx2.num_buckets == 16
        for k in keys:
            assert idx2.search(k) == [rid(k)], f"lost key {k} after restart"
    finally:
        pager2.close()


# ---------------------------------------------------------------------------
# Randomized stress vs. a reference dict
# ---------------------------------------------------------------------------


def test_randomized_against_reference(pager):
    idx = make_index(pager, num_buckets=64, capacity=128)
    rng = random.Random(99)
    reference: dict[int, RowId] = {}
    keys = rng.sample(range(1, 10000), 3000)
    for k in keys:
        r = RowId(page_no=k, slot=k % 13)
        idx.insert(k, r)
        reference[k] = r
    for k in keys:
        assert idx.search(k) == [reference[k]]
    # a key never inserted is absent
    assert idx.search(10001) == []
