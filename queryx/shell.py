"""Interactive SQL shell (REPL) for QueryX.

Run:
    python -m queryx [DB_DIR]

Type SQL terminated by ';' (statements may span multiple lines). Results print
as a formatted table; INSERT/UPDATE/DELETE report affected rows; EXPLAIN prints
the plan. Lines starting with '.' are meta-commands (see .help). Ctrl-D (EOF) or
.quit exits, flushing and checkpointing the database on the way out.

The core loop is factored as repl(db, read_line, out) so it can be driven by a
real terminal (main) or by test streams.
"""

from __future__ import annotations

import sys
from typing import Callable

from queryx.catalog import CatalogError
from queryx.database import Database, QueryError, QueryResult
from queryx.sql.tokens import SQLSyntaxError

BANNER = (
    "QueryX interactive shell.\n"
    "Enter SQL terminated by ';'. Meta-commands start with '.' (try .help). "
    ".quit to exit."
)

HELP = """Meta-commands:
  .help              show this help
  .tables            list tables
  .indexes           list indexes
  .schema [table]    show columns (all tables, or one) and row counts
  .recommend         show indexes the workload advisor suggests
  .apply             create the recommended indexes
  .quit / .exit      leave the shell

SQL examples:
  CREATE TABLE users (id INT, name TEXT, age INT);
  INSERT INTO users VALUES (1, 'alice', 30);
  SELECT name FROM users WHERE age >= 30 ORDER BY name;
  SELECT age, COUNT(*) FROM users GROUP BY age HAVING COUNT(*) > 1;
  CREATE INDEX idx_id ON users (id);
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


def _format_table(result: QueryResult) -> str:
    """Render a QueryResult as an aligned text table (NULL for None)."""
    columns = result.columns
    rows = [["NULL" if v is None else str(v) for v in row] for row in result.rows]
    widths = [len(c) for c in columns]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: list[str]) -> str:
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))

    lines = [fmt(list(columns)), "-+-".join("-" * w for w in widths)]
    lines += [fmt(row) for row in rows]
    return "\n".join(lines)


def _run(db: Database, sql: str, out: Callable[..., None]) -> None:
    """Execute one statement and print its result, catching query errors."""
    try:
        result = db.execute(sql)
    except (SQLSyntaxError, CatalogError, QueryError) as exc:
        out(f"Error: {exc}")
        return
    except Exception as exc:  # never let the shell crash on a bad query
        out(f"Error: {type(exc).__name__}: {exc}")
        return

    if isinstance(result, QueryResult):
        out(_format_table(result))
        n = len(result.rows)
        out(f"({n} row{'' if n == 1 else 's'})")
    elif isinstance(result, str):  # EXPLAIN
        out(result)
    elif isinstance(result, int):  # INSERT / UPDATE / DELETE
        out(f"{result} row{'' if result == 1 else 's'} affected")
    else:  # DDL returns None
        out("OK")


def _meta(db: Database, line: str, out: Callable[..., None]) -> bool:
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
                out(f"Error: {exc}")
                continue
            cols = ", ".join(f"{c.name} {c.type.name}" for c in info.columns)
            out(f"{table} ({cols})  [{info.row_count} rows]")
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


def repl(db: Database, read_line: Callable[[str], "str | None"], out: Callable[..., None]) -> None:
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
            if not _meta(db, line.strip(), out):
                return
            continue
        buffer += line + "\n"
        statements, buffer = _split_complete(buffer)
        for statement in statements:
            if statement.strip():
                _run(db, statement, out)


def main(argv: "list[str] | None" = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    db_dir = args[0] if args else "queryx_data"
    db = Database(db_dir)
    print(f"Connected to {db_dir!r}.")

    def read_line(prompt: str) -> "str | None":
        try:
            return input(prompt)
        except EOFError:
            return None

    try:
        repl(db, read_line, lambda text="": print(text))
    except KeyboardInterrupt:
        print("\n(interrupted)")
    finally:
        db.close()
        print("Goodbye.")


if __name__ == "__main__":
    main()
