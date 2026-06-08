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

> Filled in during **Phase 4**, when the parser is built. The grammar will
> cover the supported subset listed in the README, with operator precedence
> (`NOT` > comparison > `AND` > `OR`) documented explicitly.

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
