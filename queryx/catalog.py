"""The system catalog — QueryX's self-describing metadata.

The catalog records *what exists* logically in the database: the tables, each
table's ordered columns and their types, and the indexes defined on them. Both
the planner (to know which columns are indexed and what a row looks like) and the
executor (to resolve column names and serialize/deserialize rows) depend on it,
which is why it lives at the package root rather than inside any single layer.

This catalog is purely LOGICAL: it stores names, column schemas, and index
definitions — not file handles or page numbers. The physical mapping (which file
holds a table's heap, which file holds an index) is the Database facade's job,
derived from the names by convention. That keeps the catalog a small, pure,
testable metadata store.

Persistence is a JSON file. Real databases store the catalog in ordinary tables
that the database queries with its own machinery (PostgreSQL's pg_catalog,
SQLite's sqlite_master) — a far more elegant "the database describes itself"
design. QueryX uses JSON for simplicity; the conceptual role is identical, and
this simplification is called out in DESIGN.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from queryx.storage.page import ColumnType


class CatalogError(Exception):
    """A logical schema error: duplicate/missing table or index, bad column."""


@dataclass(frozen=True)
class ColumnInfo:
    """One column's logical definition: a name and a storage type."""
    name: str
    type: ColumnType


@dataclass
class TableInfo:
    """A table's logical schema: its name and ordered columns."""
    name: str
    columns: list[ColumnInfo]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def column_types(self) -> list[ColumnType]:
        return [c.type for c in self.columns]

    def has_column(self, name: str) -> bool:
        return any(c.name == name for c in self.columns)

    def position(self, name: str) -> int:
        """Return the 0-based index of column ``name`` (raises if absent)."""
        for i, c in enumerate(self.columns):
            if c.name == name:
                return i
        raise CatalogError(f"table {self.name!r} has no column {name!r}")


@dataclass
class IndexInfo:
    """An index definition: its name, the table/column it covers, and its kind."""
    name: str
    table: str
    column: str
    kind: str  # "btree" or "hash"


class Catalog:
    """A JSON-backed store of table schemas and index definitions."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._tables: dict[str, TableInfo] = {}
        self._indexes: dict[str, IndexInfo] = {}
        if os.path.exists(path):
            self._load()

    # -- tables -------------------------------------------------------------

    def create_table(self, name: str, columns: list[ColumnInfo]) -> TableInfo:
        if name in self._tables:
            raise CatalogError(f"table {name!r} already exists")
        if not columns:
            raise CatalogError(f"table {name!r} must have at least one column")
        seen: set[str] = set()
        for c in columns:
            if c.name in seen:
                raise CatalogError(f"duplicate column {c.name!r} in table {name!r}")
            seen.add(c.name)
        info = TableInfo(name=name, columns=list(columns))
        self._tables[name] = info
        self._save()
        return info

    def get_table(self, name: str) -> TableInfo:
        try:
            return self._tables[name]
        except KeyError:
            raise CatalogError(f"no such table: {name!r}")

    def has_table(self, name: str) -> bool:
        return name in self._tables

    def drop_table(self, name: str) -> None:
        if name not in self._tables:
            raise CatalogError(f"no such table: {name!r}")
        del self._tables[name]
        # DROP TABLE also drops every index defined on it.
        for index_name in [i.name for i in self._indexes.values() if i.table == name]:
            del self._indexes[index_name]
        self._save()

    def list_tables(self) -> list[str]:
        return sorted(self._tables)

    # -- indexes ------------------------------------------------------------

    def create_index(self, name: str, table: str, column: str, kind: str = "btree") -> IndexInfo:
        if name in self._indexes:
            raise CatalogError(f"index {name!r} already exists")
        if kind not in ("btree", "hash"):
            raise CatalogError(f"unknown index kind {kind!r} (expected 'btree' or 'hash')")
        table_info = self.get_table(table)  # raises if the table is missing
        if not table_info.has_column(column):
            raise CatalogError(f"table {table!r} has no column {column!r}")
        info = IndexInfo(name=name, table=table, column=column, kind=kind)
        self._indexes[name] = info
        self._save()
        return info

    def get_index(self, name: str) -> IndexInfo:
        try:
            return self._indexes[name]
        except KeyError:
            raise CatalogError(f"no such index: {name!r}")

    def has_index(self, name: str) -> bool:
        return name in self._indexes

    def drop_index(self, name: str) -> None:
        if name not in self._indexes:
            raise CatalogError(f"no such index: {name!r}")
        del self._indexes[name]
        self._save()

    def indexes_for_table(self, table: str) -> list[IndexInfo]:
        return [i for i in self._indexes.values() if i.table == table]

    def index_on(self, table: str, column: str) -> IndexInfo | None:
        """Return an index covering (table, column), or None. Used by the optimizer."""
        for i in self._indexes.values():
            if i.table == table and i.column == column:
                return i
        return None

    def list_indexes(self) -> list[str]:
        return sorted(self._indexes)

    # -- persistence --------------------------------------------------------

    def _save(self) -> None:
        data = {
            "tables": {
                name: {"columns": [[c.name, c.type.name] for c in info.columns]}
                for name, info in self._tables.items()
            },
            "indexes": {
                name: {"table": i.table, "column": i.column, "kind": i.kind}
                for name, i in self._indexes.items()
            },
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, t in data.get("tables", {}).items():
            columns = [ColumnInfo(col_name, ColumnType[type_name]) for col_name, type_name in t["columns"]]
            self._tables[name] = TableInfo(name=name, columns=columns)
        for name, i in data.get("indexes", {}).items():
            self._indexes[name] = IndexInfo(name=name, table=i["table"], column=i["column"], kind=i["kind"])
