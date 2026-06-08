"""WAL layer (Phase 7) — write-ahead logging and crash recovery.

This is what makes QueryX a real *database* rather than a storage engine with
indexes: durability across crashes. The rule is in the name — *write ahead*:
before a mutation is applied to a data page, a record describing it is appended
to a log on disk. If the process dies mid-write, the log is the source of truth;
on restart we replay it to bring the data files back to a consistent state.

Modules (built in Phase 7):
    log.py        The append-only log: encode each mutation as a log record,
                  append it durably before the data page is written, and write
                  periodic checkpoints to bound how much log must be replayed.
    recovery.py   On startup, scan the log from the last checkpoint and REDO
                  each logged change, so committed work survives a crash.

Scope is kept tight (redo logging + replay only — no undo, no MVCC, no
concurrency control); that boundary is stated honestly in DESIGN.md.

Implemented in Phase 7. No logic yet.
"""
