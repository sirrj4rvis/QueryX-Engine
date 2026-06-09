# QueryX — Interview Preparation

Likely interview questions about QueryX, with model answers. Each answer is
grounded in what the project actually implements and is honest about the
simplifications — a flagged simplification is a *stronger* answer than a vague
claim. Skim the **one-liner** in bold, then the detail.

---

## Architecture & design

**Q. Walk me through what happens when you run `SELECT name FROM users WHERE age > 30`.**

**It falls down a layered pipeline, getting more concrete at each step.**
1. **Lexer** turns the text into tokens (`SELECT`, `name`, `FROM`, …).
2. **Parser** (recursive descent) builds an AST — a `Select` node with a
   projection list, a table, and a `Comparison(>, Column(age), Literal(30))`
   predicate.
3. **Optimizer** estimates the cost of a `SeqScan` vs an `IndexScan` for the
   predicate using table statistics and picks the cheaper; output is a tree of
   operators.
4. **Execution** runs that tree with the volcano model — `Projection` pulls from
   `Filter` pulls from `SeqScan`, one row at a time.
5. **Storage/index** layers serve the rows: the scan reads 4KB pages through the
   buffer pool from the heap file (or an index locates them).
   The WAL sits beside all of this, logging mutations before they hit disk.

**Q. Why organize the code by layer, with dependencies only pointing down?**

**So each layer is testable in isolation and there are no cycles.** `storage`
knows nothing about SQL, so I can test the buffer pool without a parser. If a
lower layer imported an upper one we'd get a cycle and lose that isolation. The
directory structure *is* the architecture, and the rule is enforceable (a grep
or import-linter check could fail CI on a violation).

---

## Storage engine

**Q. Why fixed-size 4KB pages instead of reading exactly the bytes you need?**

