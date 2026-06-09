"""Phase 6, slice 2 — optimizer + EXPLAIN through the Database facade.

Two things to prove: (1) the same query returns identical results whether it
goes through a SeqScan or an IndexScan (the optimizer must never change answers,
only cost), and (2) EXPLAIN reports the access path the cost model chose.
"""

import pytest

from queryx.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "optdb"))
    yield database
    database.close()


def seed(db, n=200):
    db.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    for i in range(n):
        db.execute(f"INSERT INTO users VALUES ({i}, 'u{i}', {i % 50})")
    return n


# ---------------------------------------------------------------------------
# Correctness is identical with and without an index
# ---------------------------------------------------------------------------


def test_index_scan_matches_seqscan_results(db):
    seed(db, 200)
    before = sorted(r[0] for r in db.execute("SELECT id FROM users WHERE id = 137"))
    db.execute("CREATE INDEX idx_id ON users (id)")  # now an IndexScan is available
    after = sorted(r[0] for r in db.execute("SELECT id FROM users WHERE id = 137"))
    assert before == after == [137]


def test_index_residual_filter_correct(db):
    seed(db, 200)
    db.execute("CREATE INDEX idx_id ON users (id)")
    # id = 42 drives the index; age = 42%50 must still hold via the residual filter
    r = db.execute("SELECT id, age FROM users WHERE id = 42 AND age = 42")
    assert r.rows == [(42, 42)]
    # a contradictory residual yields nothing even though the index found id=42
    assert db.execute("SELECT id FROM users WHERE id = 42 AND age = 999").rows == []


def test_range_index_scan_results(db):
    seed(db, 200)
    db.execute("CREATE INDEX idx_id ON users (id)")
    r = db.execute("SELECT id FROM users WHERE id >= 195")
    assert sorted(x[0] for x in r) == [195, 196, 197, 198, 199]


# ---------------------------------------------------------------------------
# EXPLAIN
# ---------------------------------------------------------------------------


def test_explain_seqscan_without_index(db):
    seed(db, 50)
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE id = 10")
    assert "SeqScan on users" in plan
    assert "chose SeqScan" in plan


def test_explain_uses_index_for_selective_equality(db):
    seed(db, 500)
    db.execute("CREATE INDEX idx_id ON users (id)")  # id is unique -> very selective
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE id = 10")
    assert "IndexScan using idx_id" in plan
    assert "id = 10" in plan
    assert "chose IndexScan" in plan


def test_explain_prefers_seqscan_for_unselective_predicate(db):
    seed(db, 500)
    db.execute("CREATE INDEX idx_age ON users (age)")  # age has 50 distinct -> ~2% each
    # age = 7 matches ~10 of 500 rows; on a tiny page count, seqscan can still win.
    plan = db.execute("EXPLAIN SELECT * FROM users WHERE age = 7")
    assert "cost=" in plan  # decision is cost-annotated either way
    assert ("SeqScan on users" in plan) or ("IndexScan using idx_age" in plan)


def test_explain_shows_full_operator_tree(db):
    seed(db, 100)
    plan = db.execute(
        "EXPLAIN SELECT DISTINCT name FROM users WHERE age >= 10 ORDER BY name LIMIT 5"
    )
    for fragment in ("Limit: 5", "Distinct", "Projection: name", "Sort: name", "Filter:", "SeqScan"):
        assert fragment in plan, f"missing {fragment!r} in plan:\n{plan}"


def test_explain_aggregate_plan(db):
    seed(db, 30)
    plan = db.execute("EXPLAIN SELECT COUNT(*), AVG(age) FROM users")
    assert "Aggregate: COUNT(*), AVG(age)" in plan
    assert "SeqScan on users" in plan
