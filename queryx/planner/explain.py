"""EXPLAIN — render the chosen query plan and its estimated cost.

`EXPLAIN <query>` runs the query through the parser and optimizer but, instead
of executing, prints the operator tree the optimizer chose and the cost it
estimated for each node. This makes the optimizer's reasoning visible: you can
see *that* it picked an IndexScan over a SeqScan and *why* (the estimated cost).

This mirrors PostgreSQL's and SQLite's EXPLAIN, which are the primary tools
engineers use to understand and tune query performance — and an excellent way to
demonstrate, in an interview, that the optimizer is real and not hand-waved.

Implemented in Phase 6. No logic yet.
"""
