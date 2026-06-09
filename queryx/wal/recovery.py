"""Crash recovery — replay the log on startup to restore consistency.

When a pager opens a data file, it cannot assume the previous run shut down
cleanly. Recovery reads the write-ahead log and REDOes each logged page image
against the data file, so any change that was logged but possibly not (fully)
written to its data page is reapplied. Because each record is a full page image,
replay is idempotent: applying it when the data page was already correct is
harmless, and applying it when the page was torn or stale repairs it.

QueryX implements REDO-ONLY recovery (reapply logged changes). It does NOT undo
uncommitted changes, because it has no multi-statement transactions — a scope
boundary stated honestly. Production systems use ARIES (analysis, redo, undo)
for the full story, including rolling back partial transactions.
"""

from __future__ import annotations

from typing import Callable

from queryx.wal.log import WriteAheadLog


def replay(wal: WriteAheadLog, apply_page: Callable[[int, bytes], None]) -> int:
    """Apply every intact log record via ``apply_page``; return how many.

    ``apply_page(page_no, data)`` writes the page image to the data file. The
    caller is responsible for fsyncing the data file and checkpointing the log
    once replay completes.
    """
    count = 0
    for page_no, data in wal.records():
        apply_page(page_no, data)
        count += 1
    return count
