"""Hash index — O(1) expected point lookup, no range scans.

A hash index maps a key to a bucket via a hash function: insert, search, and
delete are expected constant time for EQUALITY predicates. The price is that it
supports nothing else — hashing destroys order, so range queries and ordered
scans are impossible. QueryX includes it as the deliberate foil to the B+ tree;
benchmarking the two makes the optimizer's eventual choice ("= on a hash-indexed
column -> hash; range -> B+ tree or seq scan") concrete.

Design — STATIC hashing on disk:
  * A fixed directory of N bucket pages, allocated at creation at fixed page
    numbers (bucket i -> page FIRST_BUCKET + i). bucket = stable_hash(key) % N.
  * Each bucket page holds up to CAPACITY entries (key, rowid); when it fills,
    it chains to an overflow page (a singly linked list of pages per bucket).
  * A STABLE multiplicative hash (not Python's built-in hash(), which is
    randomized per process for str/bytes) so lookups survive a restart.
  * Duplicate keys are allowed (one key -> many rowids), like the B+ tree.
  * Pages ride on the buffer pool via Page.overwrite, exactly like the B+ tree.

SIMPLIFICATION (see DESIGN.md): static, not dynamic. The bucket count is fixed,
so a heavily loaded index degrades into long overflow chains and lookup drifts
from O(1) toward O(n/N). Production engines use extendible or linear hashing to
grow the directory; we do not. Delete does not merge/free emptied overflow pages.

Complexity: search/insert/delete are O(1 + chain length) page reads — O(1)
expected when load factor is kept low, O(n/N) worst case under heavy collisions.
There is intentionally NO range scan.
"""

from __future__ import annotations

import struct
from typing import Optional

from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import RowId
from queryx.storage.page import PAGE_SIZE

# Bucket page: [num_entries (H)][next_overflow page_no (I)] then entries of
# [key (q)][rowid.page_no (I)][rowid.slot (I)].
_BUCKET_HEADER = struct.Struct("<HI")  # 6 bytes
_ENTRY = struct.Struct("<qII")         # 16 bytes
CAPACITY = (PAGE_SIZE - _BUCKET_HEADER.size) // _ENTRY.size  # 255 entries/page

_FIRST_BUCKET = 2  # page 0 = pager header, page 1 = index meta
_META = struct.Struct("<4sI")  # magic, num_buckets
_META_MAGIC = b"HSH1"
_META_PAGE = 1

#: Knuth multiplicative constant, for a stable 32-bit hash of an integer key.
_HASH_MULT = 2654435761


class HashIndex:
    """A disk-backed static hash index mapping integer keys to RowIds."""

    def __init__(self, pool: BufferPool, num_buckets: int = 64) -> None:
        self._pool = pool
        pager = pool.pager
        if pager.num_pages <= 1:
            if num_buckets < 1:
                raise ValueError("num_buckets must be at least 1")
            self._num_buckets = num_buckets
            meta_no, _ = pool.new_page()  # page 1
            assert meta_no == _META_PAGE, "meta page must be page 1"
            for i in range(num_buckets):
                bucket_no, _ = pool.new_page()  # pages 2 .. 2+N-1
                assert bucket_no == _FIRST_BUCKET + i, "bucket pages must be contiguous"
                self._init_bucket_page(bucket_no)
            self._write_meta()
        else:
            self._read_meta()

    # -- meta ---------------------------------------------------------------

    def _write_meta(self) -> None:
        buf = bytearray(PAGE_SIZE)
        _META.pack_into(buf, 0, _META_MAGIC, self._num_buckets)
        page = self._pool.get_page(_META_PAGE)
        page.overwrite(bytes(buf))
        self._pool.mark_dirty(_META_PAGE)

    def _read_meta(self) -> None:
        raw = self._pool.get_page(_META_PAGE).to_bytes()
        magic, num_buckets = _META.unpack_from(raw, 0)
        if magic != _META_MAGIC:
            raise ValueError(f"not a hash index (bad meta magic {magic!r})")
        self._num_buckets = num_buckets

    # -- hashing ------------------------------------------------------------

    def _bucket(self, key: int) -> int:
        return ((key * _HASH_MULT) & 0xFFFFFFFF) % self._num_buckets

    def _bucket_head(self, key: int) -> int:
        return _FIRST_BUCKET + self._bucket(key)

    # -- bucket page (de)serialization --------------------------------------

    def _init_bucket_page(self, page_no: int) -> None:
        self._write_bucket_page(page_no, entries=[], next_overflow=0)

    def _read_bucket_page(self, page_no: int) -> tuple[list[tuple[int, RowId]], int]:
        raw = self._pool.get_page(page_no).to_bytes()
        num, next_overflow = _BUCKET_HEADER.unpack_from(raw, 0)
        entries: list[tuple[int, RowId]] = []
        pos = _BUCKET_HEADER.size
        for _ in range(num):
            key, pno, slot = _ENTRY.unpack_from(raw, pos)
            entries.append((key, RowId(pno, slot)))
            pos += _ENTRY.size
        return entries, next_overflow

    def _write_bucket_page(
        self, page_no: int, entries: list[tuple[int, RowId]], next_overflow: int
    ) -> None:
        buf = bytearray(PAGE_SIZE)
        _BUCKET_HEADER.pack_into(buf, 0, len(entries), next_overflow)
        pos = _BUCKET_HEADER.size
        for key, rid in entries:
            _ENTRY.pack_into(buf, pos, key, rid.page_no, rid.slot)
            pos += _ENTRY.size
        page = self._pool.get_page(page_no)
        page.overwrite(bytes(buf))
        self._pool.mark_dirty(page_no)

    # -- operations ---------------------------------------------------------

    def insert(self, key: int, rowid: RowId) -> None:
        """Insert (key, rowid). Duplicates allowed; chains an overflow if full."""
        page_no = self._bucket_head(key)
        while True:
            entries, next_overflow = self._read_bucket_page(page_no)
            if len(entries) < CAPACITY:
                entries.append((key, rowid))
                self._write_bucket_page(page_no, entries, next_overflow)
                return
            if next_overflow != 0:
                page_no = next_overflow
                continue
            # This page is full and has no overflow yet: allocate and link one.
            overflow_no, _ = self._pool.new_page()
            self._write_bucket_page(page_no, entries, overflow_no)  # relink
            self._write_bucket_page(overflow_no, [(key, rowid)], 0)
            return

    def search(self, key: int) -> list[RowId]:
        """Return every RowId stored under ``key`` (empty list if none)."""
        page_no = self._bucket_head(key)
        results: list[RowId] = []
        while page_no != 0:
            entries, next_overflow = self._read_bucket_page(page_no)
            for k, rid in entries:
                if k == key:
                    results.append(rid)
            page_no = next_overflow
        return results

    def delete(self, key: int, rowid: RowId) -> bool:
        """Remove the specific (key, rowid) entry. Returns False if not found."""
        page_no = self._bucket_head(key)
        while page_no != 0:
            entries, next_overflow = self._read_bucket_page(page_no)
            for i, (k, rid) in enumerate(entries):
                if k == key and rid == rowid:
                    entries.pop(i)
                    self._write_bucket_page(page_no, entries, next_overflow)
                    return True
            page_no = next_overflow
        return False

    # -- introspection / lifecycle -----------------------------------------

    @property
    def pool(self) -> BufferPool:
        """The buffer pool backing this index (read-only, for introspection)."""
        return self._pool

    @property
    def num_buckets(self) -> int:
        return self._num_buckets

    def flush(self) -> None:
        self._pool.flush_all()
