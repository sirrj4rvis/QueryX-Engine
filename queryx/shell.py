"""Interactive SQL shell (REPL) for QueryX.

Run:
    python -m queryx [DB_DIR]

Type SQL terminated by ';' (statements may span multiple lines). Results print
as a formatted table with a per-query timing; INSERT/UPDATE/DELETE report
affected rows; EXPLAIN prints the plan. Lines starting with '.' are
meta-commands (see .help) — including .stats and .pages, which expose the
engine's internals (buffer-pool hit ratio, on-disk page layout). Ctrl-D (EOF)
or .quit exits, flushing and checkpointing the database on the way out.

The core loop is factored as repl(db, read_line, out, color) so it can be driven
by a real terminal (main) or by test streams. Colour is emitted only on a TTY
(disabled under tests and when NO_COLOR is set), so captured output stays plain.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

from queryx import __version__
from queryx.catalog import CatalogError
from queryx.database import Database, QueryError, QueryResult
from queryx.sql.tokens import SQLSyntaxError


class _C:
    """ANSI colour codes (stdlib only — no third-party dependency)."""
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    CYAN = "\x1b[36m"


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_C.RESET}" if color else text


BANNER = (
    "=" * 60 + "\n"
    f"  QueryX {__version__}  -  a relational database engine, from scratch\n"
    + "=" * 60 + "\n"
    "Type SQL ending with ';'.  .help for commands,  .quit to exit."
)

HELP = """Meta-commands:
  .help              show this help
  .tables            list tables
  .indexes           list indexes
  .schema [table]    show columns (all tables, or one) and row counts
  .stats             buffer-pool hit ratio, page counts, WAL size
  .pages <table>     on-disk page layout (slots, live rows, fill %)
  .recommend         indexes the workload advisor suggests
  .apply             create the recommended indexes
  .quit / .exit      leave the shell

