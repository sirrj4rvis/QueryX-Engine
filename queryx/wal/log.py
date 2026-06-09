"""The write-ahead log — append page images to disk before applying them.

Every page about to be written to a data file is first encoded as a log record
and appended here. The ordering is the whole point: the log record reaches
durable storage BEFORE the data page is modified, so a crash mid-write can be
repaired by replaying the log.

Record format (all little-endian), one per logged page:

    [ MAGIC (4s) ][ page_no (I) ][ length (I) ][ crc32 (I) ][ data (length bytes) ]

The CRC lets recovery detect a torn trailing record (a crash during the append):
replay stops at the first record whose header is short, whose magic/length is
wrong, or whose data fails the CRC — discarding the incomplete tail. That gives
the all-or-nothing guarantee for the last in-flight write.

This module is intentionally page-size-agnostic and depends on NOTHING in the
storage layer (it stores each record's length), so the pager can import it
without creating an import cycle.

Durability policy: log_append flushes to the OS (surviving a process crash);
fsync happens at checkpoint (truncate) and on the pager's sync()/close(). A
production WAL fsyncs at each commit, batching many via group commit, for
power-loss durability — we flush-per-record and fsync-at-checkpoint for speed,
a documented simplification.
"""

from __future__ import annotations

import os
import struct
import zlib
from typing import Iterator

_MAGIC = b"WLOG"
_HEADER = struct.Struct("<4sIII")  # magic, page_no, length, crc32


class WriteAheadLog:
    """An append-only log of (page_no, page-image) records."""

    def __init__(self, path: str) -> None:
        self.path = path
        if not os.path.exists(path):
            open(path, "xb").close()
        self._file = open(path, "r+b")

    def log_append(self, page_no: int, data: bytes) -> None:
        """Append a page image and flush it to the OS (write-ahead)."""
        crc = zlib.crc32(data) & 0xFFFFFFFF
        self._file.seek(0, os.SEEK_END)
        self._file.write(_HEADER.pack(_MAGIC, page_no, len(data), crc))
        self._file.write(data)
        self._file.flush()  # reach the OS now; fsync deferred to checkpoint

    def records(self) -> Iterator[tuple[int, bytes]]:
        """Yield (page_no, data) for each intact record, stopping at a torn one."""
        self._file.seek(0)
        while True:
            header = self._file.read(_HEADER.size)
            if len(header) < _HEADER.size:
                return  # clean end, or a torn header
            magic, page_no, length, crc = _HEADER.unpack(header)
            if magic != _MAGIC or length <= 0 or length > (1 << 24):
                return  # corrupt header
            data = self._file.read(length)
            if len(data) < length:
                return  # torn data (crash mid-append)
            if (zlib.crc32(data) & 0xFFFFFFFF) != crc:
                return  # corrupt data
            yield page_no, data

    def checkpoint(self) -> None:
        """Discard the log after the data it describes is known durable.

        Truncating is itself made durable (fsync) so a crash can't resurrect
        already-applied records.
        """
        self._file.seek(0)
        self._file.truncate(0)
        self._file.flush()
        os.fsync(self._file.fileno())

    def size(self) -> int:
        return os.fstat(self._file.fileno()).st_size

    def close(self) -> None:
        self._file.close()
