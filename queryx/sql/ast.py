"""Abstract Syntax Tree (AST) node definitions.

The AST is the structured, in-memory representation of a parsed SQL statement:
a tree of plain dataclasses with no behavior. For example,
`SELECT name FROM users WHERE age > 30` becomes a Select node holding a
projection list ([Column('name')]), a table ('users'), and a where predicate
(Comparison(GT, Column('age'), Literal(30))).

These nodes are the contract between the parser (which produces them) and the
planner/executor (which consume them). Keeping them behavior-free means the same
tree can be printed, validated, or planned without coupling parsing to
execution.

Two marker base classes, Expr and Statement, exist only for clarity and
isinstance checks; the real content is in the concrete subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from queryx.sql.tokens import TokenType
from queryx.storage.page import ColumnType


class Node:
    """Base of every AST node."""


class Expr(Node):
    """An expression: something that evaluates to a value (or a boolean)."""


class Statement(Node):
    """A complete SQL statement."""


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass
class Column(Expr):
    """A column reference, e.g. ``age`` or qualified ``u.age`` (table = 'u')."""
    name: str
    table: Optional[str] = None  # the qualifier (alias or table name), if written

    @property
    def key(self) -> str:
        """The lookup key: 'table.name' if qualified, else 'name'."""
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class Literal(Expr):
    """A constant value, e.g. ``30`` or ``'alice'``. ``type`` is INT or TEXT."""
    value: object
    type: ColumnType


@dataclass
class Star(Expr):
    """The ``*`` token: all columns (in SELECT *) or the COUNT(*) argument."""


@dataclass
class Aggregate(Expr):
    """A scalar aggregate call, e.g. ``COUNT(*)`` or ``AVG(age)``.

    ``func`` is one of COUNT/SUM/AVG/MIN/MAX; ``arg`` is a Column, or Star for
    COUNT(*).
    """
    func: str
    arg: Expr


@dataclass
class Comparison(Expr):
    """A comparison predicate: ``left <op> right`` where op is =, !=, <, >, <=, >=."""
    op: TokenType
    left: Expr
    right: Expr


@dataclass
class And(Expr):
    left: Expr
    right: Expr


@dataclass
class Or(Expr):
    left: Expr
    right: Expr


@dataclass
class Not(Expr):
    operand: Expr


# ---------------------------------------------------------------------------
# Clause helpers
# ---------------------------------------------------------------------------


@dataclass
class ColumnDef:
    """A column declaration in CREATE TABLE: a name and a type."""
    name: str
    type: ColumnType


@dataclass
class OrderItem:
    """One ORDER BY term: a column and a direction (ascending by default)."""
    column: str
    descending: bool = False


@dataclass
class Assignment:
    """One ``column = value`` pair in UPDATE ... SET."""
    column: str
    value: Expr


@dataclass
class Join:
    """An INNER JOIN target: the right table, its optional alias, and ON predicate."""
    table: str
    alias: Optional[str]
    on: Expr


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class CreateTable(Statement):
    table: str
    columns: list[ColumnDef]


@dataclass
class DropTable(Statement):
    table: str


@dataclass
class Insert(Statement):
    table: str
    columns: Optional[list[str]]  # None = positional (all columns, in order)
    values: list[Expr]


@dataclass
class Select(Statement):
    table: str
    projections: list[Expr]              # Column / Star / Aggregate
    distinct: bool = False
    where: Optional[Expr] = None
    group_by: Optional[list[str]] = None  # columns to group on
    having: Optional[Expr] = None         # post-aggregation filter (needs group_by)
    order_by: Optional[list[OrderItem]] = None
    limit: Optional[int] = None
    table_alias: Optional[str] = None     # alias for the FROM table (joins)
    join: Optional[Join] = None           # optional second table (INNER JOIN)


@dataclass
class Update(Statement):
    table: str
    assignments: list[Assignment]
    where: Optional[Expr] = None


@dataclass
class Delete(Statement):
    table: str
    where: Optional[Expr] = None


@dataclass
class CreateIndex(Statement):
    name: str
    table: str
    column: str


@dataclass
class DropIndex(Statement):
    name: str


@dataclass
class Explain(Statement):
    """EXPLAIN <select>: describe the plan instead of running it."""
    query: Select
