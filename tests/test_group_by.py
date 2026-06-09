"""Phase 9a — GROUP BY + HAVING tests (end to end through the Database)."""

import pytest

from queryx.database import Database, QueryError


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "gdb"))
    database.execute("CREATE TABLE sales (id INT, region TEXT, amount INT)")
    rows = [
        (1, "north", 100), (2, "north", 200), (3, "south", 50),
        (4, "south", 70), (5, "south", 30), (6, "west", 400),
    ]
    for r in rows:
        database.execute(f"INSERT INTO sales VALUES ({r[0]}, '{r[1]}', {r[2]})")
    yield database
    database.close()


def as_dict(result):
    """{group_key: rest_of_row} for order-independent assertions."""
    return {row[0]: row[1:] for row in result.rows}


def test_group_by_count(db):
    r = db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region")
    assert r.columns == ["region", "COUNT(*)"]
    assert as_dict(r) == {"north": (2,), "south": (3,), "west": (1,)}


def test_group_by_sum_avg_min_max(db):
    r = db.execute(
        "SELECT region, SUM(amount), MIN(amount), MAX(amount) FROM sales GROUP BY region"
    )
    d = as_dict(r)
    assert d["north"] == (300, 100, 200)
    assert d["south"] == (150, 30, 70)
    assert d["west"] == (400, 400, 400)


def test_group_by_with_where_filters_before_grouping(db):
    r = db.execute("SELECT region, COUNT(*) FROM sales WHERE amount >= 70 GROUP BY region")
    assert as_dict(r) == {"north": (2,), "south": (1,), "west": (1,)}


def test_having_filters_groups(db):
    r = db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region HAVING COUNT(*) >= 2")
    assert as_dict(r) == {"north": (2,), "south": (3,)}


def test_having_on_aggregate_value(db):
    r = db.execute("SELECT region, SUM(amount) FROM sales GROUP BY region HAVING SUM(amount) > 200")
    assert as_dict(r) == {"north": (300,), "west": (400,)}


def test_having_on_group_column(db):
    r = db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region HAVING region <> 'south'")
    assert set(as_dict(r)) == {"north", "west"}


def test_group_by_order_by_and_limit(db):
    r = db.execute("SELECT region, SUM(amount) FROM sales GROUP BY region ORDER BY region DESC LIMIT 2")
    assert [row[0] for row in r.rows] == ["west", "south"]


def test_group_by_avg_is_float(db):
    r = db.execute("SELECT region, AVG(amount) FROM sales GROUP BY region HAVING region = 'south'")
    assert r.rows == [("south", 150 / 3)]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_non_grouped_column_rejected(db):
    # selecting a bare column not in GROUP BY is illegal
    with pytest.raises(QueryError):
        db.execute("SELECT region, id FROM sales GROUP BY region")


def test_star_with_group_by_rejected(db):
    with pytest.raises(QueryError):
        db.execute("SELECT * FROM sales GROUP BY region")


def test_having_on_non_group_column_rejected(db):
    with pytest.raises(QueryError):
        db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region HAVING amount > 5")


def test_order_by_unselected_column_rejected(db):
    with pytest.raises(QueryError):
        db.execute("SELECT region, COUNT(*) FROM sales GROUP BY region ORDER BY amount")
