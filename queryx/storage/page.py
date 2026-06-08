"""Fixed-size pages, the slotted-page record layout, and row serialization.

A *page* is the unit of disk I/O: QueryX always reads and writes whole 4KB
blocks, never individual rows, because that is how disks and operating systems
actually move data (PostgreSQL uses 8KB pages; SQLite defaults to 4KB). Reading
50 bytes and reading 4096 bytes cost essentially the same — one seek — so we
read the whole block and pick the row out of it in memory.

Rows are variable length and must be deletable and movable without invalidating
the index entries that point at them. The standard solution, used by every real
database, is the SLOTTED PAGE:

    offset 0
    ┌──────────────────────────────────────────────────────────────────┐
    │ header │ slot0 slot1 slot2 → →  ...free gap...  ← ← rec2 rec1 rec0 │
    └──────────────────────────────────────────────────────────────────┘
      4 bytes  slot directory grows UP        record bytes grow DOWN from
               from just after the header     the end of the page

  * A slot directory grows inward from the front; each slot is (offset, length).
  * Record bytes grow inward from the back of the page.
  * Free space is the gap between the two; we can insert while the gap holds the
    record (plus a new slot, if we are not reusing a dead one).

The crucial property: a row's identity is its SLOT NUMBER, not its byte
position. An index points at "page p, slot i". If the page is later compacted
and the record's bytes move, only the slot's stored offset changes — the slot
number, and therefore every index entry, stays valid. That indirection is the
entire reason slotted pages exist.

This module also owns row (de)serialization: turning a tuple of typed Python
values into a compact byte string and back, given a schema. The page itself
stores opaque record bytes and does not care what they mean; serialization is
kept here, alongside the page, because both are "how a row becomes bytes".

Time complexity (per page, in-memory): insert / get / delete are O(1) given a
slot index, except that insert scans existing slots for a reusable dead one,
which is O(number of slots on the page). A page holds at most a few hundred
rows, so this is a tiny constant in practice — the real cost of touching a page
is the disk I/O to fetch it, handled a layer up by the pager/buffer pool.
"""

from __future__ import annotations

import struct
from enum import Enum
from typing import Iterator, Sequence

# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

#: The fixed size of every page, in bytes. Matches SQLite's default; chosen so a
#: page maps cleanly onto OS/disk block sizes. Must fit page offsets in a uint16
#: (4096 <= 65535), which the slotted-page layout below relies on.
PAGE_SIZE = 4096

#: Page header: two little-endian unsigned 16-bit ints —
#:   num_slots: how many slot entries exist (including dead ones), and
#:   free_end:  the offset where the record region begins; records occupy
#:              [free_end, PAGE_SIZE). Starts at PAGE_SIZE (no records yet).
_HEADER_FORMAT = "<HH"
HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)  # 4 bytes

#: Each slot directory entry: (offset, length), both little-endian uint16.
#: A dead (deleted/free) slot is recorded as (0, 0); offset 0 can never be a
#: real record, since records always live at an offset >= HEADER_SIZE.
_SLOT_FORMAT = "<HH"
SLOT_SIZE = struct.calcsize(_SLOT_FORMAT)  # 4 bytes


class PageFullError(Exception):
    """Raised when a record will not fit in a page's remaining free space."""


