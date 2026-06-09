# QueryX — Design Document

This document is the engineering companion to the [README](README.md). It
records *why* QueryX is built the way it is: the architecture, the module
boundaries, the SQL grammar, and per-phase design decisions, trade-offs, and
honest failure analysis. It grows one phase at a time.

---

## 1. Design philosophy

1. **Architecturally faithful, deliberately small.** QueryX mirrors the real
   structure of a relational engine (storage → index → execution → planning,
   with a WAL underneath) but on a focused SQL subset. Depth on a small surface
   beats shallow breadth.
2. **Layered, with one-directional dependencies.** The package is organized by
   layer, and dependencies flow strictly downward (`sql → execution → planner →
   index → storage`). This keeps each layer testable in isolation and makes the
   architecture physically visible in the directory tree.
3. **Standard library only for the engine.** No ORM, no database libraries — we
   are building the database. Third-party packages appear only in test and
   benchmark tooling (`pytest`, `matplotlib`).
4. **Honest about scope.** Where QueryX simplifies relative to PostgreSQL or
   SQLite, the simplification is named, not hidden. Knowing *why* a feature is
   deferred is itself a deliverable.

---

## 2. Architecture

A SQL string is transformed step by step as it descends the stack:

```
  SQL text
    │
    ▼  sql/  (lexer -> parser)
  AST  ── a tree describing WHAT was asked
    │
    ▼  planner/  (statistics + optimizer)
  Query plan  ── a tree of operators describing HOW to run it
    │
    ▼  execution/  (volcano operators: open/next/close)
  Rows  ── pulled lazily, one at a time, up the operator tree
    │
    ▼  index/ + storage/
  Bytes on disk  ── 4KB pages in heap files, cached in the buffer pool

  wal/      logs every mutation before it is applied (durability)
  catalog/  records which tables, columns, and indexes exist
  database.py  the facade that owns and wires all of the above
```

### Module responsibilities

| Module | Phase | Responsibility |
|--------|-------|----------------|
| `storage/page.py` | 2 | 4KB page format; slotted-page record layout; row (de)serialization |
| `storage/pager.py` | 2 | Read/write pages by number; free-page tracking; the only raw disk I/O |
| `storage/buffer_pool.py` | 2 | In-memory page cache with LRU eviction; dirty-page write-back |
| `storage/heap_file.py` | 2 | Unordered table storage: `insert(row) -> row id`, `scan()` |
| `index/btree.py` | 3 | Disk-backed B+ tree: point lookup, range scan, node splitting |
| `index/hash_index.py` | 3 | Hash index: O(1) equality lookup; insert/search/delete |
| `sql/tokens.py` | 4 | Token kinds and the `Token` record |
| `sql/lexer.py` | 4 | Tokenizer: SQL text → token stream |
| `sql/ast.py` | 4 | AST node classes (statements, expressions, clauses) |
| `sql/parser.py` | 4 | Recursive-descent parser: tokens → AST |
| `execution/operators.py` | 5 | Volcano operators: SeqScan, IndexScan, Filter, Projection, Sort, Limit, Distinct, Aggregate |
| `planner/statistics.py` | 6 | Row counts, indexed columns, selectivity estimates |
| `planner/optimizer.py` | 6 | Cost models; SeqScan vs IndexScan selection |
| `planner/explain.py` | 6 | `EXPLAIN`: render the chosen plan + estimated cost |
| `wal/log.py` | 7 | Append-only log; log-record format; checkpoints |
| `wal/recovery.py` | 7 | Replay (redo) the log on startup |
| `catalog.py` | 2+ | System catalog: tables, columns, indexes |
| `database.py` | 2+ | Facade: `db.execute(sql)` driving the full pipeline |

---

## 3. SQL grammar (BNF)

The grammar QueryX's recursive-descent parser accepts. Uppercase words are
keywords (case-insensitive in practice); `{ x }` means zero or more; `[ x ]`
means optional; `|` is choice. Expression precedence is encoded by the rule
layering (loosest first): `OR` < `AND` < `NOT` < comparison < primary.

