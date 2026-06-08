"""Crash recovery — replay the log on startup to restore consistency.

When QueryX starts, it cannot assume the previous run shut down cleanly. The
recovery process reads the write-ahead log from the most recent checkpoint
forward and REDOes each logged mutation against the data pages, so any change
that was logged but not yet flushed to its data page is reapplied. After replay,
the data files reflect every change the log promised, and normal operation
resumes.

QueryX implements redo-only recovery (reapply committed changes). It does NOT
implement undo (rolling back uncommitted changes) because it has no multi-
statement transactions — a scope boundary we state honestly. Production systems
use the ARIES algorithm (analysis, redo, undo) for the full story.

Implemented in Phase 7. No logic yet.
"""
