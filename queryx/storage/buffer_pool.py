"""The buffer pool — an in-memory cache of pages with LRU eviction.

Disk is orders of magnitude slower than RAM, so re-reading a page on every
access (and flushing on every write, as the bare pager does) would be ruinous.
The buffer pool sits between the upper layers and the pager: it keeps a bounded
set of pages in memory, serves repeat accesses from RAM, and defers writes.

Three mechanisms:

  * Cache with identity. get_page(n) returns an in-memory Page; asking again
    returns the SAME object (a cache hit, no disk I/O). Callers mutate that
    object in place.
  * Dirty tracking + lazy write-back. Mutating a cached page does not touch
    disk; the caller marks it dirty, and it is written back only when evicted
    or explicitly flushed. A page touched 100 times costs one write, not 100.
  * LRU eviction. When the pool is full and a new page is needed, the
    least-recently-used page is evicted (written first if dirty, else dropped,
    since its disk copy is already current). LRU is implemented with an
    OrderedDict: each access moves the page to the most-recent end, so the
    eviction victim is always at the front.

Simplification — NO PINNING. A production buffer pool pins a page that is in
use so it cannot be evicted while a caller still holds it; doing otherwise risks
a "lost update" (caller mutates an object that is no longer the cached copy).
QueryX is single-threaded and finishes with one page before fetching the next,
so it omits pinning. The contract: do not retain a Page reference across a
get_page/new_page call that could trigger eviction — re-fetch instead. This is a
deliberate, documented limit (see DESIGN.md), not an oversight.

Complexity: get_page is O(1) on a hit (dict lookup + move-to-end) and O(1) plus
one disk read on a miss; eviction is O(1). The win is measured in disk seeks
avoided, not CPU.
"""

from __future__ import annotations

from collections import OrderedDict

from .page import Page
from .pager import Pager


class BufferPool:
    """A fixed-capacity, write-back LRU cache of pages over a Pager."""

    def __init__(self, pager: Pager, capacity: int = 64) -> None:
        if capacity < 1:
            raise ValueError("buffer pool capacity must be at least 1")
        self._pager = pager
        self._capacity = capacity
        #: page_no -> Page, ordered from least- to most-recently used.
        self._cache: "OrderedDict[int, Page]" = OrderedDict()
        #: page numbers whose cached copy differs from disk.
        self._dirty: set[int] = set()
        # simple counters for benchmarking / introspection (Phase 8).
        self.hits = 0
        self.misses = 0

    # -- access -------------------------------------------------------------

    def get_page(self, page_no: int) -> Page:
        """Return page ``page_no``, from cache if present, else read from disk."""
        page = self._cache.get(page_no)
        if page is not None:
            self.hits += 1
            self._cache.move_to_end(page_no)  # mark most-recently used
            return page
        self.misses += 1
        page = self._pager.read_page(page_no)
        self._admit(page_no, page)
        return page

    def new_page(self) -> tuple[int, Page]:
        """Allocate a fresh empty page, cache it, and return (page_no, page)."""
        page_no = self._pager.allocate_page()
        page = Page.empty()
        self._admit(page_no, page)
        return page_no, page

    def mark_dirty(self, page_no: int) -> None:
        """Record that the cached copy of ``page_no`` has unsaved changes."""
        if page_no not in self._cache:
            raise KeyError(f"page {page_no} is not in the buffer pool")
        self._dirty.add(page_no)

    # -- write-back ---------------------------------------------------------

    def flush_page(self, page_no: int) -> None:
        """Write a single dirty page to the pager (no-op if clean/absent)."""
        if page_no in self._dirty and page_no in self._cache:
            self._pager.write_page(page_no, self._cache[page_no])
            self._dirty.discard(page_no)

    def flush_all(self) -> None:
        """Write every dirty page back to the pager."""
        for page_no in list(self._dirty):
            self._pager.write_page(page_no, self._cache[page_no])
        self._dirty.clear()

    # -- internals ----------------------------------------------------------

    def _admit(self, page_no: int, page: Page) -> None:
        """Insert a freshly fetched page, evicting the LRU victim if full."""
        if len(self._cache) >= self._capacity:
            self._evict_one()
        self._cache[page_no] = page  # inserted at the most-recent end

    def _evict_one(self) -> None:
        victim_no, victim_page = self._cache.popitem(last=False)  # LRU = front
        if victim_no in self._dirty:
            self._pager.write_page(victim_no, victim_page)
            self._dirty.discard(victim_no)

    # -- introspection ------------------------------------------------------

    @property
    def pager(self) -> Pager:
        """The underlying pager (read-only access for scanning page ranges)."""
        return self._pager

    def __contains__(self, page_no: int) -> bool:
        return page_no in self._cache

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def dirty_count(self) -> int:
        return len(self._dirty)
