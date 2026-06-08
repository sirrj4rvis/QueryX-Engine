"""Abstract Syntax Tree (AST) node definitions.

The AST is the structured, in-memory representation of a parsed SQL statement —
a tree of plain data objects (likely dataclasses) with no behavior. For example,
`SELECT name FROM users WHERE age > 30` becomes a Select node holding a
projection list ([Column('name')]), a from-target ('users'), and a where
predicate (BinaryOp('>', Column('age'), Literal(30))).

Node families this module will define:
    - Statements: CreateTable, DropTable, Insert, Select, Update, Delete,
      CreateIndex, DropIndex.
    - Expressions/predicates: Column, Literal, BinaryOp (=, !=, <>, <, >, <=,
      >=), logical AND/OR/NOT, and scalar aggregate calls (COUNT/SUM/AVG/
      MIN/MAX).
    - Clause helpers: order-by item, limit, distinct flag.

The AST is the contract between the parser (which produces it) and the planner
(which consumes it). Keeping nodes behavior-free means the same tree can be
printed, validated, or planned without coupling parsing to execution.

Implemented in Phase 4. No logic yet.
"""
