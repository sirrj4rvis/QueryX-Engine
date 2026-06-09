"""Table statistics and selectivity estimation — the inputs to cost estimation.

A cost-based optimizer cannot choose well without estimating how many rows a
predicate will match. QueryX keeps two cheap, persisted statistics (in the
catalog): a table's ``row_count`` and an index's ``n_distinct`` (distinct key
count). From those it estimates SELECTIVITY — the fraction of rows a predicate
passes — using the classic System R (Selinger) approach:

  * equality (col = v):  1 / n_distinct  if known, else a default (0.1)
  * inequality (col != v): 1 - equality
  * range (<, >, <=, >=): a fixed default (1/3) — we have no histograms
  * AND: multiply (assume independence)
  * OR:  s1 + s2 - s1*s2 (inclusion-exclusion under independence)
  * NOT: 1 - s

These "magic constants" and the independence assumption are exactly what real
optimizers fall back to without histograms — and exactly where their estimates
go wrong on correlated columns. QueryX is honest about using them.

HONESTY NOTE: ``row_count`` is maintained on insert/delete and ``n_distinct`` is
computed once when an index is built, so both can go STALE after later
mutations. Production databases refresh statistics with ANALYZE; QueryX does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from queryx.sql import ast
from queryx.sql.tokens import TokenType

#: Selinger-style default selectivities, used when no better statistic exists.
DEFAULT_EQ_SELECTIVITY = 0.1
RANGE_SELECTIVITY = 1.0 / 3.0

_RANGE_OPS = (TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE)


@dataclass
class TableStats:
    """The statistics the optimizer reads to estimate cost and result size."""
    row_count: int
    num_data_pages: int


def comparison_selectivity(op: TokenType, n_distinct: Optional[int] = None) -> float:
    """Estimate the fraction of rows passing a single comparison."""
    if op == TokenType.EQ:
        return 1.0 / n_distinct if n_distinct else DEFAULT_EQ_SELECTIVITY
    if op == TokenType.NEQ:
        eq = 1.0 / n_distinct if n_distinct else DEFAULT_EQ_SELECTIVITY
        return 1.0 - eq
    if op in _RANGE_OPS:
        return RANGE_SELECTIVITY
    return 1.0  # pragma: no cover - all comparison ops are covered above


def predicate_selectivity(expr: Optional[ast.Expr], n_distinct_by_column: dict[str, int]) -> float:
    """Estimate the selectivity of a whole WHERE predicate (1.0 if no predicate).

    Column n_distinct values (where known) sharpen equality estimates; otherwise
    the defaults apply. AND/OR/NOT compose under the independence assumption.
    """
    if expr is None:
        return 1.0
    if isinstance(expr, ast.Comparison):
        column = _comparison_column(expr)
        nd = n_distinct_by_column.get(column) if column else None
        return comparison_selectivity(expr.op, nd)
    if isinstance(expr, ast.And):
        return (predicate_selectivity(expr.left, n_distinct_by_column)
                * predicate_selectivity(expr.right, n_distinct_by_column))
    if isinstance(expr, ast.Or):
        s1 = predicate_selectivity(expr.left, n_distinct_by_column)
        s2 = predicate_selectivity(expr.right, n_distinct_by_column)
        return s1 + s2 - s1 * s2
    if isinstance(expr, ast.Not):
        return 1.0 - predicate_selectivity(expr.operand, n_distinct_by_column)
    return 1.0


def _comparison_column(cmp: ast.Comparison) -> Optional[str]:
    """The column name in a `column <op> literal` comparison (either side)."""
    if isinstance(cmp.left, ast.Column) and isinstance(cmp.right, ast.Literal):
        return cmp.left.name
    if isinstance(cmp.right, ast.Column) and isinstance(cmp.left, ast.Literal):
        return cmp.right.name
    return None


def estimate_rows(row_count: int, selectivity: float) -> int:
    """Estimated number of matching rows, clamped to [0, row_count]."""
    return max(0, min(row_count, round(row_count * selectivity)))
