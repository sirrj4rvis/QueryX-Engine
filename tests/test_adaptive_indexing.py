"""Phase 9c — adaptive indexing tests.

The advisor watches filtered columns and recommends indexes for hot, unindexed
INT columns: hash for equality-only workloads, B+ tree when ranges appear.
apply_recommendations() then creates them, and they must not change results.
"""

import pytest

from queryx.database import Database


@pytest.fixture
def db(tmp_path):
    # low threshold so a few queries trigger a recommendation
    d = Database(str(tmp_path / "adb"), advisor_min_uses=3)
    d.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    for i in range(50):
        d.execute(f"INSERT INTO users VALUES ({i}, 'u{i}', {i % 10})")
    yield d
    d.close()


def test_no_recommendation_below_threshold(db):
    db.execute("SELECT * FROM users WHERE id = 1")  # only 1 use (< 3)
    assert db.recommend_indexes() == []


def test_recommends_hash_for_equality_workload(db):
    for _ in range(4):
        db.execute("SELECT * FROM users WHERE id = 7")
    recs = db.recommend_indexes()
    assert len(recs) == 1
    assert recs[0].table == "users" and recs[0].column == "id"
    assert recs[0].kind == "hash"  # equality-only -> hash


def test_recommends_btree_when_ranges_seen(db):
    for _ in range(3):
        db.execute("SELECT * FROM users WHERE age >= 5")
    recs = db.recommend_indexes()
    assert any(r.column == "age" and r.kind == "btree" for r in recs)


def test_no_recommendation_for_text_column(db):
    for _ in range(5):
        db.execute("SELECT * FROM users WHERE name = 'u1'")
    # name is TEXT; QueryX indexes are INT-keyed, so no recommendation
    assert all(r.column != "name" for r in db.recommend_indexes())


def test_no_recommendation_when_already_indexed(db):
    db.execute("CREATE INDEX idx_id ON users (id)")
    for _ in range(5):
        db.execute("SELECT * FROM users WHERE id = 3")
    assert all(r.column != "id" for r in db.recommend_indexes())


def test_apply_recommendations_creates_indexes_and_preserves_results(db):
    for _ in range(4):
        db.execute("SELECT id FROM users WHERE id = 9")
    before = db.execute("SELECT id FROM users WHERE id = 9").rows

    created = db.apply_recommendations()
    assert created == ["auto_users_id"]
    assert db.catalog.has_index("auto_users_id")
    assert db.catalog.get_index("auto_users_id").kind == "hash"

    after = db.execute("SELECT id FROM users WHERE id = 9").rows
    assert before == after == [(9,)]

    # once applied, it is no longer recommended
    assert all(r.column != "id" for r in db.recommend_indexes())


def test_applied_index_is_used_by_optimizer(db):
    # Grow the table past one page so an index can actually beat a SeqScan
    # (on a 1-page table SeqScan is correctly cheaper, so the index would be
    # recommended but not chosen).
    for i in range(50, 1000):
        db.execute(f"INSERT INTO users VALUES ({i}, 'u{i}', {i % 10})")
    for _ in range(4):
        db.execute("SELECT id FROM users WHERE id = 900")
    db.apply_recommendations()
    plan = db.execute("EXPLAIN SELECT id FROM users WHERE id = 900")
    assert "IndexScan using auto_users_id" in plan