```bnf
statement      ::= ( select | insert | update | delete
                   | create_table | drop_table | create_index | drop_index ) [ ";" ]

create_table   ::= "CREATE" "TABLE" ident "(" column_def { "," column_def } ")"
column_def     ::= ident ( "INT" | "INTEGER" | "TEXT" )
drop_table     ::= "DROP" "TABLE" ident

create_index   ::= "CREATE" "INDEX" ident "ON" ident "(" ident ")"
drop_index     ::= "DROP" "INDEX" ident

insert         ::= "INSERT" "INTO" ident [ "(" ident { "," ident } ")" ]
                   "VALUES" "(" value { "," value } ")"

select         ::= "SELECT" [ "DISTINCT" ] select_list
                   "FROM" ident
                   [ "WHERE" expr ]
                   [ "ORDER" "BY" order_item { "," order_item } ]
                   [ "LIMIT" number ]
select_list    ::= "*" | select_item { "," select_item }
select_item    ::= aggregate | ident
aggregate      ::= "COUNT" "(" ( "*" | ident ) ")"
                 | ( "SUM" | "AVG" | "MIN" | "MAX" ) "(" ident ")"
order_item     ::= ident [ "ASC" | "DESC" ]

update         ::= "UPDATE" ident "SET" assignment { "," assignment } [ "WHERE" expr ]
assignment     ::= ident "=" value
delete         ::= "DELETE" "FROM" ident [ "WHERE" expr ]

expr           ::= or_expr
or_expr        ::= and_expr { "OR" and_expr }
and_expr       ::= not_expr { "AND" not_expr }
not_expr       ::= "NOT" not_expr | comparison
comparison     ::= primary [ ( "=" | "!=" | "<>" | "<" | ">" | "<=" | ">=" ) primary ]
primary        ::= "(" expr ")" | literal | ident
literal        ::= [ "-" ] number | string
value          ::= literal
```

Notes: comparison is non-associative (`a = b = c` is rejected). A leading `-`
is unary minus on a numeric literal only. `SELECT *` and `COUNT(*)` are the only
uses of `*`. The parser builds the AST in [ast.py](queryx/sql/ast.py); the
grammar above maps one rule ≈ one parser method.

---

## 4. Per-phase design notes

Each phase appends a section here covering: the problem it solves, the key
design decision and the rejected alternative, complexity and scaling limits, a
PostgreSQL/SQLite comparison, and an honest failure analysis.

### Phase 1 — Architecture & project skeleton

**Problem solved.** Establish the module boundaries and dependency direction
before any engine code exists, so every later phase has an obvious, isolated
place to land and no layer can accidentally depend upward.

**Key decision: organize by layer, not by feature.** The package is split into
`storage / index / sql / execution / planner / wal` — the stages of the query
pipeline — rather than by feature (e.g. a "users table" module). This makes the
downward dependency rule visible and enforceable: a reviewer can see at a glance
that `storage/` importing `sql/` would be a violation. *Rejected alternative:* a
flat package or feature-oriented layout, which would blur the layering that is
the whole pedagogical point of the project.

**Complexity / scaling.** No algorithmic content in Phase 1 — this phase is pure
structure. Complexity analysis begins in Phase 2 (pages and the buffer pool).

**PostgreSQL / SQLite comparison.** Both real engines are also layered along the
same pipeline (parser → planner/optimizer → executor → access methods → buffer
manager → storage), with a WAL beside it. PostgreSQL spreads this across a large
C codebase; SQLite is a famously compact amalgamation but follows the same
conceptual stages. QueryX's by-layer Python packages are a faithful, miniature
echo of that structure.

**Failure analysis.** The main risk a skeleton can introduce is *latent
architectural debt*: an import that quietly points upward, or a layer that grows
to do a neighbor's job, would erode the boundaries before they are tested. The
Phase 1 smoke tests guard the first part (every module imports cleanly); the
one-directional dependency rule is enforced by convention and code review until,
if desired, an automated import-direction check is added in a later phase.

### Phase 2 — Storage Engine

**Problem solved.** Give the engine durable, byte-level storage: pack
variable-length rows into fixed 4KB pages, move pages to and from disk, cache
hot pages in memory, and organize a table's rows so they can be inserted,
scanned, and read back after a restart. Everything above this layer can now
pretend rows are objects; this layer makes that pretence durable.

**The four components (bottom-up).**

