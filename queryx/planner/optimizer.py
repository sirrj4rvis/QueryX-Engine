"""The cost-based optimizer — choose the cheapest access path.

Given a parsed query and table statistics, the optimizer estimates the cost of
each way of producing the result and picks the cheapest. In QueryX's focused
scope the central decision is access-path selection for a WHERE predicate:

    - SeqScan:   read every page of the heap file. Cost ~ number of pages.
                 Cheap relative to an index when the predicate matches most rows.
    - IndexScan: descend an index and fetch only matching rows. Cost ~ index
                 traversal + one fetch per matching row. Wins when the predicate
                 is selective (matches few rows) and a usable index exists.

The optimizer outputs the operator tree the execution engine runs. We model cost
in terms of page/row counts (a proxy for disk I/O), the dominant cost in a real
database — not CPU comparisons.

Implemented in Phase 6. No logic yet.
"""
