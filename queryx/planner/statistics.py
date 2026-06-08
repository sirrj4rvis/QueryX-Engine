"""Table statistics — the raw inputs to cost estimation.

A cost-based optimizer cannot choose well without knowing the shape of the data.
This module will maintain, per table:
    - row count (how many live rows the table holds),
    - which columns have indexes (and of what kind),
    - a simple cardinality / selectivity estimate: given a predicate like
      `age > 30`, roughly what fraction of rows match?

QueryX keeps this deliberately simple (e.g. uniform-distribution assumptions),
and we will state that simplification honestly. Real databases maintain
histograms, most-common-value lists, and correlation stats (PostgreSQL's
pg_statistic, refreshed by ANALYZE); we approximate.

Implemented in Phase 6. No logic yet.
"""
