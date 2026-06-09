"""Phase 6, slice 1 — statistics and optimizer unit tests (pure logic).

No storage involved: these feed predicates and stats to the cost model and
assert which access path it chooses and why. Covers selectivity estimation,
sargable-comparison extraction (incl. flipping `5 < age`), the SeqScan-vs-
IndexScan decision, the selective/unselective crossover, and hash-vs-range.
"""

import pytest

from queryx.catalog import IndexInfo
from queryx.planner.optimizer import (
    AccessPath, choose_access_path, normalize_comparison, sargable_comparisons,
)
from queryx.planner.statistics import (
    TableStats, comparison_selectivity, predicate_selectivity,
)
from queryx.sql.parser import parse
from queryx.sql.tokens import TokenType


def where(predicate: str):
    return parse(f"SELECT * FROM t WHERE {predicate}").where


def btree(column, n_distinct=None):
    return IndexInfo(name=f"idx_{column}", table="t", column=column, kind="btree", n_distinct=n_distinct)


def hash_idx(column, n_distinct=None):
    return IndexInfo(name=f"h_{column}", table="t", column=column, kind="hash", n_distinct=n_distinct)


# ---------------------------------------------------------------------------
# Selectivity
# ---------------------------------------------------------------------------


def test_equality_selectivity_uses_n_distinct():
    assert comparison_selectivity(TokenType.EQ, n_distinct=100) == pytest.approx(0.01)
    assert comparison_selectivity(TokenType.EQ, n_distinct=None) == pytest.approx(0.1)


def test_range_and_neq_selectivity():
    assert comparison_selectivity(TokenType.LT) == pytest.approx(1 / 3)
    assert comparison_selectivity(TokenType.NEQ, n_distinct=10) == pytest.approx(0.9)


def test_predicate_selectivity_and_or_not():
    nd = {"a": 100, "b": 100}
    # AND multiplies: 0.01 * 0.01
    assert predicate_selectivity(where("a = 1 AND b = 2"), nd) == pytest.approx(0.0001)
    # OR inclusion-exclusion: 0.01 + 0.01 - 0.0001
    assert predicate_selectivity(where("a = 1 OR b = 2"), nd) == pytest.approx(0.0199)
    # NOT complements
    assert predicate_selectivity(where("NOT a = 1"), nd) == pytest.approx(0.99)


def test_no_predicate_selectivity_is_one():
    assert predicate_selectivity(None, {}) == 1.0


# ---------------------------------------------------------------------------
# Sargable extraction
# ---------------------------------------------------------------------------


def test_normalize_flips_literal_on_left():
    assert normalize_comparison(where("5 < age")) == ("age", TokenType.GT, 5)
    assert normalize_comparison(where("age >= 30")) == ("age", TokenType.GTE, 30)


def test_sargable_from_and_chain():
    got = sargable_comparisons(where("a = 1 AND b > 2 AND c = 3"))
    assert ("a", TokenType.EQ, 1) in got
    assert ("b", TokenType.GT, 2) in got
    assert ("c", TokenType.EQ, 3) in got


def test_or_and_not_are_not_sargable():
    assert sargable_comparisons(where("a = 1 OR b = 2")) == []
    assert sargable_comparisons(where("NOT a = 1")) == []


# ---------------------------------------------------------------------------
# Access-path choice
# ---------------------------------------------------------------------------


def test_no_index_forces_seqscan():
    stats = TableStats(row_count=10_000, num_data_pages=100)
    ap = choose_access_path(where("age = 30"), stats, indexes=[], n_distinct_by_column={})
    assert ap.method == "SeqScan"


def test_selective_equality_chooses_index():
    # Unique-ish column: n_distinct == row_count -> ~1 matching row -> index wins.
    stats = TableStats(row_count=10_000, num_data_pages=100)
    idx = btree("id", n_distinct=10_000)
    ap = choose_access_path(where("id = 42"), stats, [idx], {"id": 10_000})
    assert ap.method == "IndexScan"
    assert ap.index_name == "idx_id"
    assert ap.op == TokenType.EQ and ap.value == 42
    assert ap.est_cost < ap.seqscan_cost


def test_unselective_equality_chooses_seqscan():
    # Low-cardinality column: 0.1 selectivity -> 1000 matches >> 100 pages -> seqscan.
    stats = TableStats(row_count=10_000, num_data_pages=100)
    idx = btree("status", n_distinct=10)
    ap = choose_access_path(where("status = 1"), stats, [idx], {"status": 10})
    assert ap.method == "SeqScan"


def test_range_uses_btree_but_not_hash():
    stats = TableStats(row_count=10_000, num_data_pages=5_000)  # big table -> index attractive
    bt = choose_access_path(where("age > 9990"), stats, [btree("age", 10_000)], {"age": 10_000})
    assert bt.method == "IndexScan" and bt.op == TokenType.GT
    # A hash index cannot serve a range, so the same query falls back to SeqScan.
    h = choose_access_path(where("age > 9990"), stats, [hash_idx("age", 10_000)], {"age": 10_000})
    assert h.method == "SeqScan"


def test_residual_filter_for_extra_conjuncts():
    stats = TableStats(row_count=10_000, num_data_pages=100)
    idx = btree("id", n_distinct=10_000)
    ap = choose_access_path(where("id = 42 AND name = 'x'"), stats, [idx], {"id": 10_000})
    assert ap.method == "IndexScan"
    assert ap.residual is not None  # name = 'x' must still be checked


def test_no_residual_when_predicate_is_just_the_indexed_comparison():
    stats = TableStats(row_count=10_000, num_data_pages=100)
    idx = btree("id", n_distinct=10_000)
    ap = choose_access_path(where("id = 42"), stats, [idx], {"id": 10_000})
    assert ap.residual is None  # the index lookup fully satisfies the predicate
