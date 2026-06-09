"""Tests for the interactive SQL shell (REPL).

The loop is driven by injected read_line/out callables, so no real terminal or
subprocess is needed: feed lines, capture output, assert.
"""

import pytest

from queryx import shell
from queryx.database import Database
from queryx.execution.operators import Row  # noqa: F401  (kept for clarity)


def drive(tmp_path, lines):
    """Run the shell over a list of input lines; return captured output text."""
    db = Database(str(tmp_path / "shelldb"))
    captured: list[str] = []
    it = iter(lines)

    def read_line(_prompt):
        try:
            return next(it)
        except StopIteration:
            return None

    shell.repl(db, read_line, lambda text="": captured.append(text))
    db.close()
    return "\n".join(captured)


# ---------------------------------------------------------------------------
# Statement splitting
# ---------------------------------------------------------------------------


def test_split_multiple_statements():
    stmts, remainder = shell._split_complete("A; B; C")
    assert stmts == ["A", " B"]
    assert remainder == " C"


def test_split_keeps_semicolon_inside_string():
    stmts, remainder = shell._split_complete("INSERT 'a;b'; rest")
    assert stmts == ["INSERT 'a;b'"]
    assert remainder == " rest"


def test_split_handles_escaped_quote():
    stmts, _ = shell._split_complete("'O''Brien;'; x")
    assert stmts == ["'O''Brien;'"]


def test_split_incomplete_is_all_remainder():
    stmts, remainder = shell._split_complete("SELECT * FROM t")
    assert stmts == []
    assert remainder == "SELECT * FROM t"


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------


def test_format_table_aligns_and_shows_null():
    from queryx.database import QueryResult
    table = shell._format_table(QueryResult(columns=["id", "name"], rows=[(1, "alice"), (2, None)]))
    lines = table.splitlines()
    assert lines[0].startswith("id | name")
    assert "NULL" in table


# ---------------------------------------------------------------------------
# End-to-end REPL behavior
# ---------------------------------------------------------------------------


def test_create_insert_select(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT, name TEXT);",
        "INSERT INTO t VALUES (1, 'alice');",
        "SELECT * FROM t;",
    ])
    assert "OK" in out                  # CREATE
    assert "1 row affected" in out      # INSERT
    assert "alice" in out               # SELECT row
    assert "(1 row)" in out


def test_multiline_statement(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT);",
        "INSERT INTO t",      # statement continues on the next line
        "VALUES (42);",
        "SELECT * FROM t;",
    ])
    assert "42" in out


def test_semicolon_inside_string_literal(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT, s TEXT);",
        "INSERT INTO t VALUES (1, 'a;b');",
        "SELECT s FROM t;",
    ])
    assert "a;b" in out


def test_error_does_not_crash_the_shell(tmp_path):
    out = drive(tmp_path, [
        "SELECT * FROM ghost;",   # unknown table -> error, but shell continues
        "CREATE TABLE t (id INT);",
        "SELECT * FROM t;",
    ])
    assert "Error:" in out
    assert "(0 rows)" in out      # the later valid query still ran


def test_explain_is_printed(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT);",
        "INSERT INTO t VALUES (1);",
        "EXPLAIN SELECT * FROM t WHERE id = 1;",
    ])
    assert "SeqScan on t" in out


def test_meta_tables_and_schema(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE users (id INT, name TEXT);",
        ".tables",
        ".schema users",
    ])
    assert "users" in out
    assert "id INT" in out and "name TEXT" in out


def test_meta_quit_stops_processing(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT);",
        ".quit",
        "SELECT * FROM t;",   # must NOT run after .quit
    ])
    assert "SeqScan" not in out
    assert "(0 rows)" not in out


def test_timing_suffix_is_shown(tmp_path):
    out = drive(tmp_path, ["CREATE TABLE t (id INT);", "SELECT * FROM t;"])
    assert "ms]" in out  # per-query timing


def test_meta_stats_shows_internals(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT);",
        "INSERT INTO t VALUES (1), (2), (3);",
        "SELECT * FROM t;",
        ".stats",
    ])
    assert "buffer pool" in out
    assert "hit ratio" in out
    assert "data pages" in out


def test_meta_pages_shows_layout(tmp_path):
    out = drive(tmp_path, [
        "CREATE TABLE t (id INT, name TEXT);",
        "INSERT INTO t VALUES (1, 'a'), (2, 'b');",
        ".pages t",
    ])
    assert "data page" in out
    assert "page | slots | live" in out


def test_meta_pages_requires_table(tmp_path):
    out = drive(tmp_path, [".pages"])
    assert "usage: .pages" in out


def test_recommend_and_apply(tmp_path):
    # advisor default threshold is 5; issue enough equality queries to trigger it
    lines = ["CREATE TABLE t (id INT, v INT);"]
    lines += [f"INSERT INTO t VALUES ({i}, {i});" for i in range(20)]
    lines += [f"SELECT v FROM t WHERE id = {i};" for i in range(6)]
    lines += [".recommend", ".apply", ".indexes"]
    out = drive(tmp_path, lines)
    assert "t.id" in out          # recommended
    assert "auto_t_id" in out     # applied + shown in .indexes
