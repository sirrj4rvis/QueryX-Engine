"""Phase 5, slice 3 — end-to-end database tests.

These exercise the full pipeline through the public facade: SQL string -> parse
-> plan -> execute against real storage and indexes. This is the Phase 5
deliverable: SELECT returns correct results end to end, and data survives a
restart of the Database.
"""

import pytest

from queryx.catalog import CatalogError
from queryx.database import Database, QueryError
from queryx.sql.tokens import SQLSyntaxError


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "mydb"))
    yield database
    database.close()


def setup_users(db):
    db.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    rows = [
        (1, "alice", 30), (2, "bob", 25), (3, "carol", 30),
        (4, "dave", 40), (5, "erin", 25),
    ]
    for r in rows:
        db.execute(f"INSERT INTO users VALUES ({r[0]}, '{r[1]}', {r[2]})")
    return rows


# ---------------------------------------------------------------------------
# CREATE / INSERT / SELECT basics
# ---------------------------------------------------------------------------


def test_create_insert_select_star(db):
    setup_users(db)
    result = db.execute("SELECT * FROM users")
    assert result.columns == ["id", "name", "age"]
    assert len(result) == 5
    assert (1, "alice", 30) in result.rows


def test_select_columns_projection(db):
    setup_users(db)
    result = db.execute("SELECT name, age FROM users WHERE id = 1")
    assert result.columns == ["name", "age"]
    assert result.rows == [("alice", 30)]


def test_insert_with_column_list(db):
    db.execute("CREATE TABLE t (a INT, b TEXT)")
    db.execute("INSERT INTO t (b, a) VALUES ('x', 7)")  # out of order
    assert db.execute("SELECT * FROM t").rows == [(7, "x")]


def test_insert_count_return(db):
    db.execute("CREATE TABLE t (a INT)")
    assert db.execute("INSERT INTO t VALUES (1)") == 1


def test_insert_multiple_rows(db):
    db.execute("CREATE TABLE t (id INT, name TEXT)")
    n = db.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    assert n == 3  # one statement, three rows
    assert sorted(db.execute("SELECT * FROM t").rows) == [(1, "a"), (2, "b"), (3, "c")]


def test_multi_row_insert_keeps_indexes_in_sync(db):
    db.execute("CREATE TABLE t (id INT, v INT)")
    db.execute("CREATE INDEX idx_id ON t (id)")
    db.execute("INSERT INTO t VALUES (1, 10), (2, 20), (3, 30)")
    assert db.execute("SELECT v FROM t WHERE id = 2").rows == [(20,)]


def test_multi_row_insert_bad_row_rejected_atomically(db):
    db.execute("CREATE TABLE t (a INT)")
    with pytest.raises(QueryError):
        db.execute("INSERT INTO t VALUES (1), ('oops'), (3)")  # middle row wrong type
    assert len(db.execute("SELECT * FROM t")) == 0  # nothing inserted


# ---------------------------------------------------------------------------
# WHERE predicates
# ---------------------------------------------------------------------------


def test_where_and_or_not(db):
    setup_users(db)
    r = db.execute("SELECT id FROM users WHERE age = 30 OR id = 2")
    assert sorted(x[0] for x in r) == [1, 2, 3]
    r = db.execute("SELECT id FROM users WHERE NOT age = 25 AND age < 40")
    assert sorted(x[0] for x in r) == [1, 3]


def test_all_comparison_operators(db):
    setup_users(db)
    assert sorted(x[0] for x in db.execute("SELECT id FROM users WHERE age >= 30")) == [1, 3, 4]
    assert sorted(x[0] for x in db.execute("SELECT id FROM users WHERE age <> 30")) == [2, 4, 5]
    assert sorted(x[0] for x in db.execute("SELECT id FROM users WHERE age < 30")) == [2, 5]


# ---------------------------------------------------------------------------
# ORDER BY / LIMIT / DISTINCT
# ---------------------------------------------------------------------------


def test_order_by_and_limit(db):
    setup_users(db)
    r = db.execute("SELECT id FROM users ORDER BY age DESC, id ASC LIMIT 3")
    assert [x[0] for x in r.rows] == [4, 1, 3]


def test_distinct(db):
    setup_users(db)
    r = db.execute("SELECT DISTINCT age FROM users ORDER BY age")
    assert r.rows == [(25,), (30,), (40,)]


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def test_aggregates(db):
    setup_users(db)
    r = db.execute("SELECT COUNT(*), MIN(age), MAX(age), SUM(age) FROM users")
    assert r.columns == ["COUNT(*)", "MIN(age)", "MAX(age)", "SUM(age)"]
    assert r.rows == [(5, 25, 40, 150)]


