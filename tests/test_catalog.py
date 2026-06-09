"""Phase 5, slice 1 — catalog tests.

The catalog is pure logical metadata: table schemas and index definitions, with
JSON persistence. These prove creation/lookup/drop, the validation rules
(duplicates, missing references, unknown columns), DROP TABLE cascading to its
indexes, and that everything survives reload from disk.
"""

import pytest

from queryx.catalog import Catalog, CatalogError, ColumnInfo
from queryx.storage.page import ColumnType


@pytest.fixture
def catalog(tmp_path):
    return Catalog(str(tmp_path / "catalog.json"))


def users_cols():
    return [
        ColumnInfo("id", ColumnType.INT),
        ColumnInfo("name", ColumnType.TEXT),
        ColumnInfo("age", ColumnType.INT),
    ]


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_create_and_get_table(catalog):
    info = catalog.create_table("users", users_cols())
    assert info.name == "users"
    assert catalog.get_table("users").column_names == ["id", "name", "age"]
    assert catalog.get_table("users").column_types == [ColumnType.INT, ColumnType.TEXT, ColumnType.INT]


def test_column_position(catalog):
    catalog.create_table("users", users_cols())
    t = catalog.get_table("users")
    assert t.position("id") == 0
    assert t.position("age") == 2
    with pytest.raises(CatalogError):
        t.position("ghost")


def test_duplicate_table_rejected(catalog):
    catalog.create_table("users", users_cols())
    with pytest.raises(CatalogError):
        catalog.create_table("users", users_cols())


def test_duplicate_column_rejected(catalog):
    with pytest.raises(CatalogError):
        catalog.create_table("t", [ColumnInfo("a", ColumnType.INT), ColumnInfo("a", ColumnType.TEXT)])


def test_empty_table_rejected(catalog):
    with pytest.raises(CatalogError):
        catalog.create_table("t", [])


def test_get_missing_table_raises(catalog):
    with pytest.raises(CatalogError):
        catalog.get_table("nope")


def test_drop_table(catalog):
    catalog.create_table("users", users_cols())
    catalog.drop_table("users")
    assert not catalog.has_table("users")
    with pytest.raises(CatalogError):
        catalog.drop_table("users")


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_create_and_get_index(catalog):
    catalog.create_table("users", users_cols())
    catalog.create_index("idx_age", "users", "age")
    idx = catalog.get_index("idx_age")
    assert idx.table == "users" and idx.column == "age" and idx.kind == "btree"


def test_index_kind_hash(catalog):
    catalog.create_table("users", users_cols())
    catalog.create_index("h", "users", "id", kind="hash")
    assert catalog.get_index("h").kind == "hash"


def test_index_on_missing_table_rejected(catalog):
    with pytest.raises(CatalogError):
        catalog.create_index("idx", "ghost", "x")


def test_index_on_missing_column_rejected(catalog):
    catalog.create_table("users", users_cols())
    with pytest.raises(CatalogError):
        catalog.create_index("idx", "users", "ghost")


def test_bad_index_kind_rejected(catalog):
    catalog.create_table("users", users_cols())
    with pytest.raises(CatalogError):
        catalog.create_index("idx", "users", "age", kind="rtree")


def test_index_on_lookup(catalog):
    catalog.create_table("users", users_cols())
    catalog.create_index("idx_age", "users", "age")
    assert catalog.index_on("users", "age").name == "idx_age"
    assert catalog.index_on("users", "name") is None


def test_drop_table_cascades_to_indexes(catalog):
    catalog.create_table("users", users_cols())
    catalog.create_index("idx_age", "users", "age")
    catalog.create_index("idx_id", "users", "id")
    catalog.drop_table("users")
    assert catalog.list_indexes() == []


def test_drop_index(catalog):
    catalog.create_table("users", users_cols())
    catalog.create_index("idx_age", "users", "age")
    catalog.drop_index("idx_age")
    assert not catalog.has_index("idx_age")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_catalog_survives_reload(tmp_path):
    path = str(tmp_path / "catalog.json")
    cat = Catalog(path)
    cat.create_table("users", users_cols())
    cat.create_index("idx_age", "users", "age", kind="hash")

    reloaded = Catalog(path)
    assert reloaded.list_tables() == ["users"]
    assert reloaded.get_table("users").column_types == [ColumnType.INT, ColumnType.TEXT, ColumnType.INT]
    idx = reloaded.get_index("idx_age")
    assert idx.column == "age" and idx.kind == "hash"
