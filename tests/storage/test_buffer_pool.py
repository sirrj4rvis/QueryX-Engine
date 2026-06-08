"""Phase 2, slice 3 — buffer pool tests.

These prove the three mechanisms the pool exists for: caching (a second access
is a hit, same object), lazy write-back (dirty pages reach disk only on flush or
eviction), and LRU eviction (the least-recently-used page is the victim, and a
dirty victim is written out first).
"""

import pytest

from queryx.storage.buffer_pool import BufferPool
from queryx.storage.page import Page
from queryx.storage.pager import Pager


@pytest.fixture
def pager(tmp_path):
    p = Pager(str(tmp_path / "test.qx"))
    yield p
    p.close()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_second_access_is_a_cache_hit_same_object(pager):
    pool = BufferPool(pager, capacity=8)
    n, _ = pool.new_page()
    first = pool.get_page(n)
    second = pool.get_page(n)
    assert first is second  # identical cached object, not a re-read
    assert pool.hits >= 1


def test_miss_then_hit_counters(pager):
    n = pager.allocate_page()
    pool = BufferPool(pager, capacity=8)
    assert pool.misses == 0
    pool.get_page(n)  # not cached yet -> miss
    pool.get_page(n)  # cached -> hit
    assert pool.misses == 1
    assert pool.hits == 1


def test_capacity_must_be_positive(pager):
    with pytest.raises(ValueError):
        BufferPool(pager, capacity=0)


# ---------------------------------------------------------------------------
# Dirty tracking + write-back
# ---------------------------------------------------------------------------


def test_changes_are_not_written_until_flush(pager):
    pool = BufferPool(pager, capacity=8)
    n, page = pool.new_page()
    page.insert_record(b"buffered")
    pool.mark_dirty(n)
    assert pool.dirty_count == 1

    # Read the raw page directly from the pager: the change is NOT there yet.
    assert pager.read_page(n).num_slots == 0

    pool.flush_all()
    assert pool.dirty_count == 0
    assert pager.read_page(n).get_record(0) == b"buffered"


def test_flush_page_writes_only_that_page(pager):
    pool = BufferPool(pager, capacity=8)
    a, pa = pool.new_page()
    b, pb = pool.new_page()
    pa.insert_record(b"aaa")
    pb.insert_record(b"bbb")
    pool.mark_dirty(a)
    pool.mark_dirty(b)

    pool.flush_page(a)
    assert pager.read_page(a).get_record(0) == b"aaa"
    assert pager.read_page(b).num_slots == 0  # b still only in the pool
    assert pool.dirty_count == 1


def test_mark_dirty_unknown_page_raises(pager):
    pool = BufferPool(pager, capacity=8)
    with pytest.raises(KeyError):
        pool.mark_dirty(123)


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


def test_lru_evicts_least_recently_used(pager):
    pool = BufferPool(pager, capacity=2)
    a, _ = pool.new_page()
    b, _ = pool.new_page()
    assert a in pool and b in pool

    pool.get_page(a)          # touch a -> b is now the LRU
    c, _ = pool.new_page()    # admitting c must evict b

    assert a in pool
    assert c in pool
    assert b not in pool
    assert pool.size == 2


def test_dirty_victim_is_written_on_eviction(pager):
    pool = BufferPool(pager, capacity=1)
    a, pa = pool.new_page()
    pa.insert_record(b"evict-me")
    pool.mark_dirty(a)

    # Allocating a second page with capacity 1 forces a's eviction -> write-back.
    pool.new_page()
    assert a not in pool
    assert pager.read_page(a).get_record(0) == b"evict-me"


def test_clean_victim_is_dropped_without_error(pager):
    # Pre-populate two pages on disk.
    a = pager.allocate_page()
    b = pager.allocate_page()
    pager.write_page(a, _one(b"disk-a"))
    pager.write_page(b, _one(b"disk-b"))

    pool = BufferPool(pager, capacity=1)
    pool.get_page(a)          # clean in cache
    pool.get_page(b)          # evicts clean a (no write needed)
    assert a not in pool
    # a's on-disk copy is intact and re-readable through the pool.
    assert pool.get_page(a).get_record(0) == b"disk-a"


# ---------------------------------------------------------------------------
# Integration: pool -> flush -> disk persistence
# ---------------------------------------------------------------------------


def test_pool_writes_persist_through_pager(tmp_path):
    path = str(tmp_path / "persist.qx")
    pager1 = Pager(path)
    pool = BufferPool(pager1, capacity=4)
    n, page = pool.new_page()
    page.insert_record(b"durable")
    pool.mark_dirty(n)
    pool.flush_all()
    pager1.close()

    pager2 = Pager(path)
    try:
        assert pager2.read_page(n).get_record(0) == b"durable"
    finally:
        pager2.close()


def _one(record: bytes) -> Page:
    page = Page.empty()
    page.insert_record(record)
    return page
