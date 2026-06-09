"""Phase 4, slice 3 — parser tests.

Prove every supported statement parses into the right AST, that WHERE-predicate
operator precedence (OR < AND < NOT < comparison) is correct, and that malformed
input raises a positioned SQLSyntaxError.
"""

import pytest

from queryx.sql import ast
from queryx.sql.parser import parse
from queryx.sql.tokens import SQLSyntaxError, TokenType
from queryx.storage.page import ColumnType


# ---------------------------------------------------------------------------
# CREATE TABLE / DROP TABLE
# ---------------------------------------------------------------------------


def test_create_table():
    stmt = parse("CREATE TABLE users (id INT, name TEXT, age INTEGER)")
    assert stmt == ast.CreateTable(
        table="users",
        columns=[
            ast.ColumnDef("id", ColumnType.INT),
            ast.ColumnDef("name", ColumnType.TEXT),
            ast.ColumnDef("age", ColumnType.INT),
        ],
    )


def test_create_table_requires_a_type():
    with pytest.raises(SQLSyntaxError):
        parse("CREATE TABLE t (id)")


def test_drop_table():
    assert parse("DROP TABLE users") == ast.DropTable(table="users")


# ---------------------------------------------------------------------------
# INSERT
# ---------------------------------------------------------------------------


def test_insert_positional():
    stmt = parse("INSERT INTO users VALUES (1, 'alice', 30)")
    assert stmt == ast.Insert(
        table="users",
        columns=None,
        rows=[[
            ast.Literal(1, ColumnType.INT),
            ast.Literal("alice", ColumnType.TEXT),
            ast.Literal(30, ColumnType.INT),
        ]],
    )


def test_insert_with_column_list():
    stmt = parse("INSERT INTO users (id, name) VALUES (2, 'bob')")
    assert stmt.columns == ["id", "name"]
    assert stmt.rows == [[ast.Literal(2, ColumnType.INT), ast.Literal("bob", ColumnType.TEXT)]]


def test_insert_negative_number():
    stmt = parse("INSERT INTO t VALUES (-5)")
    assert stmt.rows == [[ast.Literal(-5, ColumnType.INT)]]


