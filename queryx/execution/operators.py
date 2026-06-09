"""Volcano-model execution operators.

Each operator is an iterator with three methods:
    open()   prepare to produce rows (open children, allocate state)
    next()   return the next row (a tuple of values), or None when exhausted;
             typically by pulling one or more rows from its child(ren)
    close()  release resources (close children, free state)

Composing operators into a tree expresses a query plan; pulling next() at the
root cascades down the tree, so rows are produced lazily, one at a time. Most
operators here are STREAMING (Filter, Projection, Limit); a few are BLOCKING
(Sort must read all input before emitting the smallest row; Aggregate must see
every row before it knows the result) — the blocking ones are where a query
materializes data and where its cost concentrates.

Each operator exposes ``column_names`` describing the schema of the rows it
emits, so a parent can map a column reference to a tuple position. A row is a
plain Python tuple of values, positionally aligned with ``column_names``.

Complexity: SeqScan is O(rows); Filter/Projection/Limit/Distinct are O(rows)
streaming with O(1) (or O(distinct)) state; Sort is O(rows log rows) and buffers
all rows; scalar Aggregate is O(rows) with O(1) state.
"""

from __future__ import annotations

import operator as _op
from collections import Counter
from typing import Iterable, Iterator, Optional

from queryx.sql import ast
from queryx.sql.tokens import TokenType
from queryx.storage.heap_file import HeapFile, RowId
from queryx.storage.page import ColumnType, deserialize_row

Row = tuple

# Map comparison token types to Python comparison functions.
_COMPARATORS = {
    TokenType.EQ: _op.eq, TokenType.NEQ: _op.ne,
    TokenType.LT: _op.lt, TokenType.GT: _op.gt,
    TokenType.LTE: _op.le, TokenType.GTE: _op.ge,
}


def column_index(column_names: list[str]) -> dict[str, int]:
    """Map column names to positions, resolving qualified and bare references.

    Every full name maps to its position (e.g. 'u.id' -> 0). In addition, a bare
    suffix maps to its position IFF it is unambiguous across the schema (e.g.
    'name' -> 1 when only one column is named name). An ambiguous bare name
    (e.g. 'id' present in both joined tables) is deliberately left unmapped, so
    referencing it raises rather than silently picking one side.
    """
    idx = {name: i for i, name in enumerate(column_names)}
    bare_counts = Counter(name.split(".")[-1] for name in column_names)
    for i, name in enumerate(column_names):
        bare = name.split(".")[-1]
        if bare_counts[bare] == 1:
            idx.setdefault(bare, i)
    return idx


def evaluate(expr: ast.Expr, row: Row, index_of: dict[str, int]) -> object:
    """Evaluate an expression against ``row`` given a column-name->position map.

    Predicates (Comparison/And/Or/Not) return a bool; Literal/Column return the
    underlying value. Assumes a well-typed query (no NULL three-valued logic).
    """
    if isinstance(expr, ast.Literal):
        return expr.value
    if isinstance(expr, ast.Column):
        return row[index_of[expr.key]]
    if isinstance(expr, ast.Comparison):
        left = evaluate(expr.left, row, index_of)
        right = evaluate(expr.right, row, index_of)
        return _COMPARATORS[expr.op](left, right)
    if isinstance(expr, ast.And):
        return bool(evaluate(expr.left, row, index_of)) and bool(evaluate(expr.right, row, index_of))
    if isinstance(expr, ast.Or):
        return bool(evaluate(expr.left, row, index_of)) or bool(evaluate(expr.right, row, index_of))
    if isinstance(expr, ast.Not):
        return not bool(evaluate(expr.operand, row, index_of))
    if isinstance(expr, ast.Aggregate):
        # In a HAVING predicate, an aggregate resolves to its precomputed value,
        # carried in the row under its label (e.g. "COUNT(*)").
        return row[index_of[aggregate_label(expr)]]
    raise TypeError(f"cannot evaluate expression node {type(expr).__name__}")


class Operator:
    """Base class: an iterator with open()/next()/close()."""

    column_names: list[str]

    def open(self) -> None:
        raise NotImplementedError

    def next(self) -> Optional[Row]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __iter__(self) -> Iterator[Row]:
        """Convenience: open, yield every row, then close (used on the root)."""
        self.open()
        try:
            while True:
                row = self.next()
                if row is None:
                    return
                yield row
        finally:
            self.close()


