"""Disk-backed B+ tree index — the headline data-structures artifact.

A B+ tree is a balanced search tree tuned for disk: each node is a page, so the
*fan-out* (children per node) is large and the tree stays very shallow — a few
levels cover millions of keys. Two properties distinguish a B+ tree from a plain
B-tree, and both matter here:

    - All actual data (key -> row id) lives in the LEAF nodes; internal nodes
      hold only separator keys to route searches. This keeps internal nodes
      dense, raising fan-out and shrinking the tree.
    - The leaves are linked in sorted order, so a range scan is "descend once,
      then walk the leaf chain" — no re-traversal per key.

This module will implement leaf and internal node layouts, point lookup, range
scan, insertion with node splitting (and propagating splits up to the root).
Deletion/merging may be simplified — scope will be stated explicitly in Phase 3.

Implemented in Phase 3. No logic yet.
"""
