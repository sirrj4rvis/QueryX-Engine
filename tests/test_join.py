"""Phase 9b — two-table INNER JOIN tests (end to end through the Database).

Covers nested-loop and index-nested-loop joins, qualified column names,
WHERE/projection/ORDER BY/LIMIT over the joined result, that adding an index
does not change results (only the algorithm), and EXPLAIN showing the join.
"""

import pytest

from queryx.database import Database, QueryError


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "joindb"))
    d.execute("CREATE TABLE users (id INT, name TEXT)")
    d.execute("CREATE TABLE orders (id INT, user_id INT, total INT)")
    for i in (1, 2, 3):
        d.execute(f"INSERT INTO users VALUES ({i}, 'user{i}')")
    # user 1 has two orders, user 2 has one, user 3 has none
    d.execute("INSERT INTO orders VALUES (10, 1, 100)")
    d.execute("INSERT INTO orders VALUES (11, 1, 200)")
    d.execute("INSERT INTO orders VALUES (12, 2, 50)")
    yield d
    d.close()


def test_basic_inner_join(db):
    r = db.execute(
        "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
    )
    assert r.columns == ["u.name", "o.total"]
    assert sorted(r.rows) == [("user1", 100), ("user1", 200), ("user2", 50)]
    # user3 has no orders -> excluded (inner join)


def test_join_select_star_combines_both_tables(db):
    r = db.execute("SELECT * FROM users u JOIN orders o ON u.id = o.user_id")
    assert r.columns == ["u.id", "u.name", "o.id", "o.user_id", "o.total"]
    assert len(r.rows) == 3


def test_join_with_where(db):
    r = db.execute(
        "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id "
        "WHERE o.total >= 100"
    )
    assert sorted(r.rows) == [("user1", 100), ("user1", 200)]


def test_join_order_by_and_limit(db):
    r = db.execute(
        "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id "
        "ORDER BY o.total DESC LIMIT 2"
    )
    assert [row[1] for row in r.rows] == [200, 100]


def test_index_nested_loop_matches_nested_loop(db):
    # results must be identical whether or not orders.user_id is indexed
    q = "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
    before = sorted(db.execute(q).rows)
    db.execute("CREATE INDEX idx_uid ON orders (user_id)")
    after = sorted(db.execute(q).rows)
    assert before == after == [("user1", 100), ("user1", 200), ("user2", 50)]


def test_join_works_with_literal_order_in_on(db):
    # ON written with the right table first should still parse and join
    r = db.execute("SELECT u.id FROM users u JOIN orders o ON o.user_id = u.id")
    assert sorted(x[0] for x in r.rows) == [1, 1, 2]


def test_explain_join_uses_index_when_available(db):
    plan_seq = db.execute("EXPLAIN SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id")
    assert "NestedLoopJoin" in plan_seq
    db.execute("CREATE INDEX idx_uid ON orders (user_id)")
    plan_idx = db.execute("EXPLAIN SELECT u.id FROM users u JOIN orders o ON u.id = o.user_id")
    assert "IndexNestedLoopJoin" in plan_idx
    assert "idx_uid" in plan_idx


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unknown_join_column_rejected(db):
    with pytest.raises(QueryError):
        db.execute("SELECT u.ghost FROM users u JOIN orders o ON u.id = o.user_id")


def test_ambiguous_bare_column_rejected(db):
    # `id` exists in both tables, so an unqualified reference is ambiguous
    with pytest.raises(QueryError):
        db.execute("SELECT id FROM users u JOIN orders o ON u.id = o.user_id")


def test_aggregate_with_join_rejected(db):
    with pytest.raises(QueryError):
        db.execute("SELECT COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id")
