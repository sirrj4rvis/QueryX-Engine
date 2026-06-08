"""Planner / optimizer layer (Phase 6) — decide HOW to run a query.

The parser tells us *what* the user asked for; the planner decides *how* to get
it efficiently. Given an AST and table statistics, it considers the available
physical strategies (e.g. SeqScan vs. IndexScan for a WHERE predicate),
estimates the cost of each, and emits the cheapest as a tree of execution
operators. This is *cost-based optimization* — the heart of what makes a query
engine "smart" rather than literal.

Modules (built in Phase 6):
    statistics.py   Per-table stats: row count, which columns are indexed, and
                    a simple cardinality/selectivity estimate (how many rows a
                    predicate is likely to match). The raw inputs to costing.
    optimizer.py    Cost models for each access path and the logic that picks
                    the cheaper plan for a given predicate.
    explain.py      EXPLAIN <query>: render the chosen plan and its estimated
                    cost as readable text, so the optimizer's decisions are
                    inspectable (and demonstrable in an interview).

Implemented in Phase 6. No logic yet.
"""