def test_insert_multiple_rows():
    stmt = parse("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    assert len(stmt.rows) == 3
    assert stmt.rows[0] == [ast.Literal(1, ColumnType.INT), ast.Literal("a", ColumnType.TEXT)]
    assert stmt.rows[2] == [ast.Literal(3, ColumnType.INT), ast.Literal("c", ColumnType.TEXT)]


# ---------------------------------------------------------------------------
# SELECT
# ---------------------------------------------------------------------------


def test_select_star():
    stmt = parse("SELECT * FROM users")
    assert isinstance(stmt, ast.Select)
    assert stmt.projections == [ast.Star()]
    assert stmt.table == "users"
    assert stmt.distinct is False
    assert stmt.where is None


def test_select_columns():
    stmt = parse("SELECT id, name FROM users")
    assert stmt.projections == [ast.Column("id"), ast.Column("name")]


def test_select_distinct():
    stmt = parse("SELECT DISTINCT city FROM users")
    assert stmt.distinct is True
    assert stmt.projections == [ast.Column("city")]


def test_select_with_where():
    stmt = parse("SELECT name FROM users WHERE age >= 30")
    assert stmt.where == ast.Comparison(TokenType.GTE, ast.Column("age"), ast.Literal(30, ColumnType.INT))


def test_select_order_by_and_limit():
    stmt = parse("SELECT name FROM users ORDER BY age DESC, name LIMIT 10")
    assert stmt.order_by == [
        ast.OrderItem("age", descending=True),
        ast.OrderItem("name", descending=False),
    ]
    assert stmt.limit == 10


def test_select_aggregates():
    stmt = parse("SELECT COUNT(*), AVG(age), MAX(salary) FROM emp")
    assert stmt.projections == [
        ast.Aggregate("COUNT", ast.Star()),
        ast.Aggregate("AVG", ast.Column("age")),
        ast.Aggregate("MAX", ast.Column("salary")),
    ]


# ---------------------------------------------------------------------------
# WHERE predicate precedence
# ---------------------------------------------------------------------------


def test_and_binds_tighter_than_or():
    # a = 1 OR b = 2 AND c = 3  ->  a=1 OR (b=2 AND c=3)
    where = parse("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3").where
    assert isinstance(where, ast.Or)
    assert isinstance(where.left, ast.Comparison)   # a = 1
    assert isinstance(where.right, ast.And)          # (b=2 AND c=3)


def test_not_binds_tighter_than_and():
    # NOT a = 1 AND b = 2  ->  (NOT a=1) AND b=2
    where = parse("SELECT * FROM t WHERE NOT a = 1 AND b = 2").where
    assert isinstance(where, ast.And)
    assert isinstance(where.left, ast.Not)
    assert isinstance(where.right, ast.Comparison)


def test_parentheses_override_precedence():
    # (a = 1 OR b = 2) AND c = 3  ->  And(Or(...), c=3)
    where = parse("SELECT * FROM t WHERE (a = 1 OR b = 2) AND c = 3").where
    assert isinstance(where, ast.And)
    assert isinstance(where.left, ast.Or)


def test_all_comparison_operators():
    ops = {
        "=": TokenType.EQ, "!=": TokenType.NEQ, "<>": TokenType.NEQ,
        "<": TokenType.LT, ">": TokenType.GT, "<=": TokenType.LTE, ">=": TokenType.GTE,
    }
    for symbol, tok in ops.items():
        where = parse(f"SELECT * FROM t WHERE a {symbol} 1").where
        assert isinstance(where, ast.Comparison)
        assert where.op == tok


# ---------------------------------------------------------------------------
# UPDATE / DELETE
# ---------------------------------------------------------------------------


def test_update():
    stmt = parse("UPDATE users SET age = 31, name = 'al' WHERE id = 1")
    assert isinstance(stmt, ast.Update)
    assert stmt.assignments == [
        ast.Assignment("age", ast.Literal(31, ColumnType.INT)),
        ast.Assignment("name", ast.Literal("al", ColumnType.TEXT)),
    ]
    assert stmt.where == ast.Comparison(TokenType.EQ, ast.Column("id"), ast.Literal(1, ColumnType.INT))


def test_delete_with_and_without_where():
    assert parse("DELETE FROM users") == ast.Delete(table="users", where=None)
    stmt = parse("DELETE FROM users WHERE age < 18")
    assert stmt.where == ast.Comparison(TokenType.LT, ast.Column("age"), ast.Literal(18, ColumnType.INT))


# ---------------------------------------------------------------------------
# CREATE INDEX / DROP INDEX
# ---------------------------------------------------------------------------


def test_create_index():
    assert parse("CREATE INDEX idx_age ON users (age)") == ast.CreateIndex(
        name="idx_age", table="users", column="age"
    )


def test_drop_index():
    assert parse("DROP INDEX idx_age") == ast.DropIndex(name="idx_age")


# ---------------------------------------------------------------------------
# Misc / errors
# ---------------------------------------------------------------------------


def test_trailing_semicolon_allowed():
    assert parse("SELECT * FROM t;") is not None


def test_trailing_tokens_rejected():
    with pytest.raises(SQLSyntaxError):
        parse("SELECT * FROM t extra junk")


def test_empty_input_rejected():
    with pytest.raises(SQLSyntaxError):
        parse("")


def test_missing_from_rejected():
    with pytest.raises(SQLSyntaxError):
        parse("SELECT name users")


def test_unclosed_paren_rejected():
    with pytest.raises(SQLSyntaxError):
        parse("SELECT * FROM t WHERE (a = 1")
