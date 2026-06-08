"""Volcano-model execution operators.

Each operator is an iterator with three methods:
    open()   prepare to produce rows (open children, allocate state).
    next()   return the next row, or a sentinel when exhausted; typically pulls
             one or more rows from its child(ren) to do so.
    close()  release resources (close children, free state).

Composing operators into a tree expresses a query plan. Pulling next() at the
root cascades down the tree, so rows are produced lazily, one at a time.

Operators in scope (Phase 5):
    SeqScan      yields every row of a heap file (a full table scan).
    IndexScan    yields rows matching a key/range via a B+ tree or hash index.
    Filter       passes through only rows satisfying a predicate.
    Projection   reshapes each row to the selected columns (and expressions).
    Sort         buffers all input rows and yields them in ORDER BY order
                 (a blocking operator — the one place we materialize input).
    Limit        yields at most N rows then stops pulling.
    Distinct     drops duplicate rows.
    Aggregate    scalar COUNT(*)/SUM/AVG/MIN/MAX over all rows (no GROUP BY),
                 yielding a single result row.

Implemented in Phase 5. No logic yet.
"""
