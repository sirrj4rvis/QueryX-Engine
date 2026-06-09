# QueryX — Resume Bullets

Factual, defensible bullets describing the project. Every claim traces to the
code, the benchmark suite, or the pytest suite (currently 327 tests — re-check
with `pytest` before submitting). Swap the "Python" framing as needed per role.

## One-line project header

**QueryX — a relational database engine built from scratch in Python** (paged
storage, B+ tree/hash indexes, SQL parser, volcano execution, cost-based
optimizer with `EXPLAIN`, and WAL crash recovery). Standard library only.

## Concise bullets (pick 2–4)

- Built a relational database engine from scratch in Python (standard library
  only) — paged storage, B+ tree and hash indexes, a SQL parser, a volcano-model
  executor, a cost-based optimizer, and write-ahead-log crash recovery — covered
  by a 327-test pytest suite.
- Implemented a disk-backed **B+ tree** (4KB-page nodes, recursive split
  propagation, linked-leaf range scans) and a static **hash index**; benchmarked
  both against a sequential scan, measuring point lookups hundreds of times
  faster (in-process microbenchmark).
- Designed a **cost-based query optimizer** that estimates `SeqScan` vs
  `IndexScan` cost from table statistics and selectivity, with an `EXPLAIN` that
  renders the chosen plan — verified to preserve results across access paths.
- Built **write-ahead logging with redo crash recovery** (full-page-image
  logging, per-record CRC, checkpointing, replay-on-startup); demonstrated
  recovery of data from a deliberately corrupted page.
- Engineered a layered architecture (storage → index → SQL → execution →
  planner) with strictly downward dependencies, keeping each layer
  independently testable.

## Detailed bullets (for a projects section)

- **QueryX — relational database engine (Python, standard library only).** Built
  the full pipeline from a SQL string to disk and back: a hand-written lexer +
  recursive-descent parser producing a typed AST; a volcano (iterator) execution
  engine (SeqScan, IndexScan, Filter, Projection, Sort, Limit, Distinct,
  scalar + grouped aggregates, nested-loop and index-nested-loop joins); a
  cost-based optimizer with `EXPLAIN`; a paged storage engine with a slotted-page
  layout and an LRU write-back buffer pool; disk-backed B+ tree and hash indexes;
  and write-ahead logging with redo crash recovery. 327 pytest cases; documented
  design, BNF grammar, benchmarks, and failure analysis.
- Demonstrated database-internals depth: slotted pages giving each row a stable
  slot identity independent of its byte position; B+ tree growth-at-the-root
  balancing; cost-based selectivity estimation using textbook default factors
  (1/distinct for equality, 1/3 for ranges) under an independence assumption;
  and the write-ahead rule (log durably before applying) with idempotent
  full-page redo replay.

## Skills / keywords (ATS)

Database internals, storage engines, B+ trees, hashing, indexing, query
optimization, cost-based optimizer, query execution, volcano/iterator model,
write-ahead logging (WAL), crash recovery, page/buffer management, SQL parsing,
recursive-descent parsing, data structures, systems programming, Python, pytest.
