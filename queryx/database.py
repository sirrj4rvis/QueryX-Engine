"""The Database facade — the single public entry point that wires the pipeline.

This is the top-level object an application (or a test, or a REPL) interacts
with. Its job is orchestration, not algorithms: given a SQL string, it drives
the full pipeline and hands back results, hiding the layer-by-layer wiring.

    db = Database("mydb.qx")
    db.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    db.execute("INSERT INTO users VALUES (1, 'alice', 30)")
    rows = db.execute("SELECT name FROM users WHERE age > 25")

Internally execute() will: lex + parse the SQL into an AST (sql/), plan and
optimize it (planner/), build and run the operator tree (execution/) against the
indexes (index/) and storage (storage/), with all mutations protected by the WAL
(wal/), using the catalog for schema. It owns the lifecycle of those components.

Filled in incrementally as each phase lands; the facade is what makes the
finished engine usable as `db.execute(...)`. No logic yet.
"""
