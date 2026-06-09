"""EXPLAIN — render the chosen query plan and its estimated cost.

`EXPLAIN <select>` runs the query through the parser and optimizer but, instead
of executing, prints the operator tree the optimizer chose, the access path's
estimated cost and row count, and the SeqScan alternative's cost — so the
optimizer's decision is visible and defensible. This mirrors the role of
PostgreSQL's and SQLite's EXPLAIN.

The tree is printed root-at-top, each child indented under its parent, matching
how the volcano operators are stacked (the root pulls from its child, down to the
scan at the leaf).
"""

from __future__ import annotations

from typing import Optional

from queryx.catalog import IndexInfo, TableInfo
from queryx.planner.optimizer import AccessPath, choose_access_path
from queryx.planner.statistics import TableStats
from queryx.sql import ast
from queryx.sql.tokens import TokenType

_OP_SYMBOL = {
    TokenType.EQ: "=", TokenType.NEQ: "!=", TokenType.LT: "<",
    TokenType.GT: ">", TokenType.LTE: "<=", TokenType.GTE: ">=",
}


def expr_to_str(expr: ast.Expr) -> str:
    """Render a predicate expression back to readable SQL-ish text."""
    if isinstance(expr, ast.Column):
        return expr.name
    if isinstance(expr, ast.Literal):
        return repr(expr.value)
    if isinstance(expr, ast.Comparison):
        return f"{expr_to_str(expr.left)} {_OP_SYMBOL[expr.op]} {expr_to_str(expr.right)}"
    if isinstance(expr, ast.And):
        return f"({expr_to_str(expr.left)} AND {expr_to_str(expr.right)})"
    if isinstance(expr, ast.Or):
        return f"({expr_to_str(expr.left)} OR {expr_to_str(expr.right)})"
    if isinstance(expr, ast.Not):
        return f"(NOT {expr_to_str(expr.operand)})"
    return "?"


def _scan_line(ap: AccessPath, table: str) -> str:
    if ap.method == "IndexScan":
        cond = f"{ap.column} {_OP_SYMBOL[ap.op]} {ap.value!r}"
        head = f"IndexScan using {ap.index_name} ({ap.index_kind}) on {table} [{cond}]"
    else:
        head = f"SeqScan on {table}"
    return f"{head}  (cost={ap.est_cost:.1f} rows={ap.est_rows})"


def format_plan(
    select: ast.Select,
    table_info: TableInfo,
    stats: TableStats,
    indexes: list[IndexInfo],
    n_distinct_by_column: dict[str, int],
) -> str:
    """Produce the multi-line EXPLAIN text for a SELECT statement."""
    ap = choose_access_path(select.where, stats, indexes, n_distinct_by_column)

    # Build the operator chain from leaf (scan) up to the root.
    nodes: list[str] = [_scan_line(ap, table_info.name)]
    if ap.residual is not None:
        nodes.append(f"Filter: {expr_to_str(ap.residual)}")

    aggregates = [p for p in select.projections if isinstance(p, ast.Aggregate)]
    if aggregates:
        from queryx.execution.operators import aggregate_label
        nodes.append("Aggregate: " + ", ".join(aggregate_label(a) for a in aggregates))
    else:
        if select.order_by:
            keys = ", ".join(f"{o.column}{' DESC' if o.descending else ''}" for o in select.order_by)
            nodes.append(f"Sort: {keys}")
        stars = any(isinstance(p, ast.Star) for p in select.projections)
        if not stars:
            cols = ", ".join(p.name for p in select.projections)  # type: ignore[attr-defined]
            nodes.append(f"Projection: {cols}")
        if select.distinct:
            nodes.append("Distinct")
        if select.limit is not None:
            nodes.append(f"Limit: {select.limit}")

    # Print root first, each deeper node indented further.
    lines = []
    for depth, text in enumerate(reversed(nodes)):
        lines.append("  " * depth + ("-> " if depth else "") + text)

    footer = (
        f"(chose {ap.method} at cost {ap.est_cost:.1f}; "
        f"SeqScan alternative cost {ap.seqscan_cost:.1f})"
    )
    return "\n".join(lines + ["", footer])