SQL examples:
  CREATE TABLE users (id INT, name TEXT, age INT);
  INSERT INTO users VALUES (1, 'alice', 30), (2, 'bob', 25);
  SELECT name FROM users WHERE age >= 30 ORDER BY name;
  SELECT age, COUNT(*) FROM users GROUP BY age HAVING COUNT(*) > 1;
  SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id;
  EXPLAIN SELECT name FROM users WHERE id = 1;"""


def _split_complete(buffer: str) -> tuple[list[str], str]:
    """Split a buffer into complete statements (ended by a top-level ';') and a
    trailing remainder. A ';' inside a single-quoted string ('' = escaped quote)
    does NOT terminate a statement."""
    statements: list[str] = []
    start = 0
    i = 0
    in_string = False
    n = len(buffer)
    while i < n:
        ch = buffer[i]
        if in_string:
            if ch == "'":
                if i + 1 < n and buffer[i + 1] == "'":  # escaped quote
                    i += 2
                    continue
                in_string = False
        elif ch == "'":
            in_string = True
        elif ch == ";":
            statements.append(buffer[start:i])
            start = i + 1
        i += 1
    return statements, buffer[start:]


def _format_table(result: QueryResult, color: bool = False) -> str:
    """Render a QueryResult as an aligned text table (NULL for None)."""
    columns = result.columns
    rows = [["NULL" if v is None else str(v) for v in row] for row in result.rows]
    widths = [len(c) for c in columns]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    header = _paint(fmt(list(columns)), _C.BOLD + _C.CYAN, color)
    separator = _paint("-+-".join("-" * w for w in widths), _C.DIM, color)
    return "\n".join([header, separator] + [fmt(row) for row in rows])


def _run(db: Database, sql: str, out: Callable[..., None], color: bool = False) -> None:
    """Execute one statement, time it, and print the result; never crashes."""
    start = time.perf_counter()
    try:
        result = db.execute(sql)
    except (SQLSyntaxError, CatalogError, QueryError) as exc:
        out(_paint(f"Error: {exc}", _C.RED, color))
        return
    except Exception as exc:  # never let the shell die on a bad query
        out(_paint(f"Error: {type(exc).__name__}: {exc}", _C.RED, color))
        return
    timing = _paint(f"[{(time.perf_counter() - start) * 1000:.2f} ms]", _C.DIM, color)

    if isinstance(result, QueryResult):
        out(_format_table(result, color))
        n = len(result.rows)
        out(f"({n} row{'' if n == 1 else 's'}) {timing}")
    elif isinstance(result, str):  # EXPLAIN
        out(result)
        out(timing)
    elif isinstance(result, int):  # INSERT / UPDATE / DELETE
        out(f"{result} row{'' if result == 1 else 's'} affected {timing}")
    else:  # DDL returns None
        out(f"{_paint('OK', _C.GREEN, color)} {timing}")


def _meta_stats(db: Database, out: Callable[..., None], color: bool) -> None:
    s = db.runtime_stats()
    out(_paint("buffer pool", _C.BOLD, color))
    out(f"  hit ratio    : {s['buffer_hit_ratio'] * 100:5.1f}%   "
        f"({s['buffer_hits']} hits / {s['buffer_misses']} misses)")
    out(f"  cached pages : {s['buffer_cached_pages']}    dirty: {s['buffer_dirty_pages']}")
    out(_paint("storage", _C.BOLD, color))
    out(f"  data pages   : {s['data_pages']}    index pages: {s['index_pages']}")
    out(f"  WAL bytes    : {s['wal_bytes']}")
    out(f"  open tables  : {s['open_tables']}    open indexes: {s['open_indexes']}")


def _meta_pages(db: Database, table: str, out: Callable[..., None], color: bool) -> None:
    try:
        layout = db.page_layout(table)
    except CatalogError as exc:
        out(_paint(f"Error: {exc}", _C.RED, color))
        return
    out(f"{table}: {len(layout)} data page(s)  (page 0 is the file header)")
    out(_paint("page | slots | live | free B | used", _C.BOLD, color))
    for p in layout:
        out(f"{p['page']:>4} | {p['slots']:>5} | {p['live']:>4} | {p['free']:>6} | {p['used_pct']:>3.0f}%")


def _meta(db: Database, line: str, out: Callable[..., None], color: bool = False) -> bool:
    """Handle a '.' meta-command. Return False to quit the shell."""
    parts = line[1:].split()
    cmd = parts[0].lower() if parts else ""

    if cmd in ("quit", "exit", "q"):
        return False
    if cmd == "help":
        out(HELP)
    elif cmd == "tables":
        tables = db.catalog.list_tables()
        out("\n".join(tables) if tables else "(no tables)")
    elif cmd == "indexes":
        names = db.catalog.list_indexes()
        if not names:
            out("(no indexes)")
        for name in names:
            info = db.catalog.get_index(name)
            out(f"{name}  on {info.table}({info.column})  [{info.kind}]")
    elif cmd == "schema":
        targets = [parts[1]] if len(parts) > 1 else db.catalog.list_tables()
        if not targets:
            out("(no tables)")
        for table in targets:
            try:
                info = db.catalog.get_table(table)
            except CatalogError as exc:
                out(_paint(f"Error: {exc}", _C.RED, color))
                continue
            cols = ", ".join(f"{c.name} {c.type.name}" for c in info.columns)
            out(f"{table} ({cols})  [{info.row_count} rows]")
    elif cmd == "stats":
        _meta_stats(db, out, color)
    elif cmd == "pages":
        if len(parts) < 2:
            out("usage: .pages <table>")
        else:
            _meta_pages(db, parts[1], out, color)
    elif cmd == "recommend":
        recs = db.recommend_indexes()
        if not recs:
            out("(no recommendations)")
        for r in recs:
            out(f"  {r.table}.{r.column}  ->  {r.kind}   ({r.reason})")
    elif cmd == "apply":
        created = db.apply_recommendations()
        out(f"created: {', '.join(created)}" if created else "(nothing to create)")
    else:
        out(f"unknown command '.{cmd}' - try .help")
    return True


def repl(db: Database, read_line: Callable[[str], "str | None"],
         out: Callable[..., None], color: bool = False) -> None:
    """Run the read-eval-print loop. ``read_line(prompt)`` returns a line, or
    None at end of input; ``out(text='')`` prints a line of output."""
    out(BANNER)
    buffer = ""
    while True:
        prompt = "queryx> " if not buffer.strip() else "   ...> "
        line = read_line(prompt)
        if line is None:  # EOF
            out("")
            return
        if not buffer.strip() and line.strip().startswith("."):
            if not _meta(db, line.strip(), out, color):
                return
            continue
        buffer += line + "\n"
        statements, buffer = _split_complete(buffer)
        for statement in statements:
            if statement.strip():
                _run(db, statement, out, color)


def main(argv: "list[str] | None" = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    db_dir = args[0] if args else "queryx_data"
    color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    db = Database(db_dir)
    print(f"Connected to {db_dir!r}.")

    def read_line(prompt: str) -> "str | None":
        try:
            return input(prompt)
        except EOFError:
            return None

    try:
        repl(db, read_line, lambda text="": print(text), color=color)
    except KeyboardInterrupt:
        print("\n(interrupted)")
    finally:
        db.close()
        print("Goodbye.")


if __name__ == "__main__":
    main()
