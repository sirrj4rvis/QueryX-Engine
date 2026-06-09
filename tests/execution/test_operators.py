"""Phase 5, slice 2 — volcano operator tests.

Each operator is tested in isolation over a real heap file, then composed into
small trees (the way the planner will assemble them). Covers SeqScan, IndexScan,
Filter (with AND/OR/NOT precedence via the parser), Projection, Sort (multi-key,
mixed direction), Limit, Distinct, and scalar Aggregate.
"""

import pytest

from queryx.execution.operators import (
    Aggregate, Distinct, Filter, IndexScan, Limit, Projection, SeqScan, Sort, evaluate,
)
from queryx.index.btree import BPlusTree
from queryx.sql import ast
from queryx.sql.parser import parse
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import HeapFile
from queryx.storage.page import ColumnType, serialize_row
from queryx.storage.pager import Pager

COLUMNS = ["id", "name", "age"]
TYPES = [ColumnType.INT, ColumnType.TEXT, ColumnType.INT]
ROWS = [
    (1, "alice", 30),
    (2, "bob", 25),
    (3, "carol", 30),
    (4, "dave", 40),
    (5, "erin", 25),
]


@pytest.fixture
def heap(tmp_path):
    pager = Pager(str(tmp_path / "t.qx"))
    h = HeapFile(BufferPool(pager, capacity=16))
    for row in ROWS:
        h.insert(serialize_row(TYPES, row))
    yield h
    pager.close()


def scan(heap):
    return SeqScan(heap, COLUMNS, TYPES)


def where(sql_predicate):
    """Parse just a WHERE predicate via a throwaway SELECT."""
    return parse(f"SELECT * FROM t WHERE {sql_predicate}").where


# ---------------------------------------------------------------------------
# Expression evaluation
# ---------------------------------------------------------------------------


def test_evaluate_comparison_and_logic():
    index_of = {name: i for i, name in enumerate(COLUMNS)}
    row = (1, "alice", 30)
    assert evaluate(where("age >= 30"), row, index_of) is True
    assert evaluate(where("age > 30"), row, index_of) is False
    assert evaluate(where("age = 30 AND name = 'alice'"), row, index_of) is True
    assert evaluate(where("age = 99 OR name = 'alice'"), row, index_of) is True
    assert evaluate(where("NOT age = 30"), row, index_of) is False


# ---------------------------------------------------------------------------
# SeqScan
# ---------------------------------------------------------------------------


def test_seqscan_yields_all_rows(heap):
    assert sorted(scan(heap)) == sorted(ROWS)


def test_seqscan_reusable_after_close(heap):
    s = scan(heap)
    first = list(s)
    second = list(s)
    assert sorted(first) == sorted(second) == sorted(ROWS)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def test_filter_simple(heap):
    result = list(Filter(scan(heap), where("age = 30")))
    assert sorted(result) == [(1, "alice", 30), (3, "carol", 30)]


def test_filter_and(heap):
    result = list(Filter(scan(heap), where("age = 25 AND name = 'bob'")))
    assert result == [(2, "bob", 25)]


def test_filter_or(heap):
    result = list(Filter(scan(heap), where("age = 40 OR id = 1")))
    assert sorted(result) == [(1, "alice", 30), (4, "dave", 40)]


def test_filter_not_and_range(heap):
    result = list(Filter(scan(heap), where("NOT age = 25 AND age < 40")))
    assert sorted(result) == [(1, "alice", 30), (3, "carol", 30)]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_projection_picks_and_orders_columns(heap):
    proj = Projection(scan(heap), ["name", "id"])
    assert proj.column_names == ["name", "id"]
    assert sorted(proj) == sorted([(r[1], r[0]) for r in ROWS])


def test_projection_over_filter(heap):
    plan = Projection(Filter(scan(heap), where("age = 30")), ["name"])
    assert sorted(plan) == [("alice",), ("carol",)]


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_sort_ascending(heap):
    plan = Sort(scan(heap), [ast.OrderItem("age", descending=False)])
    ages = [r[2] for r in plan]
    assert ages == sorted(ages)


def test_sort_descending(heap):
    plan = Sort(scan(heap), [ast.OrderItem("id", descending=True)])
    ids = [r[0] for r in plan]
    assert ids == [5, 4, 3, 2, 1]


def test_sort_multi_key_mixed_direction(heap):
    # age ASC, then id DESC within equal ages
    plan = Sort(scan(heap), [ast.OrderItem("age", False), ast.OrderItem("id", True)])
    result = [(r[2], r[0]) for r in plan]
    assert result == [(25, 5), (25, 2), (30, 3), (30, 1), (40, 4)]


# ---------------------------------------------------------------------------
# Limit / Distinct
# ---------------------------------------------------------------------------


def test_limit_stops_early(heap):
    plan = Limit(Sort(scan(heap), [ast.OrderItem("id")]), 2)
    assert [r[0] for r in plan] == [1, 2]


def test_distinct_removes_duplicate_rows(heap):
    plan = Distinct(Projection(scan(heap), ["age"]))
    assert sorted(plan) == [(25,), (30,), (40,)]


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def aggs(*sqls):
    return [parse(f"SELECT {s} FROM t").projections[0] for s in sqls]


def test_aggregate_count_star(heap):
    plan = Aggregate(scan(heap), aggs("COUNT(*)"))
    assert plan.column_names == ["COUNT(*)"]
    assert list(plan) == [(5,)]


def test_aggregate_sum_avg_min_max(heap):
    plan = Aggregate(scan(heap), aggs("SUM(age)", "AVG(age)", "MIN(age)", "MAX(age)"))
    total = 30 + 25 + 30 + 40 + 25
    assert list(plan) == [(total, total / 5, 25, 40)]


def test_aggregate_over_filter(heap):
    plan = Aggregate(Filter(scan(heap), where("age = 30")), aggs("COUNT(*)", "MIN(id)"))
    assert list(plan) == [(2, 1)]


def test_aggregate_empty_input(heap):
    plan = Aggregate(Filter(scan(heap), where("age = 999")), aggs("COUNT(*)", "SUM(age)", "MAX(age)"))
    assert list(plan) == [(0, None, None)]


# ---------------------------------------------------------------------------
# IndexScan
# ---------------------------------------------------------------------------


def test_index_scan_fetches_by_rowid(tmp_path):
    # Build a heap and a B+ tree index on `age`, then scan via the index.
    heap_pager = Pager(str(tmp_path / "h.qx"))
    heap = HeapFile(BufferPool(heap_pager, capacity=16))
    idx_pager = Pager(str(tmp_path / "i.qx"))
    index = BPlusTree(BufferPool(idx_pager, capacity=16), max_keys=4)

    for row in ROWS:
        rid = heap.insert(serialize_row(TYPES, row))
        index.insert(row[2], rid)  # key on age

    try:
        rowids = index.search(30)  # ages == 30
        plan = IndexScan(heap, rowids, COLUMNS, TYPES)
        assert sorted(plan) == [(1, "alice", 30), (3, "carol", 30)]

        # range lookup feeds IndexScan just as well
        rids = [rid for _k, rid in index.range_scan(25, 30)]
        plan2 = IndexScan(heap, rids, COLUMNS, TYPES)
        assert {r[2] for r in plan2} == {25, 30}
    finally:
        heap_pager.close()
        idx_pager.close()
