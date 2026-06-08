"""Execution layer (Phase 5) — run a query plan with the volcano model.

This layer takes the operator tree the planner produced and actually produces
rows. It uses the classic *volcano (iterator) model*: every operator implements
open() / next() / close(), operators are composed into a tree, and each one
pulls rows on demand from its child by calling the child's next(). Rows flow up
the tree one at a time, so a query never has to materialize an entire table in
memory (except where an operator inherently must, e.g. Sort).

Modules (built in Phase 5):
    operators.py    SeqScan, IndexScan, Filter, Projection, Sort, Limit,
                    Distinct, and a scalar Aggregate (COUNT(*)/SUM/AVG/MIN/MAX
                    without GROUP BY). Each is a self-contained iterator.

Execution depends on the planner (for the chosen plan), the index and storage
layers (to fetch rows), and the catalog (for schema), but never on the SQL
layer above it.

Implemented in Phase 5. No logic yet.
"""
