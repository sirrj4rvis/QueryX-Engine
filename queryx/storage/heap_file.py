"""Heap files — unordered collections of rows spread across pages.

A heap file is the physical storage for a table: rows are stored in no
particular order, packed into data pages via the buffer pool, with new rows
going onto a page that has room (or a freshly allocated page). "Heap" is exactly
PostgreSQL's term for a table's main fork. It exposes the minimal operations a
table must support:

    insert(record)  -> RowId(page_no, slot)   the stable physical address
    scan()          -> yields (RowId, record) for every live row (a seq scan)
    get(row_id)     -> record bytes or None
    delete(row_id)  -> bool

Ordering, point lookups, and range queries are NOT the heap file's job — those
come from the index layer built on top of it. The heap file stores OPAQUE bytes;
it does not know the row's schema (serialization lives in page.py and is the
caller's concern), which keeps this layer free of any dependency on SQL or the
catalog.

This HeapFile assumes every data page in its buffer pool's file belongs to it
(one heap file per database file for now). Mapping multiple tables into one file
via a catalog/page-directory is later work.

Simplifications (see DESIGN.md failure analysis):
  * Append-mostly placement. insert() tries the last page and, if full,
    allocates a new one; it does NOT search earlier pages for free space left by
    deletes. A production engine keeps a Free Space Map (FSM) to reuse those
    holes. Here, space freed by a delete is reclaimed only by a later insert
    that happens to land on the same page.
  * No update(). An in-place update that grows a row will not fit its slot, and
    delete+reinsert would change the RowId and break index entries. Update is
    handled deliberately in the execution phase, not here.

Complexity: insert is O(1) amortized (one page access, occasional allocation);
get/delete are O(1) given a RowId; scan is O(pages) and is the operation the
optimizer will later try to AVOID with an index. The dominant cost throughout is
page I/O, which the buffer pool absorbs for hot pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .buffer_pool import BufferPool
from .page import HEADER_SIZE, PAGE_SIZE, SLOT_SIZE, PageFullError

#: The largest record that can ever fit in an (empty) page: page minus the page
#: header minus one slot directory entry.
MAX_RECORD_SIZE = PAGE_SIZE - HEADER_SIZE - SLOT_SIZE


@dataclass(frozen=True)
class RowId:
    """A row's stable physical address: which page, which slot on that page.

    Frozen (immutable and hashable) so it can be stored as a value in an index,
    which is exactly what the index layer will do in Phase 3.
    """

    page_no: int
    slot: int


class HeapFile:
    """An unordered table stored across the data pages of a buffer pool."""

    def __init__(self, pool: BufferPool) -> None:
        self._pool = pool
        # The last data page; new inserts try here first. Derived from the file
        # so it is correct after a restart. num_pages includes the page-0 header,
        # so data pages are 1 .. num_pages-1.
        num_pages = pool.pager.num_pages
        self._last_page: int | None = (num_pages - 1) if num_pages > 1 else None

    @property
    def pool(self) -> BufferPool:
        """The buffer pool backing this heap (read-only, for introspection)."""
        return self._pool

    # -- writes -------------------------------------------------------------

    def insert(self, record: bytes) -> RowId:
        """Store ``record`` and return its RowId. Allocates a page if needed."""
        if len(record) == 0:
            raise ValueError("cannot insert an empty record")
        if len(record) > MAX_RECORD_SIZE:
            raise ValueError(
                f"record of {len(record)} bytes exceeds max {MAX_RECORD_SIZE} (one page)"
            )

        # Try the current last page first (append-mostly).
        if self._last_page is not None:
            page = self._pool.get_page(self._last_page)
            try:
                slot = page.insert_record(record)
            except PageFullError:
                pass
            else:
                self._pool.mark_dirty(self._last_page)
                return RowId(self._last_page, slot)

        # Last page full (or none yet): allocate a fresh page.
        page_no, page = self._pool.new_page()
        slot = page.insert_record(record)  # fits: we bounded len(record) above
        self._pool.mark_dirty(page_no)
        self._last_page = page_no
        return RowId(page_no, slot)

    def delete(self, row_id: RowId) -> bool:
        """Delete the row at ``row_id``. Returns False if already absent."""
        page = self._pool.get_page(row_id.page_no)
        deleted = page.delete_record(row_id.slot)
        if deleted:
            self._pool.mark_dirty(row_id.page_no)
        return deleted

    # -- reads --------------------------------------------------------------

    def get(self, row_id: RowId) -> bytes | None:
        """Return the record at ``row_id``, or None if its slot is dead."""
        page = self._pool.get_page(row_id.page_no)
        return page.get_record(row_id.slot)

    def scan(self) -> Iterator[tuple[RowId, bytes]]:
        """Yield (RowId, record) for every live row, page by page (seq scan)."""
        num_pages = self._pool.pager.num_pages
        for page_no in range(1, num_pages):
            page = self._pool.get_page(page_no)
            for slot, record in page.records():
                yield RowId(page_no, slot), record

    # -- durability ---------------------------------------------------------

    def flush(self) -> None:
        """Write all buffered changes back through the pager."""
        self._pool.flush_all()
