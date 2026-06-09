"""Phase 7 — write-ahead log and crash recovery tests.

Two layers: (1) the WAL itself — append/replay, torn-record detection via CRC,
checkpoint truncation; (2) the recovery guarantee — after a simulated crash, a
data page corrupted on disk is restored by replaying the log, and a clean
checkpoint correctly makes the log redundant. The headline is
test_database_recovers_after_crash: kill mid-write, reopen, data survived.
"""

import os

import pytest

from queryx.database import Database
from queryx.storage.page import PAGE_SIZE, Page
from queryx.storage.pager import Pager
from queryx.wal.log import WriteAheadLog


def _corrupt_page(path: str, page_no: int) -> None:
    """Overwrite one page with zeros, simulating a torn/lost write."""
    with open(path, "r+b") as f:
        f.seek(page_no * PAGE_SIZE)
        f.write(b"\x00" * PAGE_SIZE)


def _page_with(record: bytes) -> Page:
    p = Page.empty()
    p.insert_record(record)
    return p


# ---------------------------------------------------------------------------
# WAL unit behavior
# ---------------------------------------------------------------------------


def test_append_and_replay(tmp_path):
    path = str(tmp_path / "t.wal")
    wal = WriteAheadLog(path)
    wal.log_append(1, b"alpha-padded-to-some-length")
    wal.log_append(5, b"beta")
    wal.close()

    got = list(WriteAheadLog(path).records())
    assert got == [(1, b"alpha-padded-to-some-length"), (5, b"beta")]


def test_torn_trailing_record_is_ignored(tmp_path):
    path = str(tmp_path / "t.wal")
    wal = WriteAheadLog(path)
    wal.log_append(1, b"intact")
    wal.close()
    # Simulate a crash mid-append: a partial header with no body.
    with open(path, "ab") as f:
        f.write(b"\x00\x03\x07")  # a few stray bytes, shorter than a full header

    got = list(WriteAheadLog(path).records())
    assert got == [(1, b"intact")]  # the torn tail is discarded


def test_crc_detects_corruption(tmp_path):
    path = str(tmp_path / "t.wal")
    wal = WriteAheadLog(path)
    wal.log_append(1, b"X" * 100)
    wal.close()
    # Flip a byte inside the record's data region (past the 16-byte header).
    with open(path, "r+b") as f:
        f.seek(20)
        f.write(b"!")

    assert list(WriteAheadLog(path).records()) == []  # CRC mismatch -> record rejected


def test_checkpoint_truncates(tmp_path):
    path = str(tmp_path / "t.wal")
    wal = WriteAheadLog(path)
    wal.log_append(1, b"data")
    assert wal.size() > 0
    wal.checkpoint()
    assert wal.size() == 0
    assert list(wal.records()) == []
    wal.close()


# ---------------------------------------------------------------------------
# Pager-level redo recovery
# ---------------------------------------------------------------------------


def test_redo_recovery_restores_corrupted_page(tmp_path):
    path = str(tmp_path / "db.qx")
    pager = Pager(path)
    n = pager.allocate_page()
    pager.write_page(n, _page_with(b"survive-the-crash"))
    pager.simulate_crash()  # WAL retains the page image; no checkpoint

    _corrupt_page(path, n)  # the data page is now garbage on disk

    recovered = Pager(path)  # opening replays the WAL
    try:
        assert recovered.read_page(n).get_record(0) == b"survive-the-crash"
    finally:
        recovered.close()


def test_without_wal_corruption_is_not_recovered(tmp_path):
    # The contrast case: no WAL means no redo, so the corruption persists.
    path = str(tmp_path / "db.qx")
    pager = Pager(path, use_wal=False)
    n = pager.allocate_page()
    pager.write_page(n, _page_with(b"fragile"))
    pager.simulate_crash()

    _corrupt_page(path, n)

    reopened = Pager(path, use_wal=False)
    try:
        assert reopened.read_page(n).num_slots == 0  # data lost
    finally:
        reopened.close()


def test_clean_checkpoint_makes_log_redundant(tmp_path):
    # After a clean close (which checkpoints), the WAL is empty, so a later
    # corruption is NOT recovered — the data file is the source of truth.
    path = str(tmp_path / "db.qx")
    pager = Pager(path)
    n = pager.allocate_page()
    pager.write_page(n, _page_with(b"committed"))
    pager.close()  # checkpoint: fsync data, truncate WAL

    assert os.path.getsize(path + ".wal") == 0
    _corrupt_page(path, n)

    reopened = Pager(path)
    try:
        assert reopened.read_page(n).num_slots == 0  # nothing to replay
    finally:
        reopened.close()


# ---------------------------------------------------------------------------
# THE deliverable: kill mid-write, restart, data survived
# ---------------------------------------------------------------------------


def test_database_recovers_after_crash(tmp_path):
    directory = str(tmp_path / "crashdb")
    db = Database(directory)
    db.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    for i in range(1, 6):
        db.execute(f"INSERT INTO users VALUES ({i}, 'user{i}', {20 + i})")

    db.simulate_crash()  # abandon without checkpoint — WALs keep the page images

    # Corrupt the table's data pages on disk (the rows live just past the header).
    heap_path = os.path.join(directory, "tbl_users.qx")
    _corrupt_page(heap_path, 1)

    db2 = Database(directory)  # opening the table replays its WAL and recovers
    try:
        result = db2.execute("SELECT id, name FROM users ORDER BY id")
        assert [row[0] for row in result] == [1, 2, 3, 4, 5]
        assert result.rows[0] == (1, "user1")
        assert db2.execute("SELECT COUNT(*) FROM users").rows == [(5,)]
    finally:
        db2.close()