class SeqScan(Operator):
    """Yield every live row of a heap file, deserialized to a value tuple."""

    def __init__(self, heap: HeapFile, column_names: list[str], column_types: list[ColumnType]) -> None:
        self._heap = heap
        self.column_names = column_names
        self._types = column_types
        self._gen: Optional[Iterator[tuple[RowId, bytes]]] = None

    def open(self) -> None:
        self._gen = self._heap.scan()

    def next(self) -> Optional[Row]:
        assert self._gen is not None
        item = next(self._gen, None)
        if item is None:
            return None
        _rid, record = item
        return deserialize_row(self._types, record)

    def close(self) -> None:
        self._gen = None


class IndexScan(Operator):
    """Yield heap rows located via an index, given an iterable of RowIds.

    The index lookup (point or range) is performed by the caller/optimizer, which
    passes the resulting RowIds here; this operator just fetches and deserializes
    them. Decoupling the operator from the index kind keeps it simple and lets the
    Phase 6 optimizer decide how the RowIds were produced.
    """

    def __init__(
        self,
        heap: HeapFile,
        rowids: Iterable[RowId],
        column_names: list[str],
        column_types: list[ColumnType],
    ) -> None:
        self._heap = heap
        self._rowids = rowids
        self.column_names = column_names
        self._types = column_types
        self._gen: Optional[Iterator[RowId]] = None

    def open(self) -> None:
        self._gen = iter(self._rowids)

    def next(self) -> Optional[Row]:
        assert self._gen is not None
        for rid in self._gen:
            record = self._heap.get(rid)
            if record is not None:  # skip rows deleted since the index entry was made
                return deserialize_row(self._types, record)
        return None

    def close(self) -> None:
        self._gen = None


class Filter(Operator):
    """Pass through only rows for which ``predicate`` evaluates truthy."""

    def __init__(self, child: Operator, predicate: ast.Expr) -> None:
        self._child = child
        self._predicate = predicate
        self.column_names = child.column_names
        self._index_of: dict[str, int] = {}

    def open(self) -> None:
        self._child.open()
        self._index_of = column_index(self.column_names)

    def next(self) -> Optional[Row]:
        while True:
            row = self._child.next()
            if row is None:
                return None
            if evaluate(self._predicate, row, self._index_of):
                return row

    def close(self) -> None:
        self._child.close()


class Projection(Operator):
    """Reshape each row down to a selected list of columns (by name)."""

    def __init__(self, child: Operator, columns: list[str]) -> None:
        self._child = child
        self.column_names = columns
        self._positions: list[int] = []

    def open(self) -> None:
        self._child.open()
        index_of = column_index(self._child.column_names)
        self._positions = [index_of[name] for name in self.column_names]

    def next(self) -> Optional[Row]:
        row = self._child.next()
        if row is None:
            return None
        return tuple(row[i] for i in self._positions)

    def close(self) -> None:
        self._child.close()


class Sort(Operator):
    """Buffer all input rows and emit them in ORDER BY order (blocking)."""

    def __init__(self, child: Operator, order_by: list[ast.OrderItem]) -> None:
        self._child = child
        self._order_by = order_by
        self.column_names = child.column_names
        self._rows: list[Row] = []
        self._pos = 0

    def open(self) -> None:
        self._child.open()
        index_of = column_index(self.column_names)
        rows: list[Row] = []
        while True:
            row = self._child.next()
            if row is None:
                break
            rows.append(row)
        # Stable multi-key sort: sort by the least-significant key first, working
        # up to the most-significant, so mixed ASC/DESC directions all hold.
        for item in reversed(self._order_by):
            col = index_of[item.column]
            rows.sort(key=lambda r, c=col: r[c], reverse=item.descending)
        self._rows = rows
        self._pos = 0

    def next(self) -> Optional[Row]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def close(self) -> None:
        self._child.close()
        self._rows = []


class Limit(Operator):
    """Emit at most ``count`` rows, then stop pulling from the child."""

    def __init__(self, child: Operator, count: int) -> None:
        self._child = child
        self._count = count
        self.column_names = child.column_names
        self._emitted = 0

    def open(self) -> None:
        self._child.open()
        self._emitted = 0

    def next(self) -> Optional[Row]:
        if self._emitted >= self._count:
            return None
        row = self._child.next()
        if row is None:
            return None
        self._emitted += 1
        return row

    def close(self) -> None:
        self._child.close()


class Distinct(Operator):
    """Drop duplicate rows, preserving first-seen order."""

    def __init__(self, child: Operator) -> None:
        self._child = child
        self.column_names = child.column_names
        self._seen: set[Row] = set()

    def open(self) -> None:
        self._child.open()
        self._seen = set()

    def next(self) -> Optional[Row]:
        while True:
            row = self._child.next()
            if row is None:
                return None
            if row not in self._seen:
                self._seen.add(row)
                return row

    def close(self) -> None:
        self._child.close()
        self._seen = set()


