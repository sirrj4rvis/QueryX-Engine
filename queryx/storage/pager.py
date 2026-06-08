"""The pager — the only component that performs raw page-level disk I/O.

The pager treats a database file as a flat array of fixed-size pages addressed
by page number: reading page n seeks to offset ``n * PAGE_SIZE`` and reads one
page; writing it does the reverse. Everything above this layer thinks purely in
page numbers; the pager is the single place that knows a page number is really a
byte offset, which means it is the single place that must get durability right
(and, in Phase 7, the single place the WAL hooks into).

Page 0 is reserved as the FILE HEADER (a "metadata page"): it stores a magic
number, a format version, and the free list. Real databases do exactly this —
the database describes itself using a special page (SQLite's page 1). Because
page 0 is the header, DATA pages start at page 1; ``read_page(0)`` is forbidden.

Free-page tracking: when a page is freed it is recorded in the header's free
list and reused by the next allocation, so emptied pages are not leaked across
restarts. The free list is stored as a flat array inside page 0, which caps it
at a fixed number of entries (see ``_MAX_FREE``). A production engine instead
threads a LINKED free list through the freed pages themselves (each free page
points to the next), which is unbounded; we accept the cap for simplicity and
flag it in DESIGN.md.

Durability: ``write_page`` flushes to the OS, so data survives a *process* kill
(the Phase 2 demo). Surviving *power loss* requires fsync, which ``close()``/
``sync()`` do and which the WAL (Phase 7) makes the general rule. A torn write
(a 4KB write interrupted mid-way by power loss) can still corrupt a page here —
again, the WAL's job to fix later.

Complexity: read/write/allocate are O(1) plus one disk seek — the seek is the
real cost. Allocation reuses a free page in O(1) or extends the file in O(1).
"""

from __future__ import annotations

import os
import struct

from .page import PAGE_SIZE, Page


class Pager:
    """Reads and writes 4KB pages to a single database file by page number."""

    _MAGIC = b"QRYX"
    _VERSION = 1
    #: header page layout: magic (4 bytes) + version (uint16) + free_count (uint32)
    _HEADER_FMT = "<4sHI"
    _HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 10 bytes
    #: how many free-page entries (uint32 each) fit in the remainder of page 0
    _MAX_FREE = (PAGE_SIZE - _HEADER_SIZE) // 4  # 1021

    def __init__(self, path: str) -> None:
        self.path = path
        existed = os.path.exists(path) and os.path.getsize(path) > 0
        if not os.path.exists(path):
            # create the file without truncating an existing one
            open(path, "xb").close()
        # r+b: read and write an existing file without truncating it
        self._file = open(path, "r+b")
        self._closed = False
        self._free_list: list[int] = []
        if existed:
            self._load_header()
        else:
            self._write_header()  # initializes page 0 -> num_pages becomes 1

    # -- header / page 0 ----------------------------------------------------

    def _write_header(self) -> None:
        """Serialize magic, version, and the free list into page 0 and flush."""
        data = bytearray(PAGE_SIZE)
        struct.pack_into(self._HEADER_FMT, data, 0, self._MAGIC, self._VERSION, len(self._free_list))
        pos = self._HEADER_SIZE
        for page_no in self._free_list:
            struct.pack_into("<I", data, pos, page_no)
            pos += 4
        self._file.seek(0)
        self._file.write(data)
        self._file.flush()

    def _load_header(self) -> None:
        """Validate the file header and load the free list into memory."""
        self._file.seek(0)
        data = self._file.read(PAGE_SIZE)
        if len(data) < self._HEADER_SIZE:
            raise ValueError("corrupt database file: header is too short")
        magic, version, free_count = struct.unpack_from(self._HEADER_FMT, data, 0)
        if magic != self._MAGIC:
            raise ValueError(f"not a QueryX database file (bad magic {magic!r})")
        if version != self._VERSION:
            raise ValueError(f"unsupported QueryX file version {version}")
        pos = self._HEADER_SIZE
        for _ in range(free_count):
            (page_no,) = struct.unpack_from("<I", data, pos)
            pos += 4
            self._free_list.append(page_no)

    # -- geometry / validation ---------------------------------------------

    @property
    def num_pages(self) -> int:
        """Total pages in the file, including the page-0 header."""
        return os.fstat(self._file.fileno()).st_size // PAGE_SIZE

    def _validate_data_page(self, page_no: int) -> None:
        if page_no < 1:
            raise ValueError("page 0 is reserved for the file header")
        if page_no >= self.num_pages:
            raise IndexError(f"page {page_no} does not exist (file has {self.num_pages} pages)")

    # -- raw page I/O -------------------------------------------------------

    def read_page(self, page_no: int) -> Page:
        """Read a data page (page_no >= 1) from disk and return it as a Page."""
        self._validate_data_page(page_no)
        self._file.seek(page_no * PAGE_SIZE)
        data = self._file.read(PAGE_SIZE)
        if len(data) != PAGE_SIZE:
            raise IOError(f"short read for page {page_no}: got {len(data)} bytes")
        return Page.from_bytes(data)

    def write_page(self, page_no: int, page: Page) -> None:
        """Write a data page (page_no >= 1) to disk and flush to the OS."""
        self._validate_data_page(page_no)
        self._file.seek(page_no * PAGE_SIZE)
        self._file.write(page.to_bytes())
        self._file.flush()

    def _write_empty_page(self, page_no: int) -> None:
        self._file.seek(page_no * PAGE_SIZE)
        self._file.write(Page.empty().to_bytes())
        self._file.flush()

    # -- allocation / freeing ----------------------------------------------

    def allocate_page(self) -> int:
        """Return a fresh, empty data page number, reusing a freed page if any.

        Reuse pops the free list (and re-zeros the page so stale bytes can't be
        mistaken for live records); otherwise the file is extended by one page.
        """
        if self._free_list:
            page_no = self._free_list.pop()
            self._write_empty_page(page_no)
            self._write_header()  # persist the shrunken free list
            return page_no
        page_no = self.num_pages  # next slot past the current end of file
        self._write_empty_page(page_no)  # extends the file
        return page_no

    def free_page(self, page_no: int) -> None:
        """Record a data page as free so it can be reused by a later allocation."""
        self._validate_data_page(page_no)
        if page_no in self._free_list:
            return  # idempotent: ignore a double free
        if len(self._free_list) >= self._MAX_FREE:
            raise RuntimeError(
                f"free list is full ({self._MAX_FREE} entries); page-0 array cannot grow"
            )
        self._free_list.append(page_no)
        self._write_header()

    @property
    def free_page_count(self) -> int:
        return len(self._free_list)

    # -- durability / lifecycle --------------------------------------------

    def sync(self) -> None:
        """Force buffered data all the way to the physical disk (fsync)."""
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        """Persist the header, fsync, and close the file."""
        if self._closed:
            return
        self._write_header()
        self.sync()
        self._file.close()
        self._closed = True

    def __enter__(self) -> "Pager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
