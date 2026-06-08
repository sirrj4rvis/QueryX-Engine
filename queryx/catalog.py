"""The system catalog — QueryX's self-describing metadata.

The catalog records *what exists* in the database: the tables, each table's
columns and their types, and the indexes defined on them. Both the planner (to
know which columns are indexed and what a row looks like) and the executor (to
serialize/deserialize rows and resolve column names) depend on it, which is why
it lives at the package root rather than inside any single layer.

In a real database the catalog is itself stored in ordinary tables (PostgreSQL's
pg_catalog, SQLite's sqlite_master) — the database describes itself using its own
machinery. QueryX may start with a simpler in-memory/JSON-backed catalog and is
free to note that simplification; the conceptual role is identical.

Implemented from Phase 2 onward as tables/indexes are introduced. No logic yet.
"""
