"""QueryX — a relational database engine built from scratch in Python.

This package implements a miniature but architecturally faithful relational
database engine. It is organized *by layer*, mirroring the path a SQL query
takes through a real database:

    SQL text
      -> sql/        lexer + parser            (text  -> AST)
      -> planner/    optimizer + statistics    (AST   -> query plan)
      -> execution/  volcano operators         (plan  -> rows)
      -> index/      B+ tree / hash index      (key   -> row location)
      -> storage/    pages, pager, buffer pool, heap files (bytes <-> disk)

    wal/             write-ahead log + recovery — guards every mutation.
    catalog.py       the system catalog: which tables/columns/indexes exist.
    database.py      the top-level facade that wires the pipeline together.

DEPENDENCY RULE (enforced by convention, made visible by this layout):
dependencies flow strictly DOWNWARD. An upper layer may import a lower one;
a lower layer must NEVER import an upper one. If `storage/` ever imports
`sql/`, we have a cycle and have lost the ability to test layers in isolation.
"""

__version__ = "0.1.0"
