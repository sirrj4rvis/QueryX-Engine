"""Index layer (Phase 3) — find rows by key without scanning the whole table.

An index answers "where is the row whose key is X?" (and "which rows have keys
between X and Y?") far faster than reading every page of a heap file. QueryX
implements two classic index structures so we can compare their trade-offs:

    btree.py        A disk-backed B+ tree: balanced, high fan-out, with all
                    data in the leaves linked in sorted order. Supports point
                    lookup AND range scan in O(log_b n), and is the headline
                    data-structures artifact of the project. This is what
                    PostgreSQL and SQLite use as their default index.
    hash_index.py   A hash index: O(1) expected point lookup (insert/search/
                    delete) but NO range or ordered scans. Useful for equality
                    predicates only; included to make the B+ tree's strengths
                    concrete by contrast.

Indexes store key -> row id (the heap location), not the rows themselves. They
sit above storage and below execution, and never import from upper layers.

Implemented in Phase 3. No logic yet.
"""