def aggregate_label(agg: ast.Aggregate) -> str:
    """The output column name for an aggregate, e.g. 'COUNT(*)' or 'AVG(age)'."""
    inner = "*" if isinstance(agg.arg, ast.Star) else agg.arg.name
    return f"{agg.func}({inner})"


class Aggregate(Operator):
    """Compute scalar aggregates over all input rows; emit exactly one row.

    Supports COUNT(*)/COUNT(col)/SUM/AVG/MIN/MAX without GROUP BY. With no input
    rows, COUNT is 0 and the others are None (we have no NULL logic; this mirrors
    SQL's "aggregate of the empty set" for SUM/AVG/MIN/MAX).
    """

    def __init__(self, child: Operator, aggregates: list[ast.Aggregate]) -> None:
        self._child = child
        self._aggregates = aggregates
        self.column_names = [aggregate_label(a) for a in aggregates]
        self._result: Optional[Row] = None
        self._done = False

    def open(self) -> None:
        self._child.open()
        index_of = {name: i for i, name in enumerate(self._child.column_names)}

        count = 0
        # per-aggregate accumulators for non-COUNT functions
        values: list[list[object]] = [[] for _ in self._aggregates]
        while True:
            row = self._child.next()
            if row is None:
                break
            count += 1
            for i, agg in enumerate(self._aggregates):
                if isinstance(agg.arg, ast.Column):
                    values[i].append(row[index_of[agg.arg.name]])

        result: list[object] = []
        for i, agg in enumerate(self._aggregates):
            vals = values[i]
            if agg.func == "COUNT":
                # COUNT(*) and COUNT(col) both count rows (no NULLs in QueryX).
                result.append(count)
            elif not vals:
                result.append(None)
            elif agg.func == "SUM":
                result.append(sum(vals))
            elif agg.func == "AVG":
                result.append(sum(vals) / len(vals))
            elif agg.func == "MIN":
                result.append(min(vals))
            elif agg.func == "MAX":
                result.append(max(vals))
            else:  # pragma: no cover - parser restricts funcs to the set above
                raise ValueError(f"unknown aggregate {agg.func!r}")
        self._result = tuple(result)
        self._done = False

    def next(self) -> Optional[Row]:
        if self._done:
            return None
        self._done = True
        return self._result

    def close(self) -> None:
        self._child.close()
        self._result = None


def aggregates_in(expr: Optional[ast.Expr]) -> list[ast.Aggregate]:
    """Every Aggregate node inside a predicate (used to find HAVING aggregates)."""
    if expr is None:
        return []
    if isinstance(expr, ast.Aggregate):
        return [expr]
    if isinstance(expr, ast.Comparison):
        return aggregates_in(expr.left) + aggregates_in(expr.right)
    if isinstance(expr, (ast.And, ast.Or)):
        return aggregates_in(expr.left) + aggregates_in(expr.right)
    if isinstance(expr, ast.Not):
        return aggregates_in(expr.operand)
    return []


def _finalize(func: str, count: int, values: list) -> object:
    if func == "COUNT":
        return count               # COUNT(*) and COUNT(col) both count group rows
    if not values:
        return None                # a group always has >=1 row, so unreachable here
    if func == "SUM":
        return sum(values)
    if func == "AVG":
        return sum(values) / len(values)
    if func == "MIN":
        return min(values)
    if func == "MAX":
        return max(values)
    raise ValueError(f"unknown aggregate {func!r}")  # pragma: no cover


