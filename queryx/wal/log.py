"""The write-ahead log — append mutations to disk before applying them.

Every change that must survive a crash is first encoded as a *log record* and
appended to this log, and the log is flushed to disk, BEFORE the corresponding
data page is modified on disk. That ordering is the entire point: the log is a
durable, sequential record of intent that always reaches disk first.

This module will define:
    - the log record format (what change, to which page/row, with what new bytes),
    - append + flush (durably appending a record),
    - checkpoints: periodically recording "everything up to here is safely in the
      data files," so recovery need only replay records after the last checkpoint
      rather than the entire log.

Sequential appends are fast (no random seeks); this is why WAL is both safer and
often faster than flushing every data page synchronously.

Implemented in Phase 7. No logic yet.
"""
