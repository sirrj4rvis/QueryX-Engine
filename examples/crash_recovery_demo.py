"""QueryX crash-recovery demo  --  Write-Ahead Logging + redo recovery.

A single-command, narrated demonstration that QueryX survives a crash that
corrupts a data page on disk. It uses ONLY the public engine API plus the
existing Database.simulate_crash() helper -- it changes no engine internals.

    python examples/crash_recovery_demo.py

What it proves (the same guarantee as tests/wal/test_wal.py):
  1. Inserted rows are written to the heap AND logged (full-page images) to a
     write-ahead log BEFORE the data page is considered safe.
  2. After a crash with no clean checkpoint, the WAL still holds those images.
  3. Even if the on-disk data page is destroyed (zeroed), reopening the database
     REPLAYS the WAL and overwrites the corrupted page with the last good,
     CRC-verified image -- so committed data survives.

Reproducibility: runs in a fresh temp directory each time and prints only
deterministic values (row data, WAL sizes for fixed input, byte-presence
checks) -- no timings or paths -- so repeated runs produce identical output.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from queryx.database import Database
from queryx.storage.page import PAGE_SIZE

# A small, fixed dataset -> deterministic output on every run.
ROWS = [
    (1, "alice", 100),
    (2, "bob", 250),
    (3, "carol", 75),
    (4, "dave", 500),
    (5, "erin", 320),
]


def _line(text: str = "") -> None:
    print(text)


def _rule() -> None:
    _line("=" * 64)


def _read_page(path: str, page_no: int) -> bytes:
    """Read one raw 4KB page straight from the data file (no engine involved)."""
    with open(path, "rb") as f:
        f.seek(page_no * PAGE_SIZE)
        return f.read(PAGE_SIZE)


def _zero_page(path: str, page_no: int) -> None:
    """Overwrite one page with zeros -- simulates a torn / lost disk write."""
    with open(path, "r+b") as f:
        f.seek(page_no * PAGE_SIZE)
        f.write(b"\x00" * PAGE_SIZE)


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="queryx_crashdemo_")
    heap_path = os.path.join(workdir, "tbl_accounts.qx")
    values = ", ".join(f"({i}, '{name}', {bal})" for i, name, bal in ROWS)

    try:
        _rule()
        _line(" QueryX  --  Crash Recovery Demo (Write-Ahead Logging + redo)")
        _rule()
        _line()

        # ---- [1/6] write data ------------------------------------------------
        _line("[1/6] Creating a fresh database and inserting account rows...")
        db = Database(workdir)
        db.execute("CREATE TABLE accounts (id INT, name TEXT, balance INT)")
        wal_after_create = db.runtime_stats()["wal_bytes"]
        affected = db.execute(f"INSERT INTO accounts VALUES {values}")
        wal_after_insert = db.runtime_stats()["wal_bytes"]
        rows = db.execute("SELECT id, name, balance FROM accounts ORDER BY id").rows
        _line(f"      inserted {affected} rows into 'accounts':")
        for r in rows:
            _line(f"        {r}")
        _line()

        # ---- [2/6] the WAL recorded every page change ------------------------
        _line("[2/6] The write-ahead log recorded every page change:")
        _line(f"      WAL size after CREATE TABLE : {wal_after_create} bytes")
        _line(f"      WAL size after INSERT       : {wal_after_insert} bytes")
        _line("      (each record is a full 4KB page image + CRC, not yet checkpointed)")
        _line()

        # ---- [3/6] crash -----------------------------------------------------
        _line("[3/6] *** SIMULATING A CRASH ***  (process killed; no clean shutdown)")
        db.simulate_crash()  # drop handles WITHOUT checkpointing -> WAL is retained
        _line("      file handles dropped with no checkpoint; the WAL kept its images")
        _line()

        # ---- [4/6] destroy the data page on disk -----------------------------
        _line("[4/6] Corrupting the data page on disk (simulating a torn write)...")
        before = _read_page(heap_path, 1)
        _line(f"        page 1 currently contains 'alice'? {b'alice' in before}")
        _zero_page(heap_path, 1)
        after = _read_page(heap_path, 1)
        _line("        -> zeroed page 1 of tbl_accounts.qx")
        _line(f"        page 1 contains 'alice' now?       {b'alice' in after}   (data destroyed on disk)")
        _line()

        # ---- [5/6] reopen -> the pager replays the WAL -----------------------
        _line("[5/6] Restarting QueryX on the same files...")
        _line("      on reopen, the pager REPLAYS the WAL, rewriting the corrupted")
        _line("      page from the last good CRC-verified image.")
        db2 = Database(workdir)
        recovered = db2.execute("SELECT id, name, balance FROM accounts ORDER BY id").rows
        db2.close()
        _line()

        # ---- [6/6] verify ----------------------------------------------------
        _line("[6/6] Verifying the data survived:")
        for r in recovered:
            _line(f"        {r}")
        restored = _read_page(heap_path, 1)
        _line(f"      page 1 contains 'alice' again?      {b'alice' in restored}   (recovered from the WAL)")
        _line()

        ok = recovered == ROWS
        _rule()
        if ok:
            _line(" RESULT: all rows recovered after the crash.  DURABILITY HOLDS.  [PASS]")
        else:
            _line(" RESULT: data was NOT fully recovered.  [FAIL]")
        _rule()
        return 0 if ok else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
