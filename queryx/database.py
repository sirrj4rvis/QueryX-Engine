"""The Database facade — the single public entry point that wires the pipeline.

This is the top-level object an application, test, or REPL interacts with. Its
job is orchestration, not algorithms: given a SQL string it drives the full
pipeline (parse -> plan -> execute against storage and indexes) and returns a
result, hiding the layer-by-layer wiring.

    db = Database("mydb")                      # a directory holding the database
    db.execute("CREATE TABLE users (id INT, name TEXT, age INT)")
    db.execute("INSERT INTO users VALUES (1, 'alice', 30)")
    result = db.execute("SELECT name FROM users WHERE age >= 25")
    result.columns  # ['name']
    result.rows     # [('alice',)]

Physical layout: a database is a directory containing a JSON catalog plus one
pager file per table (tbl_<name>.qx) and one per index (idx_<name>.qx), the file
names derived from the logical names. The catalog stays purely logical; this
facade owns the physical mapping and the lifecycle of the storage objects.

SCOPE / SIMPLIFICATIONS (see DESIGN.md):
  * Phase 5 uses a NAIVE plan: always SeqScan (+ Filter), never an index, even
    when one exists. Cost-based access-path selection is Phase 6. (IndexScan the
    operator exists and is tested; the optimizer that chooses it does not yet.)
  * UPDATE/DELETE are implemented as scan -> collect matches -> apply, and UPDATE
    is delete-old + insert-new (the heap has no in-place update), so a row's
    RowId changes on update and affected indexes are re-pointed.
  * Indexes require an INT column (index keys are integers).
  * Mutations flush eagerly for durability (no WAL yet — that is Phase 7).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from queryx.catalog import Catalog, CatalogError, ColumnInfo
from queryx.execution.operators import (
    Aggregate, Distinct, Filter, HashAggregate, IndexNestedLoopJoin, IndexScan,
    Limit, NestedLoopJoin, Operator, Projection, SeqScan, Sort, aggregates_in,
    column_index,
)
from queryx.index.btree import BPlusTree
from queryx.index.hash_index import HashIndex
from queryx.planner.advisor import IndexRecommendation, WorkloadAdvisor
from queryx.planner.explain import format_plan
from queryx.planner.optimizer import AccessPath, choose_access_path, sargable_comparisons
from queryx.planner.statistics import TableStats
from queryx.sql import ast
from queryx.sql.parser import parse
from queryx.sql.tokens import TokenType
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import HeapFile, RowId
from queryx.storage.page import ColumnType, deserialize_row, serialize_row
from queryx.storage.pager import Pager

_POOL_CAPACITY = 64


class QueryError(Exception):
    """A runtime query error: type mismatch, arity, or an unsupported combination."""


@dataclass
class QueryResult:
    """The result of a SELECT: column names and the rows produced."""
    columns: list[str]
    rows: list[tuple]

    def __iter__(self):
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


class Database:
    """Top-level facade: open a database directory and run SQL against it."""

    def __init__(self, directory: str, use_wal: bool = True, advisor_min_uses: int = 5) -> None:
        self.directory = directory
        self._use_wal = use_wal  # disable only for benchmarking WAL overhead
        os.makedirs(directory, exist_ok=True)
        self.catalog = Catalog(os.path.join(directory, "catalog.json"))
        # Open storage objects, lazily created/cached, keyed by logical name.
        self._tables: dict[str, tuple[Pager, HeapFile]] = {}
        self._indexes: dict[str, tuple[Pager, object]] = {}
        # Adaptive indexing: observes filtered columns to recommend indexes.
        self.advisor = WorkloadAdvisor(min_uses=advisor_min_uses)

    # -- public API ---------------------------------------------------------

    def execute(self, sql: str):
        """Parse and run one SQL statement; return a QueryResult, a row count, or None."""
        stmt = parse(sql)
        if isinstance(stmt, ast.CreateTable):
            return self._create_table(stmt)
        if isinstance(stmt, ast.DropTable):
            return self._drop_table(stmt)
        if isinstance(stmt, ast.CreateIndex):
            return self._create_index(stmt)
        if isinstance(stmt, ast.DropIndex):
            return self._drop_index(stmt)
        if isinstance(stmt, ast.Insert):
            return self._insert(stmt)
        if isinstance(stmt, ast.Select):
            return self._select(stmt)
        if isinstance(stmt, ast.Update):
            return self._update(stmt)
        if isinstance(stmt, ast.Delete):
            return self._delete(stmt)
        if isinstance(stmt, ast.Explain):
            return self._explain(stmt.query)
        raise QueryError(f"unsupported statement type {type(stmt).__name__}")

    def close(self) -> None:
        """Flush and close all open table/index files."""
        for pager, heap in self._tables.values():
            heap.flush()
            pager.close()
        for pager, index in self._indexes.values():
            index.flush()  # type: ignore[attr-defined]
            pager.close()
        self._tables.clear()
        self._indexes.clear()

    def simulate_crash(self) -> None:
        """Test/demo: abandon all open files WITHOUT checkpointing, leaving each
        pager's WAL intact so the next open recovers. Mimics a process crash."""
        for pager, _ in self._tables.values():
            pager.simulate_crash()
        for pager, _ in self._indexes.values():
            pager.simulate_crash()
        self._tables.clear()
        self._indexes.clear()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- storage object access ---------------------------------------------

    def _heap(self, table: str) -> HeapFile:
        if table not in self._tables:
            path = os.path.join(self.directory, f"tbl_{table}.qx")
            pager = Pager(path, use_wal=self._use_wal)
            self._tables[table] = (pager, HeapFile(BufferPool(pager, capacity=_POOL_CAPACITY)))
        return self._tables[table][1]

    def _index(self, name: str):
        if name not in self._indexes:
            info = self.catalog.get_index(name)
            path = os.path.join(self.directory, f"idx_{name}.qx")
            pager = Pager(path, use_wal=self._use_wal)
            pool = BufferPool(pager, capacity=_POOL_CAPACITY)
            obj = BPlusTree(pool) if info.kind == "btree" else HashIndex(pool)
            self._indexes[name] = (pager, obj)
        return self._indexes[name][1]

    def _flush_table(self, table: str) -> None:
        """Persist a table's heap and every index on it (eager durability)."""
        self._heap(table).flush()
        for info in self.catalog.indexes_for_table(table):
            self._index(info.name).flush()  # type: ignore[attr-defined]

    # -- DDL ----------------------------------------------------------------

    def _create_table(self, stmt: ast.CreateTable) -> None:
        columns = [ColumnInfo(c.name, c.type) for c in stmt.columns]
        self.catalog.create_table(stmt.table, columns)
        self._heap(stmt.table)  # create the heap file now so it exists on disk
        return None

    def _drop_table(self, stmt: ast.DropTable) -> None:
        table = stmt.table
        index_names = [i.name for i in self.catalog.indexes_for_table(table)]
        self.catalog.drop_table(table)  # validates existence, cascades indexes
        self._discard_table_file(table)
        for name in index_names:
            self._discard_index_file(name)
        return None

    def _create_index(self, stmt: ast.CreateIndex) -> None:
        self._create_index_impl(stmt.name, stmt.table, stmt.column, kind="btree")
        return None

    def _create_index_impl(self, name: str, table: str, column: str, kind: str) -> None:
        """Create an index of the given kind and populate it from existing rows."""
        table_info = self.catalog.get_table(table)
        if table_info.columns[table_info.position(column)].type != ColumnType.INT:
            raise QueryError(
                f"index requires an INT column; {table}.{column} is not INT "
                "(index keys are integers in QueryX)"
            )
        self.catalog.create_index(name, table, column, kind=kind)
        index = self._index(name)  # reads the kind back from the catalog
        heap = self._heap(table)
        types = table_info.column_types
        pos = table_info.position(column)
        distinct_keys: set = set()
        for rid, record in heap.scan():
            key = deserialize_row(types, record)[pos]
            index.insert(key, rid)  # type: ignore[attr-defined]
            distinct_keys.add(key)
        index.flush()  # type: ignore[attr-defined]
        # Record the column statistic the optimizer uses for equality selectivity.
        self.catalog.set_index_stats(name, len(distinct_keys))

    # -- adaptive indexing (Phase 9c) --------------------------------------

    def recommend_indexes(self) -> list[IndexRecommendation]:
        """Indexes the workload advisor suggests, given the queries seen so far."""
        return self.advisor.recommend(self.catalog)

    def apply_recommendations(self) -> list[str]:
        """Create every recommended index; return the names created."""
        created = []
        for rec in self.recommend_indexes():
            name = f"auto_{rec.table}_{rec.column}"
            if self.catalog.has_index(name):
                continue
            self._create_index_impl(name, rec.table, rec.column, rec.kind)
            created.append(name)
        return created

    def _drop_index(self, stmt: ast.DropIndex) -> None:
        self.catalog.drop_index(stmt.name)  # validates existence
        self._discard_index_file(stmt.name)
        return None

    # -- DML ----------------------------------------------------------------

    def _insert(self, stmt: ast.Insert) -> int:
        table_info = self.catalog.get_table(stmt.table)
        heap = self._heap(stmt.table)
        indexes = self.catalog.indexes_for_table(stmt.table)
        # Resolve and type-check every row first, so a bad row in a multi-row
        # insert doesn't leave a partially-applied statement.
        resolved = [self._resolve_row(table_info, stmt.columns, row, stmt.table) for row in stmt.rows]
        for values in resolved:
            rid = heap.insert(serialize_row(table_info.column_types, values))
            for info in indexes:
                self._index(info.name).insert(values[table_info.position(info.column)], rid)  # type: ignore[attr-defined]
        self.catalog.add_row_count(stmt.table, len(resolved))
        self._flush_table(stmt.table)  # flush once for the whole (multi-row) insert
        return len(resolved)

    def _update(self, stmt: ast.Update) -> int:
        table_info = self.catalog.get_table(stmt.table)
        types = table_info.column_types
        index_of = {name: i for i, name in enumerate(table_info.column_names)}

        # Validate assignment columns and value types up front.
        for a in stmt.assignments:
            if not table_info.has_column(a.column):
                raise QueryError(f"no such column {a.column!r} in {stmt.table!r}")
            self._check_literal_type(table_info, a.column, a.value)
        if stmt.where is not None:
            self._validate_columns(table_info, self._columns_in(stmt.where))

        heap = self._heap(stmt.table)
        # Collect matches first (don't mutate the heap mid-scan).
        matches: list[tuple[RowId, tuple]] = []
        for rid, record in heap.scan():
            row = deserialize_row(types, record)
            if stmt.where is None or self._eval(stmt.where, row, index_of):
                matches.append((rid, row))

        indexes = self.catalog.indexes_for_table(stmt.table)
        for rid, old_row in matches:
            new_row = list(old_row)
            for a in stmt.assignments:
                new_row[index_of[a.column]] = a.value.value
            new_row_t = tuple(new_row)
            heap.delete(rid)
            new_rid = heap.insert(serialize_row(types, new_row_t))
            for info in indexes:  # re-point each index at the new RowId
                pos = table_info.position(info.column)
                index = self._index(info.name)
                index.delete(old_row[pos], rid)        # type: ignore[attr-defined]
                index.insert(new_row_t[pos], new_rid)  # type: ignore[attr-defined]
        self._flush_table(stmt.table)
        return len(matches)

    def _delete(self, stmt: ast.Delete) -> int:
        table_info = self.catalog.get_table(stmt.table)
        types = table_info.column_types
        index_of = {name: i for i, name in enumerate(table_info.column_names)}
        if stmt.where is not None:
            self._validate_columns(table_info, self._columns_in(stmt.where))

        heap = self._heap(stmt.table)
        matches: list[tuple[RowId, tuple]] = []
        for rid, record in heap.scan():
            row = deserialize_row(types, record)
            if stmt.where is None or self._eval(stmt.where, row, index_of):
                matches.append((rid, row))

        indexes = self.catalog.indexes_for_table(stmt.table)
        for rid, row in matches:
            heap.delete(rid)
            for info in indexes:
                self._index(info.name).delete(row[table_info.position(info.column)], rid)  # type: ignore[attr-defined]
        self.catalog.add_row_count(stmt.table, -len(matches))
        self._flush_table(stmt.table)
        return len(matches)

    # -- SELECT (the Phase 5 deliverable) -----------------------------------

    def _select(self, stmt: ast.Select) -> QueryResult:
        if stmt.join is not None:
            return self._select_join(stmt)

        table_info = self.catalog.get_table(stmt.table)

        # Validate WHERE columns; ORDER BY validation depends on the path below.
        if stmt.where is not None:
            self._validate_columns(table_info, set(self._columns_in(stmt.where)))

        # Cost-based access-path selection (SeqScan vs IndexScan + residual).
        root: Operator = self._build_scan(table_info, stmt.table, stmt.where)

        if stmt.group_by is not None:
            return self._select_grouped(stmt, table_info, root)

        aggregates = [p for p in stmt.projections if isinstance(p, ast.Aggregate)]
        stars = [p for p in stmt.projections if isinstance(p, ast.Star)]

        if aggregates:
            if len(aggregates) != len(stmt.projections):
                raise QueryError("cannot mix aggregates with plain columns (no GROUP BY)")
            if stmt.distinct or stmt.order_by:
                raise QueryError("DISTINCT/ORDER BY are not supported with scalar aggregates")
            for agg in aggregates:
                if isinstance(agg.arg, ast.Column):
                    self._validate_columns(table_info, {agg.arg.name})
            root = Aggregate(root, aggregates)
            return QueryResult(columns=list(root.column_names), rows=list(root))

        # Regular projection path.
        if stars and len(stmt.projections) > 1:
            raise QueryError("cannot combine * with other select items")

        if stmt.order_by:  # sort full rows so ORDER BY may reference any column
            self._validate_columns(table_info, {o.column for o in stmt.order_by})
            root = Sort(root, stmt.order_by)

        if not stars:  # explicit column list
            proj_names = [p.name for p in stmt.projections]  # type: ignore[attr-defined]
            self._validate_columns(table_info, set(proj_names))
            root = Projection(root, proj_names)

        if stmt.distinct:
            root = Distinct(root)
        if stmt.limit is not None:
            root = Limit(root, stmt.limit)

        return QueryResult(columns=list(root.column_names), rows=list(root))

    def _select_grouped(self, stmt: ast.Select, table_info, root: Operator) -> QueryResult:
        """Build the GROUP BY pipeline: scan -> HashAggregate -> (order/limit)."""
        group_by = stmt.group_by
        self._validate_columns(table_info, set(group_by))

        for p in stmt.projections:
            if isinstance(p, ast.Star):
                raise QueryError("SELECT * is not supported with GROUP BY")
            if isinstance(p, ast.Column):
                if p.name not in group_by:
                    raise QueryError(f"column {p.name!r} must appear in GROUP BY or be aggregated")
            elif isinstance(p, ast.Aggregate) and isinstance(p.arg, ast.Column):
                self._validate_columns(table_info, {p.arg.name})

        if stmt.having is not None:
            for col in self._columns_in(stmt.having):  # plain columns (aggregate args excluded)
                if col not in group_by:
                    raise QueryError(f"HAVING column {col!r} must be a GROUP BY column or aggregated")
            for agg in aggregates_in(stmt.having):
                if isinstance(agg.arg, ast.Column):
                    self._validate_columns(table_info, {agg.arg.name})

        root = HashAggregate(root, group_by, stmt.projections, stmt.having)

        if stmt.order_by:  # in a grouped query, ORDER BY must name a selected output column
            output = set(root.column_names)
            for o in stmt.order_by:
                if o.column not in output:
                    raise QueryError(f"ORDER BY {o.column!r} must be a selected column in a GROUP BY query")
            root = Sort(root, stmt.order_by)
        if stmt.distinct:
            root = Distinct(root)
        if stmt.limit is not None:
            root = Limit(root, stmt.limit)

        return QueryResult(columns=list(root.column_names), rows=list(root))

    def _select_join(self, stmt: ast.Select) -> QueryResult:
        """Build a two-table INNER JOIN pipeline (nested-loop or index-nested-loop)."""
        left_info = self.catalog.get_table(stmt.table)
        right_info = self.catalog.get_table(stmt.join.table)
        if stmt.group_by is not None or any(isinstance(p, ast.Aggregate) for p in stmt.projections):
            raise QueryError("GROUP BY and aggregates are not supported with JOIN")

        left_alias = stmt.table_alias or stmt.table
        right_alias = stmt.join.alias or stmt.join.table
        if left_alias == right_alias:
            raise QueryError("the two join inputs need distinct names/aliases")

        left_qual = [f"{left_alias}.{c}" for c in left_info.column_names]
        right_qual = [f"{right_alias}.{c}" for c in right_info.column_names]
        cidx = column_index(left_qual + right_qual)

        self._validate_keys(self._referenced_keys(stmt.join.on), cidx, "ON")
        if stmt.where is not None:
            self._validate_keys(self._referenced_keys(stmt.where), cidx, "WHERE")

        left_scan = SeqScan(self._heap(stmt.table), left_qual, left_info.column_types)
        root: Operator = self._build_join(
            stmt, right_info, left_alias, right_alias, right_qual, left_scan
        )

        if stmt.where is not None:
            root = Filter(root, stmt.where)
        if stmt.order_by:
            for o in stmt.order_by:
                if o.column not in cidx:
                    raise QueryError(f"ORDER BY {o.column!r} is not a column of the joined result")
            root = Sort(root, stmt.order_by)

        stars = [p for p in stmt.projections if isinstance(p, ast.Star)]
        if stars and len(stmt.projections) > 1:
            raise QueryError("cannot combine * with other select items")
        if not stars:
            names = []
            for p in stmt.projections:
                if not isinstance(p, ast.Column):
                    raise QueryError("only column projections are supported with JOIN")
                if p.key not in cidx:
                    raise QueryError(f"unknown column {p.key!r} in the joined result")
                names.append(p.key)
            root = Projection(root, names)

        if stmt.distinct:
            root = Distinct(root)
        if stmt.limit is not None:
            root = Limit(root, stmt.limit)
        return QueryResult(columns=list(root.column_names), rows=list(root))

    def _build_join(self, stmt, right_info, left_alias, right_alias, right_qual, left_scan):
        """Pick index-nested-loop when the join is an equijoin on an indexed right column."""
        equi = self._equijoin(stmt.join.on, left_alias, right_alias)
        if equi is not None:
            left_key, _right_key, right_col = equi
            info = self.catalog.index_on(stmt.join.table, right_col)
            if info is not None:
                index = self._index(info.name)
                return IndexNestedLoopJoin(
                    left_scan, self._heap(stmt.join.table), index, left_key,
                    right_qual, right_info.column_types,
                )
        right_scan = SeqScan(self._heap(stmt.join.table), right_qual, right_info.column_types)
        return NestedLoopJoin(left_scan, right_scan, stmt.join.on)

    @staticmethod
    def _equijoin(on, left_alias, right_alias):
        """If ``on`` is ``left_alias.x = right_alias.y`` (either order), return
        (left_key_qualified, right_key_qualified, right_bare_column); else None."""
        if not isinstance(on, ast.Comparison) or on.op != TokenType.EQ:
            return None
        if not (isinstance(on.left, ast.Column) and isinstance(on.right, ast.Column)):
            return None

        def side(col):
            if col.table == left_alias:
                return "L"
            if col.table == right_alias:
                return "R"
            return None

        sl, sr = side(on.left), side(on.right)
        if {sl, sr} != {"L", "R"}:
            return None
        left_col, right_col = (on.left, on.right) if sl == "L" else (on.right, on.left)
        return (f"{left_alias}.{left_col.name}", f"{right_alias}.{right_col.name}", right_col.name)

    def _referenced_keys(self, expr: ast.Expr) -> set[str]:
        """Lookup keys (qualified or bare) of every column referenced in a predicate."""
        if isinstance(expr, ast.Column):
            return {expr.key}
        if isinstance(expr, ast.Comparison):
            return self._referenced_keys(expr.left) | self._referenced_keys(expr.right)
        if isinstance(expr, (ast.And, ast.Or)):
            return self._referenced_keys(expr.left) | self._referenced_keys(expr.right)
        if isinstance(expr, ast.Not):
            return self._referenced_keys(expr.operand)
        return set()

    @staticmethod
    def _validate_keys(keys: set[str], column_idx: dict, clause: str) -> None:
        for key in keys:
            if key not in column_idx:
                raise QueryError(f"unknown or ambiguous column {key!r} in {clause}")

    # -- planning helpers ---------------------------------------------------

    def _table_stats(self, table: str) -> TableStats:
        table_info = self.catalog.get_table(table)
        self._heap(table)  # ensure the pager is open
        pager = self._tables[table][0]
        return TableStats(row_count=table_info.row_count, num_data_pages=max(0, pager.num_pages - 1))

    def _access_path(self, table_info, where) -> AccessPath:
        indexes = self.catalog.indexes_for_table(table_info.name)
        n_distinct = {i.column: i.n_distinct for i in indexes if i.n_distinct is not None}
        return choose_access_path(where, self._table_stats(table_info.name), indexes, n_distinct)

    _RANGE_OPS = (TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE)

    def _build_scan(self, table_info, table: str, where) -> Operator:
        """Build the scan (+ residual Filter) the optimizer chose."""
        heap = self._heap(table)
        names, types = table_info.column_names, table_info.column_types
        # Adaptive indexing: note which columns this query filters on.
        for column, op, _value in sargable_comparisons(where):
            if table_info.has_column(column):
                self.advisor.record_predicate(table, column, op in self._RANGE_OPS)
        ap = self._access_path(table_info, where)
        if ap.method == "IndexScan":
            index = self._index(ap.index_name)
            root: Operator = IndexScan(heap, self._rowids_for(index, ap), names, types)
            if ap.residual is not None:
                root = Filter(root, ap.residual)
            return root
        root = SeqScan(heap, names, types)
        if where is not None:
            root = Filter(root, where)
        return root

    def _rowids_for(self, index, ap: AccessPath) -> list:
        """Produce the RowIds for an IndexScan from the chosen access path.

        Index keys are integers, so exclusive range bounds are exact via +/-1.
        """
        op, value = ap.op, ap.value
        if op == TokenType.EQ:
            return index.search(value)
        if op == TokenType.GT:
            return [rid for _k, rid in index.range_scan(low=value + 1)]
        if op == TokenType.GTE:
            return [rid for _k, rid in index.range_scan(low=value)]
        if op == TokenType.LT:
            return [rid for _k, rid in index.range_scan(high=value - 1)]
        if op == TokenType.LTE:
            return [rid for _k, rid in index.range_scan(high=value)]
        raise QueryError(f"unsupported index operator {op}")  # pragma: no cover

    def _explain(self, select: ast.Select) -> str:
        if select.join is not None:
            return self._explain_join(select)
        table_info = self.catalog.get_table(select.table)
        referenced = set(self._columns_in(select.where)) if select.where else set()
        if select.order_by:
            referenced.update(o.column for o in select.order_by)
        self._validate_columns(table_info, referenced)
        indexes = self.catalog.indexes_for_table(select.table)
        n_distinct = {i.column: i.n_distinct for i in indexes if i.n_distinct is not None}
        return format_plan(select, table_info, self._table_stats(select.table), indexes, n_distinct)

    def _explain_join(self, select: ast.Select) -> str:
        from queryx.planner.explain import expr_to_str

        left_alias = select.table_alias or select.table
        right_alias = select.join.alias or select.join.table
        equi = self._equijoin(select.join.on, left_alias, right_alias)
        method = "NestedLoopJoin"
        right_leaf = f"SeqScan on {select.join.table} (as {right_alias})"
        if equi is not None:
            _lk, _rk, right_col = equi
            info = self.catalog.index_on(select.join.table, right_col)
            if info is not None:
                method = "IndexNestedLoopJoin"
                right_leaf = f"IndexScan on {select.join.table} (as {right_alias}) using {info.name}"

        lines: list[str] = []
        depth = 0

        def emit(text: str) -> None:
            nonlocal depth
            lines.append("  " * depth + ("-> " if depth else "") + text)
            depth += 1

        if select.limit is not None:
            emit(f"Limit: {select.limit}")
        if select.distinct:
            emit("Distinct")
        if not any(isinstance(p, ast.Star) for p in select.projections):
            cols = ", ".join(p.key for p in select.projections if isinstance(p, ast.Column))
            emit(f"Projection: {cols}")
        if select.order_by:
            keys = ", ".join(f"{o.column}{' DESC' if o.descending else ''}" for o in select.order_by)
            emit(f"Sort: {keys}")
        if select.where is not None:
            emit(f"Filter: {expr_to_str(select.where)}")
        emit(f"{method}  [{expr_to_str(select.join.on)}]")
        child = "  " * depth + "-> "
        lines.append(child + f"SeqScan on {select.table} (as {left_alias})")
        lines.append(child + right_leaf)
        return "\n".join(lines)

    # -- helpers ------------------------------------------------------------

    def _eval(self, expr: ast.Expr, row: tuple, index_of: dict[str, int]) -> bool:
        from queryx.execution.operators import evaluate
        return bool(evaluate(expr, row, index_of))

    def _resolve_row(self, table_info, columns, row_values: list, table: str) -> list:
        """Resolve one INSERT row into table-column order and type-check it."""
        if columns is not None:
            for c in columns:
                if not table_info.has_column(c):
                    raise QueryError(f"no such column {c!r} in {table!r}")
            if len(columns) != len(set(columns)):
                raise QueryError("duplicate column in INSERT column list")
            if len(columns) != len(row_values):
                raise QueryError(
                    f"INSERT lists {len(columns)} columns but {len(row_values)} values"
                )
            provided = dict(zip(columns, row_values))
            missing = [c for c in table_info.column_names if c not in provided]
            if missing:
                raise QueryError(f"missing values for columns {missing} (no defaults/NULL)")
            ordered = [provided[c] for c in table_info.column_names]
        else:
            if len(row_values) != len(table_info.columns):
                raise QueryError(
                    f"table {table!r} has {len(table_info.columns)} columns, "
                    f"got {len(row_values)} values"
                )
            ordered = row_values

        values = []
        for col, literal in zip(table_info.columns, ordered):
            self._check_value_type(col.name, col.type, literal)
            values.append(literal.value)
        return values

    def _check_literal_type(self, table_info, column: str, literal: ast.Literal) -> None:
        col = table_info.columns[table_info.position(column)]
        self._check_value_type(col.name, col.type, literal)

    def _check_value_type(self, name: str, col_type: ColumnType, literal: ast.Literal) -> None:
        if not isinstance(literal, ast.Literal):
            raise QueryError(f"value for {name!r} must be a literal")
        if literal.type != col_type:
            raise QueryError(
                f"type mismatch for column {name!r}: expected {col_type.name}, "
                f"got {literal.type.name}"
            )

    def _columns_in(self, expr: ast.Expr) -> set[str]:
        """All column names referenced anywhere in a predicate expression."""
        if isinstance(expr, ast.Column):
            return {expr.name}
        if isinstance(expr, ast.Comparison):
            return self._columns_in(expr.left) | self._columns_in(expr.right)
        if isinstance(expr, ast.And) or isinstance(expr, ast.Or):
            return self._columns_in(expr.left) | self._columns_in(expr.right)
        if isinstance(expr, ast.Not):
            return self._columns_in(expr.operand)
        return set()  # Literal, Star

    def _validate_columns(self, table_info, names: set[str]) -> None:
        for name in names:
            if not table_info.has_column(name):
                raise CatalogError(f"no such column {name!r} in table {table_info.name!r}")

    # -- file disposal ------------------------------------------------------

    def _discard_table_file(self, table: str) -> None:
        entry = self._tables.pop(table, None)
        if entry is not None:
            entry[0].close()
        self._remove_file(f"tbl_{table}.qx")

    def _discard_index_file(self, name: str) -> None:
        entry = self._indexes.pop(name, None)
        if entry is not None:
            entry[0].close()
        self._remove_file(f"idx_{name}.qx")

    def _remove_file(self, filename: str) -> None:
        path = os.path.join(self.directory, filename)
        if os.path.exists(path):
            os.remove(path)