- `page.py` — a 4KB **slotted page**: a 4-byte header (`num_slots`, `free_end`),
  a slot directory `(offset, length)` growing up from the front, and record
  bytes growing down from the back. Plus row (de)serialization (`INT` = 8-byte
  signed, `TEXT` = uint16-length-prefixed UTF-8).
- `pager.py` — the only raw disk I/O. Page 0 is a reserved **header page**
  (magic, version, free list); data pages start at page 1. `read_page`/
  `write_page` by number, `allocate_page` (reuse-or-extend), `free_page`.
- `buffer_pool.py` — a fixed-capacity **write-back LRU cache** over the pager:
  cache-with-identity, dirty tracking, lazy write-back, `OrderedDict` LRU.
- `heap_file.py` — the table: `insert(record) -> RowId(page_no, slot)`,
  `scan()`, `get`, `delete`, all through the buffer pool.

**Key decisions and rejected alternatives.**

1. **Slotted pages over fixed-length records.** Slotted pages support
   variable-length rows and let a row be moved or deleted while its *slot
   number* (its identity, what indexes point at) stays constant. *Rejected:*
   fixed-length records — simpler, but waste space on short strings and cannot
   store variable text. Every real engine uses slotted pages.
2. **A reserved page-0 header with a flat free-list array.** Lets the file
   describe itself and survive restarts. *Rejected (for now):* a linked free
   list threaded through the freed pages themselves — unbounded and what
   production engines use, but harder to read. Our array caps the free list at
   ~1021 entries (documented limit).
3. **Write-back (not write-through) buffer pool.** A page touched many times is
   written once, on eviction/flush. *Rejected:* write-through (flush every
   change) — simpler and crash-safer, but defeats the entire point of a cache.
   Crash-safety is instead handed to the WAL in Phase 7.
4. **Append-mostly heap placement, no Free Space Map.** `insert` tries the last
   page, else allocates a new one. *Rejected (deferred):* a Free Space Map that
   finds holes left by deletes — the correct production answer, but a subsystem
   of its own. Consequence: deleted space is reclaimed only by a same-page
   insert.

**Complexity / scaling.** Per-page insert/get/delete are O(1) (insert scans
slots for a dead one, O(slots-per-page) ≈ a few hundred, a constant). Buffer
pool get is O(1) hit / O(1)+one read miss; eviction O(1). Heap `scan` is
O(pages). The dominant real cost everywhere is the **disk seek**, not the
comparisons — which is the entire reason for 4KB pages (amortize one seek over
4096 bytes) and the buffer pool (avoid the seek entirely for hot pages). Where
it stops scaling: a full-table `scan` is O(pages) with no way to skip — that is
precisely the problem the B+ tree index solves in Phase 3.

**PostgreSQL / SQLite comparison.** Both use the same shape: fixed-size pages
(Postgres 8KB, SQLite 4KB — we match SQLite), slotted pages with a slot/cell
directory and indirection so rows can move, a shared buffer cache with
clock/LRU-style eviction and dirty write-back, and a self-describing header/
catalog. We simplify in the obvious places: a flat free list instead of
Postgres's Free Space Map / SQLite's freelist trunk pages; LRU instead of
Postgres's clock-sweep; no per-page checksums; no multi-table page directory
yet. *Honesty note:* Postgres's exact eviction is a clock-sweep approximation of
LRU, not textbook LRU — I should not claim Postgres "uses LRU".

**Engineering review — flaws, debt, simplifications.**

- *Page fragmentation, no compaction.* `delete_record` frees the slot but leaves
  the record bytes as a dead hole; only same-page reuse reclaims it. Over a
  delete-heavy workload a page can hold mostly dead bytes yet report itself full.
  A `compact()` pass is needed and deferred.
- *No pinning in the buffer pool.* A caller holding a `Page` across a `get_page`
  that triggers eviction could mutate a now-evicted object — a lost update. Safe
  only because QueryX is single-threaded and finishes one page before fetching
  the next. Documented as a contract, not enforced.
- *Free list capped at ~1021 entries* (single page-0 array); `free_page` raises
  when full instead of spilling to a linked list.
- *No per-page checksum / format validation* beyond the page-0 magic and
  version, so silent on-disk corruption of a data page is undetectable.

**Failure analysis — concrete scenarios.**