**Because the unit of disk I/O is a block, and a seek dominates the cost.**
Reading 50 bytes and reading 4096 cost essentially the same — one seek — so we
read a whole page and amortize it. 4KB matches common OS/disk block sizes
(SQLite's default; Postgres uses 8KB).

**Q. What's a slotted page and why use one?**

**A page layout where a slot directory grows from the front and variable-length
records grow from the back, with free space in the middle.** The key property:
a row's identity is its *slot number*, not its byte offset. An index points at
"page 7, slot 2"; if the page is later compacted and the record's bytes move,
only the slot's stored offset changes — the index entry stays valid. That
indirection is the whole reason slotted pages exist, and it's why variable-length
rows and deletes are possible.

**Q. Buffer pool — what does it do and what's your eviction policy?**

**An in-memory cache of pages with LRU eviction and write-back.** Repeated
accesses hit RAM; on a miss, if the pool is full I evict the least-recently-used
page (writing it back first if dirty). Writes are *lazy* (write-back), so a page
touched 100 times is written once. I implemented LRU with an ordered dict.
*Honest note:* Postgres actually uses a clock-sweep approximation of LRU, not
textbook LRU; I'd say "LRU-style."

**Q. What's the biggest correctness risk in your buffer pool?**

**No pinning.** A real buffer pool pins an in-use page so it can't be evicted
out from under a caller. QueryX is single-threaded and finishes with one page
before fetching the next, so I skipped pinning and documented the contract.
Under concurrency that would be a lost-update bug.

---

## Indexing

**Q. Why a B+ tree and not a binary search tree for the index?**

**Because the cost is disk reads, not comparisons.** A balanced binary tree over
1M keys is ~20 levels = ~20 random reads per lookup. A B+ tree makes each node a
full page with hundreds of keys, so fan-out is huge and the tree is ~3 levels —
~3 reads. Lookup is O(log_b n) where b (fan-out) is large, and that log counts
*page reads*.

**Q. B+ tree vs B-tree?**

**In a B+ tree all data lives in the leaves; internal nodes hold only separator
keys, and the leaves are linked in sorted order.** Keeping data out of internal
nodes makes them denser → higher fan-out → shallower tree. The linked leaves
make range scans "descend once, then walk the chain" — no re-traversal per key.

**Q. How does insertion keep the tree balanced?**

**It grows at the root, not the leaves.** A full leaf splits in two and pushes a
separator key up to its parent; if the parent overflows it splits too,
recursively; if the root splits, a new root is created and the tree gains a
level. Every leaf therefore stays at the same depth.

**Q. When would you use a hash index over a B+ tree?**

**For equality-only lookups.** A hash index is O(1) expected for `=`, but
hashing destroys order so it *cannot* range-scan or support `ORDER BY` — my
benchmark shows hash beating the B+ tree on point lookups, but the B+ tree is
the only one that can range. My adaptive-indexing advisor encodes exactly this:
recommend hash for equality-only workloads, B+ tree when ranges appear.

**Q. What breaks in your hash index at scale?**

**It's static hashing — a fixed bucket count — so a growing dataset turns a
bucket into a long overflow chain and lookups drift toward O(n).** Production
engines use extendible or linear hashing to grow the directory; I documented
this as the key limitation.

---

## SQL parsing

**Q. Lexer vs parser — why separate them?**

**Separation of concerns and testability.** The lexer knows what a single token
looks like (it's deliberately "dumb"); the parser knows how tokens combine into
structure. A lexer bug ("didn't recognize `>=`") never tangles with a parser bug
("AND bound looser than OR").

**Q. How do you handle operator precedence without a precedence table?**

**By layering the expression-parsing methods.** `or_expr → and_expr → not_expr →
comparison → primary`, each calling the next-tighter level. So `OR` binds
loosest and `a=1 OR b=2 AND c=3` parses as `a=1 OR (b=2 AND c=3)` — the grammar
structure encodes precedence directly.

**Q. Does a successful parse mean a valid query?**

**No — the parser checks syntax, not meaning.** `SELECT ghost FROM nowhere`
parses fine; name resolution against the catalog happens at execution time. This
is a deliberate boundary, and a good place to explain why parsing and semantic
analysis are separate phases in real compilers/databases.

---

## Execution

**Q. What is the volcano (iterator) model?**

**Every operator implements `open()/next()/close()`, and operators stack into a
tree; pulling `next()` at the root cascades down.** Rows flow up one at a time,
lazily, so `LIMIT 5` pulls exactly 5 rows and stops — no full-table
materialization. It's composable: any tree of operators is a valid plan.

**Q. Which operators block, and why does it matter?**

**`Sort` and `Aggregate` block; the rest stream.** `Sort` must read all input
before it can emit the smallest row; a scalar `Aggregate` must see every row
before it knows the `COUNT`. That's where memory is consumed and where latency
concentrates — and why `ORDER BY` on a huge table is expensive (a real engine
would spill to disk; QueryX sorts in memory).

**Q. How does `UPDATE` work given your heap has no in-place update?**

**Delete + re-insert.** A grown row won't fit its old slot, so I delete the old
row and insert a new one, which means the RowId changes and every index on the
table must be re-pointed. Postgres optimizes the common case with HOT updates;
mine always churns the indexes — a documented simplification.

---

## Query optimization

**Q. How does the optimizer decide between a sequential scan and an index scan?**

**It estimates each in page accesses and picks the cheaper.** `SeqScan` ≈ number
of data pages. `IndexScan` ≈ tree descent + one fetch per matching row. The
index wins only when the predicate is selective enough that
`descent + matches < pages` — which is why an unselective predicate correctly
keeps using a seq scan, even with an index available.

**Q. How do you estimate "how many rows match"?**

**Selectivity, Selinger-style.** Equality selectivity is `1/n_distinct`
(persisted per index when it's built); a unique column → ~1 matching row → the
index wins decisively. Without stats I fall back to the classic magic constants
(0.1 for equality, 1/3 for a range) and compose predicates assuming independence
(AND multiplies, OR is inclusion-exclusion).

**Q. Where does that estimation go wrong?**

**Correlated columns and skew.** The independence assumption underestimates
`WHERE city='NYC' AND state='NY'` badly; the uniform-range assumption misjudges
`age > 200`. And my stats go stale after inserts (I don't re-`ANALYZE`). These
are all *performance* bugs — the plan still returns correct rows, just maybe via
the slower path. Postgres mitigates with histograms, MCV lists, and autovacuum.

---

## WAL & crash recovery

**Q. What problem does the WAL solve, and what's the core rule?**

**Durability across crashes; the rule is write-ahead: log the change durably
before applying it to the data page.** If the process dies mid-write, the log is
the source of truth — on restart I replay it. I log full page images with a CRC;
replay is idempotent (rewriting a page is harmless), so a torn data-page write is
simply repaired.

**Q. Walk through a crash exactly between the log write and the data write.**

**That's the case the WAL exists for.** The log record is durable but the data
page is torn or stale. On restart, recovery replays the logged page image and
overwrites the bad data page — fixed. I demonstrate this in a test: write a page,
simulate a crash, zero the page on disk, reopen → the row is recovered from the
WAL. The contrast test (WAL off) shows the data is genuinely lost without it.

**Q. What if the crash happens *during* the log append?**

**The partial last record fails its length/CRC check and is discarded.** So that
mutation is atomically absent — and since we crashed before writing the data
page, the database is consistent at the previous state. That's the all-or-nothing
guarantee for the in-flight write.

**Q. What does your WAL NOT protect against?**

**Cross-file atomicity.** Each table/index file has its own WAL, so a statement
that writes a heap page and an index page can crash between them and leave them
disagreeing after independent recovery. Fixing that needs a single global log
with commit records — i.e. transactions, which I scoped out. I also flush (not
fsync) per record and fsync at checkpoint, so a *power loss* (not a process
crash) in that window can lose recent appends; real systems fsync per commit with
group commit.

---

## Scope & judgment

**Q. What did you deliberately leave out, and why?**

**Full ACID transactions with concurrency control / MVCC / locking.** That's the
single biggest simplification: QueryX gives durability (WAL redo) but not
isolation or atomic multi-statement transactions. Also deferred: subqueries,
`LIKE`/`IN`, joins beyond two tables, three-valued `NULL` logic. Each would
expand the parser/planner/executor for limited additional insight into core
internals. Knowing *why* they're out is part of the design.

**Q. If you kept going, what's the highest-value next step?**

**Transactions: a global WAL with commit records, then MVCC for isolation.**
That closes the cross-file atomicity gap and is the foundation everything else
(concurrency, rollback) builds on. After that, external-merge sort so `Sort`/
`GROUP BY` spill to disk, and histogram statistics so the optimizer handles skew.

**Q. What was the hardest bug?**

**The B+ tree leftmost-descent on duplicate/separator keys.** Search used
`bisect_left` at internal nodes, which could land one leaf to the left of the
target, and my continuation condition only walked right on an exact key match —
so separator keys were occasionally "lost." The fix was to keep walking right
while the leaf's max key is `<= target`. The deep-tree randomized test (2000 keys
vs a reference dict) caught it immediately.