class Page:
    """A single 4KB slotted page, backed by a mutable ``bytearray``.

    The bytearray IS the page as it will be written to disk; ``to_bytes()`` is a
    straight copy and ``from_bytes()`` wraps an existing block. All structural
    state (slot count, free-space boundary, slots) is read from and written to
    the header/slot bytes on demand, so the in-memory object and its on-disk
    representation can never drift apart.
    """

    __slots__ = ("_data",)

    def __init__(self, data: bytearray) -> None:
        if len(data) != PAGE_SIZE:
            raise ValueError(f"page must be exactly {PAGE_SIZE} bytes, got {len(data)}")
        self._data = data

    # -- construction -------------------------------------------------------

    @classmethod
    def empty(cls) -> "Page":
        """Return a fresh, empty page: zero slots, all of it free space."""
        data = bytearray(PAGE_SIZE)
        struct.pack_into(_HEADER_FORMAT, data, 0, 0, PAGE_SIZE)
        return cls(data)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Page":
        """Wrap a 4KB block read from disk as a Page (copies the bytes)."""
        return cls(bytearray(raw))

    def to_bytes(self) -> bytes:
        """Return the page's exact on-disk byte representation."""
        return bytes(self._data)

    # -- header accessors ---------------------------------------------------

    @property
    def num_slots(self) -> int:
        """Number of slot entries, including dead (deleted) ones."""
        return struct.unpack_from("<H", self._data, 0)[0]

    @property
    def _free_end(self) -> int:
        return struct.unpack_from("<H", self._data, 2)[0]

    def _set_num_slots(self, n: int) -> None:
        struct.pack_into("<H", self._data, 0, n)

    def _set_free_end(self, offset: int) -> None:
        struct.pack_into("<H", self._data, 2, offset)

    def free_space(self) -> int:
        """Bytes available between the end of the slot array and the records.

        This is the largest record that can be inserted *if* a dead slot is
        reused; inserting into a new slot needs ``free_space() - SLOT_SIZE``.
        """
        slot_array_end = HEADER_SIZE + self.num_slots * SLOT_SIZE
        return self._free_end - slot_array_end

    # -- slot accessors -----------------------------------------------------

    def _slot(self, index: int) -> tuple[int, int]:
        return struct.unpack_from(_SLOT_FORMAT, self._data, HEADER_SIZE + index * SLOT_SIZE)

    def _set_slot(self, index: int, offset: int, length: int) -> None:
        struct.pack_into(_SLOT_FORMAT, self._data, HEADER_SIZE + index * SLOT_SIZE, offset, length)

    def _find_dead_slot(self) -> int | None:
        for i in range(self.num_slots):
            offset, length = self._slot(i)
            if offset == 0 and length == 0:
                return i
        return None

    # -- record operations --------------------------------------------------

    def insert_record(self, record: bytes) -> int:
        """Store ``record`` and return its slot index (its stable row identity).

        Reuses a dead slot if one exists (no new slot needed); otherwise appends
        a new slot. Record bytes are always placed at the current free boundary,
        growing the record region downward. Raises ``PageFullError`` if there is
        not enough free space.
        """
        need = len(record)
        if need == 0:
            raise ValueError("cannot store an empty record")

        reuse = self._find_dead_slot()
        slot_cost = 0 if reuse is not None else SLOT_SIZE
        if self.free_space() < need + slot_cost:
            raise PageFullError(
                f"record needs {need + slot_cost} bytes, only {self.free_space()} free"
            )

        new_offset = self._free_end - need
        self._data[new_offset : new_offset + need] = record
        self._set_free_end(new_offset)

        if reuse is not None:
            self._set_slot(reuse, new_offset, need)
            return reuse
        index = self.num_slots
        self._set_slot(index, new_offset, need)
        self._set_num_slots(index + 1)
        return index

    def get_record(self, index: int) -> bytes | None:
        """Return the record at ``index``, or ``None`` if that slot is dead."""
        if not 0 <= index < self.num_slots:
            raise IndexError(f"slot {index} out of range (0..{self.num_slots - 1})")
        offset, length = self._slot(index)
        if offset == 0 and length == 0:
            return None
        return bytes(self._data[offset : offset + length])

    def delete_record(self, index: int) -> bool:
        """Mark the slot at ``index`` dead. Returns False if already dead.

        The record's bytes are NOT reclaimed here — they become a dead hole in
        the record region until the page is compacted. This is a deliberate
        Phase 2 simplification (no compaction yet); see the failure analysis in
        DESIGN.md. The slot itself can be reused by a later insert.
        """
        if not 0 <= index < self.num_slots:
            raise IndexError(f"slot {index} out of range (0..{self.num_slots - 1})")
        offset, length = self._slot(index)
        if offset == 0 and length == 0:
            return False
        self._set_slot(index, 0, 0)
        return True

    def records(self) -> Iterator[tuple[int, bytes]]:
        """Yield ``(slot_index, record_bytes)`` for every live record."""
        for i in range(self.num_slots):
            offset, length = self._slot(i)
            if offset == 0 and length == 0:
                continue
            yield i, bytes(self._data[offset : offset + length])


# ---------------------------------------------------------------------------
# Row serialization
# ---------------------------------------------------------------------------
#
# A row is a tuple of typed values; the page stores opaque bytes. These two
# functions are the bridge. The catalog (Phase 4+) will own the real schema; for
# now a schema is just an ordered list of column types.


class ColumnType(Enum):
    """The column types QueryX supports at the storage layer.

    INT  -> an 8-byte signed integer (little-endian). 8 bytes (not 4) so we
            never silently overflow on a large id.
    TEXT -> a uint16 length prefix followed by that many UTF-8 bytes. The length
            prefix caps a single string at 65535 bytes, but a row must fit in a
            page anyway, so the real limit is ~4KB.
    """

    INT = "INT"
    TEXT = "TEXT"


def serialize_row(schema: Sequence[ColumnType], values: Sequence[object]) -> bytes:
    """Pack ``values`` into bytes according to ``schema`` (column-by-column)."""
    if len(schema) != len(values):
        raise ValueError(f"schema has {len(schema)} columns but got {len(values)} values")

    parts: list[bytes] = []
    for col_type, value in zip(schema, values):
        if col_type is ColumnType.INT:
            parts.append(struct.pack("<q", int(value)))
        elif col_type is ColumnType.TEXT:
            encoded = str(value).encode("utf-8")
            if len(encoded) > 0xFFFF:
                raise ValueError("TEXT value exceeds 65535 bytes")
            parts.append(struct.pack("<H", len(encoded)) + encoded)
        else:  # pragma: no cover - defensive; ColumnType is closed
            raise ValueError(f"unknown column type: {col_type!r}")
    return b"".join(parts)


def deserialize_row(schema: Sequence[ColumnType], data: bytes) -> tuple[object, ...]:
    """Unpack ``data`` back into a tuple of values, guided by ``schema``."""
    values: list[object] = []
    pos = 0
    for col_type in schema:
        if col_type is ColumnType.INT:
            (value,) = struct.unpack_from("<q", data, pos)
            pos += 8
            values.append(value)
        elif col_type is ColumnType.TEXT:
            (length,) = struct.unpack_from("<H", data, pos)
            pos += 2
            values.append(data[pos : pos + length].decode("utf-8"))
            pos += length
        else:  # pragma: no cover - defensive; ColumnType is closed
            raise ValueError(f"unknown column type: {col_type!r}")
    return tuple(values)
