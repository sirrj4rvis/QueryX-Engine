"""Hash index — O(1) expected point lookup, no range scans.

A hash index maps a key to a bucket via a hash function, giving expected
constant-time insert, search, and delete for *equality* predicates. The price
is that it supports nothing else: hashing destroys order, so range queries
(`age > 30`) and ordered scans (`ORDER BY`) are impossible on a hash index.

QueryX includes it primarily as a foil to the B+ tree: benchmarking the two
makes the optimizer's eventual choice ("equality on a hash-indexed column ->
hash; range -> B+ tree or seq scan") concrete and defensible. This is analogous
to PostgreSQL's hash index access method (SQLite has no hash index at all).

Implemented in Phase 3. No logic yet.
"""
