# QueryX — Resume Material

Pick what fits your format. All claims are backed by the code and the 306-test
suite. Swap "Python" framing as needed for the role.

---

## One-line project header

**QueryX — a relational database engine built from scratch in Python** (page
storage, B+ tree/hash indexes, SQL parser, volcano execution, cost-based
optimizer with `EXPLAIN`, and WAL crash recovery). Standard library only; 306
tests.

---

## Bullet points (concise — pick 2–4)

- Built a relational database engine from scratch in Python (stdlib only) —
  paged storage, B+ tree and hash indexes, a SQL parser, a volcano-model
  executor, a cost-based optimizer, and write-ahead-log crash recovery — with
  306 passing tests.
- Implemented a disk-backed **B+ tree** (4KB-page nodes, recursive split
  propagation, linked-leaf range scans) and a static **hash index**; benchmarked
  both against sequential scan, measuring ~300× faster point lookups.
- Designed a **cost-based query optimizer** that estimates `SeqScan` vs
  `IndexScan` cost from table statistics and selectivity, with an `EXPLAIN` that
  renders the chosen plan — verified to preserve results across access paths.
- Built **write-ahead logging with redo crash recovery** (full-page-image
  logging, per-record CRC, checkpointing, replay-on-startup); demonstrated
  recovery of data from a deliberately corrupted page.
- Engineered a layered architecture (storage → index → SQL → execution →
  planner) with strictly downward dependencies, keeping each layer
  independently testable.

## Bullet points (detailed — for a projects section)

- **QueryX — relational database engine (Python, stdlib only).** Built the full
  pipeline from a SQL string to disk and back: a hand-written lexer +
  recursive-descent parser producing a typed AST; a volcano (iterator) execution
  engine (SeqScan, IndexScan, Filter, Projection, Sort, Limit, Distinct,
  scalar + grouped aggregates, nested-loop and index-nested-loop joins); a
  cost-based optimizer with `EXPLAIN`; a paged storage engine with a slotted-page
  layout and an LRU write-back buffer pool; disk-backed B+ tree and hash indexes;
  and write-ahead logging with redo crash recovery. 306 pytest cases; documented
  design, BNF grammar, benchmarks, and failure analysis.
- Demonstrated database-internals depth: slotted pages giving rows a stable
  identity across compaction; B+ tree growth-at-the-root balancing; Selinger-style
  selectivity estimation; and the write-ahead rule (log durably before applying)
  with idempotent full-page redo replay.

## Skills / keywords (for ATS)

Database internals, storage engines, B+ trees, hashing, indexing, query
optimization, cost-based optimizer, query execution, volcano/iterator model,
write-ahead logging (WAL), crash recovery, page/buffer management, SQL parsing,
recursive-descent parsing, data structures, systems programming, Python, pytest.

---

## Talking-point soundbites (for the interview itself)

- "A row's identity is its slot number, not its byte position — that indirection
  is why indexes survive page compaction."
- "A B+ tree is shallow because the cost is disk seeks, not comparisons — fan-out
  beats balance."
- "An index only wins when the predicate is selective; my optimizer keeps using a
  seq scan otherwise, which is exactly what real databases do."
- "Write-ahead means the log reaches durable storage *before* the data page; redo
  replay of full-page images is idempotent, so a torn write is just repaired."
- "The biggest thing I deliberately left out is transactions/MVCC — and I can
  explain precisely what that costs me (cross-file atomicity) and how I'd add it."
