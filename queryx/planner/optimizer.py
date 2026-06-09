"""The cost-based optimizer — choose the cheapest access path.

Given a WHERE predicate, table statistics, and the available indexes, the
optimizer estimates the cost of each way of producing the rows and picks the
cheapest. In QueryX's focused scope the central decision is access-path
selection:

    SeqScan   read every data page.       cost ~ num_data_pages
    IndexScan descend an index, fetch each match. cost ~ descent + matched_rows

Cost is modelled in PAGE ACCESSES (the dominant real cost), not CPU
comparisons. An IndexScan wins only when the predicate is selective enough that
`descent + matched_rows < num_data_pages` — which mirrors why real databases
ignore an index for an unselective predicate.

A predicate's comparison is "sargable" (index-usable) if it is `column <op>
literal` on an indexed column, with op compatible with the index kind (a hash
index serves only `=`; a B+ tree also serves ranges). For an AND of conditions,
any conjunct can drive an index (the others become a residual Filter). For OR or
NOT we conservatively fall back to SeqScan.

The optimizer does NOT build operators (it has no storage handles); it returns an
AccessPath describing the choice, which the Database turns into operators and the
EXPLAIN formatter renders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from queryx.catalog import IndexInfo
from queryx.planner.statistics import (
    TableStats, comparison_selectivity, estimate_rows, predicate_selectivity,
)
from queryx.sql import ast
from queryx.sql.tokens import TokenType

_RANGE_OPS = (TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE)
#: flips a comparison operator when the column is on the right (`5 < age` -> `age > 5`)
_FLIP = {
    TokenType.LT: TokenType.GT, TokenType.GT: TokenType.LT,
    TokenType.LTE: TokenType.GTE, TokenType.GTE: TokenType.LTE,
    TokenType.EQ: TokenType.EQ, TokenType.NEQ: TokenType.NEQ,
}
#: assumed B+ tree fan-out, for estimating tree height (descent page reads)
_FANOUT = 128


@dataclass
class AccessPath:
    """The optimizer's chosen way to scan a table for a predicate."""
    method: str                 # "SeqScan" or "IndexScan"
    est_cost: float
    est_rows: int
    index_name: Optional[str] = None
    index_kind: Optional[str] = None
    column: Optional[str] = None
    op: Optional[TokenType] = None
    value: object = None
    residual: Optional[ast.Expr] = None  # filter to apply after the scan (or full WHERE for SeqScan)
    seqscan_cost: float = 0.0            # the SeqScan alternative's cost, for EXPLAIN


def normalize_comparison(cmp: ast.Comparison) -> Optional[tuple[str, TokenType, object]]:
    """Reduce `column <op> literal` (either order) to (column, op, value), else None."""
    if isinstance(cmp.left, ast.Column) and isinstance(cmp.right, ast.Literal):
        return cmp.left.name, cmp.op, cmp.right.value
    if isinstance(cmp.right, ast.Column) and isinstance(cmp.left, ast.Literal):
        return cmp.right.name, _FLIP[cmp.op], cmp.left.value
    return None


def sargable_comparisons(where: Optional[ast.Expr]) -> list[tuple[str, TokenType, object]]:
    """Comparisons reachable through top-level ANDs that an index could serve."""
    if where is None:
        return []
    if isinstance(where, ast.Comparison):
        norm = normalize_comparison(where)
        return [norm] if norm else []
    if isinstance(where, ast.And):
        return sargable_comparisons(where.left) + sargable_comparisons(where.right)
    return []  # OR / NOT: don't drive an index


def _btree_descent_cost(row_count: int) -> float:
    """Approximate B+ tree height in page reads: log_fanout(row_count), min 2."""
    return max(2.0, math.log(max(row_count, 2), _FANOUT) + 1.0)


def _is_whole_predicate(where: ast.Expr, column: str, op: TokenType, value: object) -> bool:
    """True if the entire WHERE is exactly this one comparison (no residual needed)."""
    return isinstance(where, ast.Comparison) and normalize_comparison(where) == (column, op, value)


def choose_access_path(
    where: Optional[ast.Expr],
    stats: TableStats,
    indexes: list[IndexInfo],
    n_distinct_by_column: dict[str, int],
) -> AccessPath:
    """Return the cheapest access path for ``where`` over a table with ``stats``."""
    seq_cost = float(max(1, stats.num_data_pages))
    seq_rows = estimate_rows(stats.row_count, predicate_selectivity(where, n_distinct_by_column))
    best = AccessPath("SeqScan", seq_cost, seq_rows, residual=where, seqscan_cost=seq_cost)

    index_by_column = {i.column: i for i in indexes}
    for column, op, value in sargable_comparisons(where):
        info = index_by_column.get(column)
        if info is None:
            continue
        if info.kind == "hash" and op != TokenType.EQ:
            continue  # a hash index serves only equality
        selectivity = comparison_selectivity(op, info.n_distinct)
        matched = max(1, estimate_rows(stats.row_count, selectivity))
        descent = 1.0 if info.kind == "hash" else _btree_descent_cost(stats.row_count)
        index_cost = descent + matched
        if index_cost < best.est_cost:
            residual = None if _is_whole_predicate(where, column, op, value) else where
            best = AccessPath(
                method="IndexScan", est_cost=index_cost, est_rows=matched,
                index_name=info.name, index_kind=info.kind, column=column,
                op=op, value=value, residual=residual, seqscan_cost=seq_cost,
            )
    return best