class HashAggregate(Operator):
    """GROUP BY: partition rows by a key, aggregate each group, filter with HAVING.

    Builds a hash map from the group key (the GROUP BY column values) to per-group
    accumulators in a single streaming pass, then emits one row per surviving
    group. Output columns follow the projection: each item is either a GROUP BY
    column or an aggregate. HAVING is evaluated against a row exposing both the
    group columns and the aggregate values (by label), so it may reference either.
    """

    def __init__(
        self,
        child: Operator,
        group_cols: list[str],
        projections: list[ast.Expr],
        having: Optional[ast.Expr] = None,
    ) -> None:
        self._child = child
        self._group_cols = group_cols
        self._projections = projections
        self._having = having
        # Aggregates to compute = those in the projection plus those in HAVING,
        # de-duplicated by label.
        seen: dict[str, ast.Aggregate] = {}
        for agg in [p for p in projections if isinstance(p, ast.Aggregate)] + aggregates_in(having):
            seen.setdefault(aggregate_label(agg), agg)
        self._aggs = list(seen.values())
        self.column_names = [
            p.name if isinstance(p, ast.Column) else aggregate_label(p)
            for p in projections
        ]
        self._rows: list[Row] = []
        self._pos = 0

    def open(self) -> None:
        self._child.open()
        idx = column_index(self._child.column_names)
        labels = [aggregate_label(a) for a in self._aggs]

        # group key -> {"count": n, label: [values...]}
        groups: dict[tuple, dict] = {}
        order: list[tuple] = []  # preserve first-seen group order
        while True:
            row = self._child.next()
            if row is None:
                break
            key = tuple(row[idx[c]] for c in self._group_cols)
            acc = groups.get(key)
            if acc is None:
                acc = {"count": 0, **{label: [] for label in labels}}
                groups[key] = acc
                order.append(key)
            acc["count"] += 1
            for agg, label in zip(self._aggs, labels):
                if isinstance(agg.arg, ast.Column):
                    acc[label].append(row[idx[agg.arg.name]])

        # Finalize each group into a value map, apply HAVING, then project.
        out_names = list(self._group_cols) + labels
        rows: list[Row] = []
        for key in order:
            acc = groups[key]
            vmap = dict(zip(self._group_cols, key))
            for agg, label in zip(self._aggs, labels):
                vmap[label] = _finalize(agg.func, acc["count"], acc[label])
            if self._having is not None:
                hv_row = tuple(vmap[n] for n in out_names)
                hv_idx = column_index(out_names)
                if not evaluate(self._having, hv_row, hv_idx):
                    continue
            rows.append(tuple(
                vmap[p.name] if isinstance(p, ast.Column) else vmap[aggregate_label(p)]
                for p in self._projections
            ))
        self._rows = rows
        self._pos = 0

    def next(self) -> Optional[Row]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def close(self) -> None:
        self._child.close()
        self._rows = []


class NestedLoopJoin(Operator):
    """Inner join by the textbook algorithm: for each left row, scan the right.

    The right input is materialized once at open() (both join inputs are base-
    table scans here), then for every left row we walk the right rows, build the
    concatenated row, and emit it if the ON predicate holds. Cost is O(left x
    right) — the baseline an index nested-loop improves on.
    """

    def __init__(self, left: Operator, right: Operator, on: ast.Expr) -> None:
        self._left = left
        self._right = right
        self._on = on
        self.column_names = list(left.column_names) + list(right.column_names)
        self._idx = column_index(self.column_names)
        self._right_rows: list[Row] = []
        self._left_row: Optional[Row] = None
        self._ri = 0

    def open(self) -> None:
        self._left.open()
        self._right.open()
        self._right_rows = []
        while True:
            r = self._right.next()
            if r is None:
                break
            self._right_rows.append(r)
        self._right.close()
        self._left_row = None
        self._ri = 0

    def next(self) -> Optional[Row]:
        while True:
            if self._left_row is None:
                self._left_row = self._left.next()
                if self._left_row is None:
                    return None
                self._ri = 0
            while self._ri < len(self._right_rows):
                combined = self._left_row + self._right_rows[self._ri]
                self._ri += 1
                if evaluate(self._on, combined, self._idx):
                    return combined
            self._left_row = None  # exhausted right for this left row

    def close(self) -> None:
        self._left.close()
        self._right_rows = []


class IndexNestedLoopJoin(Operator):
    """Inner equijoin where the right side is probed through an index.

    For each left row we read the join key, look it up in the right table's
    index, and fetch the matching right rows from its heap. Cost is O(left x
    log right) instead of O(left x right) — the optimizer chooses this when the
    join is an equijoin on an indexed right column. The ON predicate is exactly
    that equality, so matches need no further filtering here.
    """

    def __init__(
        self,
        left: Operator,
        right_heap,
        right_index,
        left_key: str,
        right_column_names: list[str],
        right_types,
    ) -> None:
        self._left = left
        self._right_heap = right_heap
        self._right_index = right_index
        self._left_key = left_key  # qualified name within left.column_names
        self._right_types = right_types
        self.column_names = list(left.column_names) + list(right_column_names)
        self._left_idx = column_index(left.column_names)
        self._left_row: Optional[Row] = None
        self._pending: Iterator = iter(())

    def open(self) -> None:
        self._left.open()
        self._left_row = None
        self._pending = iter(())

    def next(self) -> Optional[Row]:
        while True:
            for rid in self._pending:
                record = self._right_heap.get(rid)
                if record is not None:
                    assert self._left_row is not None
                    return self._left_row + deserialize_row(self._right_types, record)
            self._left_row = self._left.next()
            if self._left_row is None:
                return None
            key = self._left_row[self._left_idx[self._left_key]]
            self._pending = iter(self._right_index.search(key))

    def close(self) -> None:
        self._left.close()
        self._pending = iter(())