- *Torn write (partial page write).* `write_page` issues one 4KB write. If the
  process is killed mid-write — or power is lost — the OS may have persisted only
  part of the page, leaving a half-old/half-new page that deserializes to garbage
  (e.g. a slot offset pointing into the wrong region). We have no detection and
  no recovery. **Postgres** defends with full-page images in its WAL (the first
  write of a page after a checkpoint logs the whole page) and optional checksums;
  **SQLite** uses a rollback/WAL journal. QueryX closes this gap in Phase 7
  (WAL); until then, durability holds only against a clean `close()`/`flush()`.
- *Lost free list on crash.* `free_page`/`allocate_page` rewrite page 0, but if a
  crash happens after a data page is freed/extended yet before page 0 is
  re-persisted, the in-memory free list and the file disagree on restart — a page
  can leak (lost forever) or, worse, be handed out twice. There is no atomic
  multi-page update. WAL would make this atomic.
- *fsync gap.* Intermediate `write_page` only `flush()`es to the OS, so it
  survives a process kill but **not** a power loss; only `close()`/`sync()`
  fsyncs. A crash between writes can therefore lose committed-looking data on
  power failure.
- *Eviction of a dirty page during a read storm.* With a small pool, a `scan`
  that also dirties pages can evict-and-write pages repeatedly, turning a read
  into many writes; correctness holds but performance degrades — a thrashing
  buffer pool, mitigated only by sizing capacity sensibly.

### Phase 3 — Index Manager

**Problem solved.** Phase 2's only way to find a row is `heap.scan()` — O(pages),
the whole table. An index is a separate on-disk structure mapping a key to the
RowId(s) where matching rows live, so lookups become sub-linear. We build two
with opposite trade-offs and benchmark them.

**The B+ tree (`btree.py`) — the headline.** A balanced tree whose nodes are
4KB pages, so fan-out is large and the tree is 2-3 levels deep for millions of
keys; the cost that matters is *page reads*, O(log_b n) with large base b. All
data lives in the leaves; internal nodes hold only separator keys; leaves are
linked in sorted order so a range scan descends once then walks the chain.
Insertion splits a full leaf and propagates the split upward, growing the tree
at the root. Supports point lookup, ordered range scan, duplicate keys, and
leaf-only delete.

**The hash index (`hash_index.py`) — the foil.** Static hashing: a fixed
directory of N bucket pages (`bucket = stable_hash(key) % N`), each chaining to
overflow pages when full. O(1) expected point lookup, insert, delete — and *no*
range scan, because hashing destroys order. Included to make the B+ tree's
range capability concrete by contrast.

**Key decisions and rejected alternatives.**

