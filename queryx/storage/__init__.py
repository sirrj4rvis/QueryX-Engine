"""Storage layer (Phase 2) — the foundation that turns bytes into durable rows.

This is the lowest layer in QueryX. It knows nothing about SQL, indexes, or
query plans; it only knows that disk is read and written in fixed-size blocks,
and that rows must be packed into and recovered from those blocks.

Modules (built in Phase 2):
    page.py         A fixed-size 4KB page and its slotted-page record layout
                    (a slot directory growing from one end, row bytes from the
                    other), plus row serialization to/from bytes.
    pager.py        Reads and writes pages by number to a single file; the only
                    component that performs raw disk I/O. Tracks free pages.
    buffer_pool.py  An in-memory cache of pages with LRU eviction, so hot pages
                    are not re-read from disk on every access. Mediates all page
                    access between upper layers and the pager.
    heap_file.py    An unordered collection of rows spread across many pages —
                    the default table storage. Supports insert and full scan.

This layer must not import from any other QueryX layer.
"""
