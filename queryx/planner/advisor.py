"""Adaptive indexing — a workload analyzer that recommends indexes.

The advisor observes the predicates queries actually run and tallies, per
(table, column), how often that column is filtered and whether by equality or by
range. When a column is filtered often enough but has no index, it recommends
one — suggesting a HASH index for purely-equality workloads (O(1) lookups) and a
B+ TREE when any range filter is seen (ranges need ordering). It only recommends
INT columns, since QueryX index keys are integers.

This mirrors, in miniature, real "missing index" advisors (SQL Server's missing-
index DMVs, PostgreSQL index-advisor extensions): observe the workload, find
columns that are repeatedly scanned without index support, and propose indexes —
leaving the create/keep decision to a human or an auto-apply step.

It is purely advisory and depends only on the catalog (to skip already-indexed
or non-INT columns); it never creates indexes itself. The Database wires it in.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from queryx.catalog import Catalog
from queryx.storage.page import ColumnType


@dataclass
class IndexRecommendation:
    """A suggested index: which table/column, how often used, and which kind."""
    table: str
    column: str
    uses: int
    kind: str   # "hash" (equality-only workload) or "btree" (ranges seen)
    reason: str


class WorkloadAdvisor:
    """Tallies filtered columns and recommends indexes for the hot, unindexed ones."""

    def __init__(self, min_uses: int = 5) -> None:
        self._min_uses = min_uses
        self._equality: dict[tuple[str, str], int] = defaultdict(int)
        self._range: dict[tuple[str, str], int] = defaultdict(int)

    def record_predicate(self, table: str, column: str, is_range: bool) -> None:
        """Record one filtered use of (table, column), equality or range."""
        if is_range:
            self._range[(table, column)] += 1
        else:
            self._equality[(table, column)] += 1

    def uses(self, table: str, column: str) -> int:
        return self._equality[(table, column)] + self._range[(table, column)]

    def recommend(self, catalog: Catalog) -> list[IndexRecommendation]:
        """Recommend indexes for hot, unindexed, INT columns (most-used first)."""
        recommendations: list[IndexRecommendation] = []
        for key in set(self._equality) | set(self._range):
            table, column = key
            uses = self.uses(table, column)
            if uses < self._min_uses:
                continue
            if not catalog.has_table(table):
                continue
            info = catalog.get_table(table)
            if not info.has_column(column):
                continue
            if info.columns[info.position(column)].type != ColumnType.INT:
                continue  # QueryX index keys are integers
            if catalog.index_on(table, column) is not None:
                continue  # already covered
            kind = "btree" if self._range[key] > 0 else "hash"
            reason = (
                f"{uses} filtered queries on {table}.{column} with no index"
                f" ({'ranges seen -> btree' if kind == 'btree' else 'equality-only -> hash'})"
            )
            recommendations.append(IndexRecommendation(table, column, uses, kind, reason))
        return sorted(recommendations, key=lambda r: -r.uses)