1. **B+ tree, not a plain BST, for the primary index.** A binary tree of 1M keys
   is ~20 levels = ~20 random disk reads per lookup; a B+ tree with hundreds of
   keys per node is ~3. *Rejected:* in-memory balanced trees (don't survive
   restart, don't bound disk reads).
2. **Nodes ride on the buffer pool via `Page.overwrite`.** A node is parsed into
   an in-memory `_Node`, mutated, then the whole node re-serialized back. This
   is correctness-first and, usefully, makes the pool's lack of pinning a
   non-issue (we never depend on a cached Page surviving load→store). *Rejected
   (deferred):* in-place byte edits — faster, but fiddly and error-prone; the
   benchmark shows the cost we pay (B+ tree load ~5x slower than hash).
3. **Static hashing for the hash index.** Simple and fully on-disk. *Rejected
   (deferred):* extendible / linear hashing that grows the directory — the
   production answer, but a subsystem of its own.
4. **Leaf-only delete (no merge/rebalance).** Correct for search because
   separators need not exist as live data. *Rejected (deferred):* full
   rebalancing — needed to reclaim space, but out of Phase 3 scope.

**Complexity / scaling.** B+ tree: search/insert/delete O(log_b n) page reads;
range scan O(log_b n + k/b). Hash: O(1 + chain length) — O(1) at low load
factor, degrading to O(n/N) as buckets overflow. Where each stops scaling: the
hash index has a *fixed* bucket count, so a growing dataset lengthens overflow
chains until lookups crawl; the B+ tree's cost grows only logarithmically but
its split-heavy insert path (with full-node re-serialization here) is the
practical bottleneck.

**Benchmark (N=20,000, warm pool).**

| operation | B+ tree | hash index | heap seqscan |
|---|---|---|---|
| bulk load (insert) | 7,286 ops/s | 36,635 ops/s | 49,715 ops/s |
| point lookup | 8,480 ops/s | 23,658 ops/s | 38 ops/s |
| range scan [500 keys] | 2,205 ops/s | unsupported | (via seqscan) |

Point lookup via hash is ~625x faster than a heap seq scan; the seq scan's 38
ops/s is the O(rows) wall the indexes exist to break. The hash index cannot
range-scan at all. (In-process timings, no per-op fsync — they measure
structure cost, not raw disk latency. Charted suite is Phase 8.)

**PostgreSQL / SQLite comparison.** Both default to B+ trees for indexes
(Postgres's nbtree, SQLite's table/index B-trees). Postgres also offers a hash
index access method (historically unlogged/less used); SQLite has no hash index.
Real B+ trees do prefix compression, right-most-leaf insert fast paths, and full
rebalancing on delete; real hash indexes grow dynamically. We implement the core
mechanics and simplify the rest, honestly flagged.

**Failure analysis — concrete scenarios.**

- *Split crash (B+ tree).* A leaf split writes the new right node, relinks the
  left node's `next_leaf`, and updates the parent — multiple pages, not atomic.
  A crash after writing the right node but before the parent's new separator/
  child pointer is flushed leaves an orphaned node: its keys are unreachable
  from the root (lost data) though the leaf chain may still find them on a scan.
  **Postgres** logs the split in WAL so recovery completes it atomically; QueryX
  has no such protection until Phase 7. Today durability holds only against a
  clean `flush()` + `close()`.
- *Stale root pointer.* `insert` updates `self._root` and rewrites the meta page
  after a root split. A crash between the new root's write and the meta-page
  flush leaves the meta pointing at the *old* root — the tree silently loses the
  level just added. Again a WAL-atomicity gap.
- *Hash overflow-chain crash.* `insert` into a full bucket allocates an overflow
  page, relinks the bucket's `next_overflow`, then writes the entry. A crash
  between the relink and the entry write (or vice versa) can leak an empty
  overflow page or drop the inserted entry. No atomicity across the two writes.
- *Hash hot-bucket degradation (not a crash, a design limit).* Because buckets
  are fixed, a skewed key distribution piles one bucket into a long overflow
  chain; that bucket's lookups degrade toward O(n) while others stay O(1). A
  dynamic hashing scheme would rebalance; static hashing cannot.
- *Duplicate-key fan-out.* A key with thousands of duplicates spreads RowIds
  across many leaves (B+ tree) or a long chain (hash); `search` must visit them
  all. Correct, but a single equality lookup is no longer cheap — a reason real
  systems sometimes prefer bitmap indexes for low-cardinality columns.

### Phase 4 — SQL Parser

**Problem solved.** Turn a SQL *string* into a structured AST the rest of the
engine can act on, replacing method calls (`heap.insert`) with a real query
language — without string-matching or special-casing any command.

**The two stages.** `lexer.py` (a hand-written scanner) turns characters into a
flat token stream — keywords vs. identifiers, number/string literals (with `''`
escape), multi-char operators (`<=`, `>=`, `<>`, `!=`), whitespace and `--`
comments. `parser.py` (recursive descent) turns tokens into the AST in
`ast.py`. `tokens.py` holds the shared token vocabulary and the positioned
`SQLSyntaxError`.

**Key decisions and rejected alternatives.**

1. **Hand-written recursive-descent parser.** One method per grammar rule, so
   the code mirrors the BNF and is easy to read, extend, and debug — and it
   produces clear positioned errors. *Rejected:* a parser generator
   (PLY/Lark/ANTLR) — less code, but a third-party dependency (we are stdlib-
   only for the engine) and a generated black box that teaches less.
2. **Precedence by layered methods, not a precedence table.** `or_expr →
   and_expr → not_expr → comparison → primary`; each layer climbs only to the
   next-tighter one, so `OR` binds loosest and parentheses override. *Rejected:*
   an explicit operator-precedence/Pratt parser — more general (needed for rich
   arithmetic), but overkill for a fixed, small operator set.
3. **Behavior-free AST dataclasses.** The AST is pure data, the clean contract
   between parser and planner. Column types reuse `storage.page.ColumnType`
   (a downward dependency, allowed) rather than duplicating an enum.

**Complexity / scaling.** Both lexer and parser are O(n) in input length with
bounded lookahead — each character/token is consumed a constant number of times.
No backtracking. Not a bottleneck; parsing a statement is trivial next to
executing it.

**PostgreSQL / SQLite comparison.** Both use *generated* parsers — Postgres a
Bison/yacc grammar, SQLite the bespoke Lemon generator — but the two-stage
(lex → parse → tree) shape is identical to ours. Real SQL grammars are vastly
larger (joins, subqueries, CTEs, window functions, full expression trees with
arithmetic and functions). QueryX implements a focused subset by hand; the
mechanism is faithful, the surface is deliberately small.

**Engineering review — flaws, debt, simplifications.**

- *Syntax only, no semantic validation.* The parser happily accepts
  `SELECT ghost FROM nowhere` or `COUNT(*) , name` (mixing an aggregate with a
  bare column, which is meaningless without GROUP BY). Catching unknown
  tables/columns and illegal aggregate mixing is the planner/executor's job
  (Phases 5-6) using the catalog — not done here.
- *Integer-only numeric literals.* No floats/decimals; `AVG` will yield a float
  at execution time but cannot be written as a literal. Documented gap.
- *One statement per parse.* No multi-statement scripts beyond a single optional
  trailing `;`.
- *No qualified names / aliases* (`t.col`, `AS`), consistent with the
  single-table, no-join scope.

**Failure analysis — concrete scenarios.**

- *Silent acceptance of nonsense.* Because there is no catalog check, a typo
  like `WHERE aeg > 30` parses cleanly and only fails (or worse, returns wrong
  results) downstream. The mitigation is name resolution against the catalog in
  the executor; until then a parse success does NOT mean a valid query.
- *Aggregate/column mix.* `SELECT COUNT(*), name FROM t` parses into a valid AST
  but has no meaningful scalar-aggregate semantics; the executor must reject it
  (we have no GROUP BY). A real planner raises "column must appear in GROUP BY".
- *Integer literal overflow.* `INSERT ... VALUES (99999999999999999999)` lexes
  into a Python int of arbitrary size, but the storage layer serializes INT as
  8 bytes signed — the out-of-range value will raise (or, if unchecked, wrap) at
  serialization time, not at parse time. The boundary check belongs at
  insert/execution.
- *Deep parenthesization nesting* recurses in Python; a pathological
  `((((...))))` could hit the recursion limit. Real parsers cap nesting depth;
  we do not. Not a concern for hand-written queries.

### Phase 5 — Execution Engine

**Problem solved.** Connect the parser to storage and actually *run* queries.
This is the first phase where all layers meet: it adds the system catalog, the
volcano operators, and the `Database` facade that turns a SQL string into rows.

**The volcano (iterator) model.** Every operator implements `open()/next()/
close()`; operators stack into a tree, and pulling `next()` at the root cascades
down, so rows flow up one at a time, lazily. Operators built: `SeqScan`,
`IndexScan`, `Filter`, `Projection`, `Sort`, `Limit`, `Distinct`, and a scalar
`Aggregate` (COUNT/SUM/AVG/MIN/MAX, no GROUP BY). Most stream; `Sort` and
`Aggregate` are *blocking* (must consume all input before emitting).

**The catalog (`catalog.py`).** Logical metadata — tables, columns/types, index
definitions — JSON-backed. The executor uses it to resolve names (catching the
typos the parser blindly accepts) and to serialize rows by schema.

**The facade (`database.py`).** A database is a directory: a JSON catalog plus
one pager file per table (`tbl_<name>.qx`) and per index (`idx_<name>.qx`).
`execute(sql)` dispatches on statement type and, for SELECT, assembles the
operator tree: `SeqScan → Filter → Sort → Projection → Distinct → Limit` (or
`SeqScan → Filter → Aggregate`).

**Key decisions and rejected alternatives.**

1. **Volcano/iterator model.** Composable, lazy, constant memory for streaming
   operators, and the model every textbook and classic engine uses. *Rejected:*
   materialize-everything (simpler but blows memory) and vectorized/columnar
   batches (faster for analytics, much more code, and overkill at this scale).
2. **Sort before Projection.** Sorting the full (pre-projection) rows lets
   `ORDER BY` reference columns that aren't in the SELECT list — standard SQL
   behavior. *Rejected:* project-then-sort, which would forbid `ORDER BY` on
   unselected columns.
3. **UPDATE = delete + re-insert.** The heap has no in-place update (Phase 2),
   and a grown row won't fit its slot, so update deletes the old row and inserts
   a new one, re-pointing indexes. *Consequence:* a row's RowId changes on
   update — honest and documented.
4. **A naive planner for now.** Phase 5 always uses `SeqScan`, never an index,
   even when one exists. Cost-based access-path selection is deliberately
   deferred to Phase 6; this keeps the execution layer and the optimizer as
   separate, independently reviewable concerns.

**Complexity / scaling.** A SELECT is dominated by its scan: `SeqScan` is
O(rows); `Filter/Projection/Limit/Distinct` add O(rows) streaming passes; `Sort`
is O(rows log rows) and buffers everything; `Aggregate` is O(rows), O(1) state.
Because the planner never uses an index yet, *every* query — even `WHERE id = 42`
— is O(rows). That is the exact inefficiency Phase 6 removes.

**PostgreSQL / SQLite comparison.** Both use the iterator model (Postgres's
`ExecProcNode` pulls tuples through a plan tree; SQLite compiles to a bytecode
VM that is iterator-like). Both resolve names against a catalog stored in tables.
We match the shape; we lack their breadth (joins, GROUP BY, subqueries), their
in-place `UPDATE`, and — crucially — their cost-based planner (next phase).

**Engineering review — flaws, debt, simplifications.**

- *No index usage in the planner.* The biggest current inefficiency, by design;
  Phase 6's job.
- *UPDATE churns RowIds and leaves dead space.* Delete+insert fragments pages
  (no compaction) and rewrites index entries even when the indexed column didn't
  change. A real engine updates in place when it fits and only touches indexes
  on changed columns (Postgres's HOT updates).
- *Eager per-statement flush.* Durable but slow — every mutation flushes the
  pool. Fine until benchmarking; the WAL (Phase 7) is the real durability story.
- *No NULLs, no GROUP BY, single table.* Aggregates are scalar-only; mixing an
  aggregate with a bare column is rejected.

**Failure analysis — concrete scenarios.**

- *Crash mid-UPDATE.* Update deletes the old row, inserts a new one, then fixes
  indexes — several unsynchronized writes. A crash between the heap insert and
  the index re-point leaves an index entry pointing at the old (deleted) RowId
  or missing the new one: a later IndexScan would miss the row or fetch a dead
  slot. `IndexScan` defensively skips dead slots, but a *stale* entry pointing at
  a slot since reused by a different row would return the WRONG row. This is the
  canonical argument for the WAL (Phase 7) and for atomic index maintenance.
- *Eager flush is not atomic across files.* A table's heap and its index files
  are separate pagers flushed in sequence; a crash between them leaves heap and
  index disagreeing. No cross-file atomicity until the WAL.
- *Type confusion via direct values.* Comparisons rely on Python semantics; an
  INT column compared to a TEXT literal raises `TypeError` at evaluation rather
  than being caught at plan time. We type-check INSERT/UPDATE literals, but not
  every WHERE comparison against the column's declared type.
- *Sort/aggregate memory.* Both buffer all input rows in Python lists; a SELECT
  over a table larger than memory would exhaust RAM. Production engines spill to
  disk (external merge sort); we do not.

---

## 5. Future work / out of scope

Deferred deliberately (see README for the user-facing list). Brief rationale:

- **Full ACID transactions, MVCC, locking, deadlock detection.** A large
  subsystem in its own right; QueryX provides durability (WAL redo) but not
  isolation or concurrency control. This is the single biggest simplification
  and the most important one to be able to explain.
- **`LIKE` / `IN` / subqueries / multi-table joins.** Each expands the parser,
  planner, and executor substantially for limited additional insight into core
  internals; at most one two-table join is a Phase 9 stretch.
- **Monitoring, Docker, parallel/columnar execution, compression, plan
  caching.** Tooling and performance engineering that demonstrate ops skills
  rather than database internals; doing them poorly is worse than deferring.