def test_aggregate_with_where(db):
    setup_users(db)
    r = db.execute("SELECT COUNT(*), AVG(age) FROM users WHERE age = 30")
    assert r.rows == [(2, 30.0)]


def test_aggregate_mixed_with_column_rejected(db):
    setup_users(db)
    with pytest.raises(QueryError):
        db.execute("SELECT COUNT(*), name FROM users")


# ---------------------------------------------------------------------------
# UPDATE / DELETE
# ---------------------------------------------------------------------------


def test_update(db):
    setup_users(db)
    n = db.execute("UPDATE users SET age = 31 WHERE name = 'alice'")
    assert n == 1
    assert db.execute("SELECT age FROM users WHERE id = 1").rows == [(31,)]


def test_update_all_rows(db):
    setup_users(db)
    n = db.execute("UPDATE users SET age = 0")
    assert n == 5
    assert {r[0] for r in db.execute("SELECT age FROM users")} == {0}


def test_delete(db):
    setup_users(db)
    n = db.execute("DELETE FROM users WHERE age = 25")
    assert n == 2
    assert sorted(x[0] for x in db.execute("SELECT id FROM users")) == [1, 3, 4]


def test_delete_all(db):
    setup_users(db)
    assert db.execute("DELETE FROM users") == 5
    assert len(db.execute("SELECT * FROM users")) == 0


# ---------------------------------------------------------------------------
# Indexes (created, populated, and kept in sync; correctness via SELECT)
# ---------------------------------------------------------------------------


def test_create_index_then_queries_still_correct(db):
    setup_users(db)
    db.execute("CREATE INDEX idx_age ON users (age)")
    # Inserts and updates must keep the index in sync; results stay correct.
    db.execute("INSERT INTO users VALUES (6, 'fay', 30)")
    db.execute("UPDATE users SET age = 99 WHERE id = 2")
    r = db.execute("SELECT id FROM users WHERE age = 30")
    assert sorted(x[0] for x in r) == [1, 3, 6]
    assert db.catalog.has_index("idx_age")


def test_create_index_on_text_column_rejected(db):
    setup_users(db)
    with pytest.raises(QueryError):
        db.execute("CREATE INDEX idx_name ON users (name)")


def test_drop_index(db):
    setup_users(db)
    db.execute("CREATE INDEX idx_age ON users (age)")
    db.execute("DROP INDEX idx_age")
    assert not db.catalog.has_index("idx_age")


# ---------------------------------------------------------------------------
# DROP TABLE / error handling
# ---------------------------------------------------------------------------


def test_drop_table(db):
    setup_users(db)
    db.execute("DROP TABLE users")
    assert not db.catalog.has_table("users")
    with pytest.raises(CatalogError):
        db.execute("SELECT * FROM users")


def test_unknown_column_rejected(db):
    setup_users(db)
    with pytest.raises(CatalogError):
        db.execute("SELECT * FROM users WHERE ghost = 1")


def test_type_mismatch_rejected(db):
    db.execute("CREATE TABLE t (a INT)")
    with pytest.raises(QueryError):
        db.execute("INSERT INTO t VALUES ('not a number')")


def test_wrong_arity_rejected(db):
    db.execute("CREATE TABLE t (a INT, b TEXT)")
    with pytest.raises(QueryError):
        db.execute("INSERT INTO t VALUES (1)")


def test_syntax_error_propagates(db):
    with pytest.raises(SQLSyntaxError):
        db.execute("SELECT FROM")


# ---------------------------------------------------------------------------
# Persistence across a full restart
# ---------------------------------------------------------------------------


def test_database_survives_restart(tmp_path):
    path = str(tmp_path / "persistdb")
    db1 = Database(path)
    setup_users(db1)
    db1.execute("CREATE INDEX idx_age ON users (age)")
    db1.execute("DELETE FROM users WHERE id = 5")
    db1.close()

    db2 = Database(path)
    try:
        assert db2.catalog.list_tables() == ["users"]
        assert db2.catalog.has_index("idx_age")
        r = db2.execute("SELECT id, name FROM users ORDER BY id")
        assert [row[0] for row in r] == [1, 2, 3, 4]
        assert db2.execute("SELECT COUNT(*) FROM users").rows == [(4,)]
    finally:
        db2.close()
