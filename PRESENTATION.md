# QueryX — Presentation & Demo Runbook

A **runbook**: the order to present in, the exact commands to run, where to pause,
and what to tailor per audience. It deliberately contains **no scripted technical
answers, algorithm explanations, or Q&A** — prepare those yourself from the code
and `DESIGN.md`. Section 5 lists topics to rehearse (with file pointers); the
answers are intentionally left out.

---

## 0. Pre-flight checklist (do this before you present)

- [ ] `pip install -e ".[test]"` has been run; `python -m queryx` launches.
- [ ] Terminal font is large; window is wide enough for boxed tables and `.tree`.
- [ ] Start from a clean demo dir each run: `Remove-Item -Recurse -Force demo` (PowerShell) / `rm -rf demo`.
- [ ] Browser tab open on the GitHub repo (README rendered + the green **tests** CI badge).
- [ ] Decide your length: **~8 min** (sections marked `[core]`) or **~15 min** (all).
- [ ] The two demo scripts self-clean and are safe to re-run; rehearse them once.

---

## 1. Story arc (the beats — what to show, in order)

1. **Hook** — "a relational database engine, from scratch, standard library only." (repo + green CI)
2. **Architecture** — the layered pipeline, dependencies pointing down. (README Mermaid diagram)
3. **It really runs SQL** — the shell: CREATE / multi-row INSERT / SELECT / GROUP BY / JOIN / EXPLAIN.
4. **Not a CRUD app — a real engine** — expose the internals live: `.stats`, `.pages`, `.tree`.
5. **The money shot** — crash recovery: corrupt the disk, recover from the WAL.
6. **Why it's fast / honest scope** — index vs sequential scan, benchmarks, then the deliberate simplifications.

Keep each beat short; the demos carry the weight.

---

## 2. Exact command sequence (the runbook)

### A. Open — `[core]` (~30s)
```bash
pytest -q
```
Cue: point at the passing count; switch to the browser tab showing the green CI badge.

### B. Live SQL in the shell — `[core]` (~3–4 min)
```bash
python -m queryx demo
```
Type these (also in [examples/sample_queries.sql](examples/sample_queries.sql) to paste):
```sql
CREATE TABLE employees (id INT, name TEXT, dept_id INT, salary INT, age INT);
INSERT INTO employees VALUES
  (1,'alice',1,95000,30), (2,'bob',1,80000,25), (3,'carol',2,70000,40),
  (4,'dave',2,60000,35), (5,'erin',3,75000,28), (6,'frank',1,120000,45);
SELECT name, salary FROM employees WHERE salary >= 80000 ORDER BY salary DESC;
SELECT dept_id, COUNT(*), AVG(salary) FROM employees GROUP BY dept_id HAVING COUNT(*) >= 2;
CREATE TABLE departments (id INT, name TEXT);
INSERT INTO departments VALUES (1,'Engineering'), (2,'Sales'), (3,'Marketing');
SELECT e.name, d.name FROM employees e JOIN departments d ON e.dept_id = d.id;
EXPLAIN SELECT name FROM employees WHERE id = 3;
```
Cues (where to point — not what to explain): the boxed result + timing footer; the
`GROUP BY` aggregation; the two-table join; the `EXPLAIN` plan tree.

### C. Show the internals — `[core, the differentiator]` (~2 min)
Continue in the same shell:
```
.stats
.pages employees
CREATE INDEX idx_id ON employees (id);
.tree idx_id
.quit
```
Cues: the buffer-pool **hit ratio** on `.stats`; rows packed into a 4KB page on
`.pages`; the actual B+ tree on `.tree`. (Small table → `.tree` is height 1; for a
deeper tree show it inside the index demo in step E, which loads 10k rows.)

### D. Durability — `[core, the money shot]` (~2 min)
```bash
python examples/crash_recovery_demo.py
```
Cues (stage directions): pause when the page is **zeroed** (`'alice'` gone from
disk), then again on the green **PASS** line — let it land. This is the moment.

### E. Why indexes matter — `[core]` (~1–2 min)
```bash
python examples/index_vs_seqscan_demo.py
```
Cues: the `EXPLAIN` plan flips **SeqScan → IndexScan**; point at the cost numbers
and the measured speedup.

### F. Benchmarks — `[optional]` (~1 min)
```bash
python benchmarks/benchmark_suite.py
```
…or just open [benchmarks/REPORT.md](benchmarks/REPORT.md) and the chart. Cue:
say plainly these are in-process microbenchmarks (relative behaviour, not
production latency).

### G. Close — `[core]` (~30s)
Open the README "**How QueryX compares to SQLite / PostgreSQL**" table and the
"**Future work**" section. Cue: frame the simplifications as deliberate scope
decisions — honesty is the close.

---

## 3. Wow-moment cues (the three to land)

1. **Crash recovery** (step D) — the zero-the-page-then-recover. Biggest moment; slow down here.
2. **Self-inspecting engine** (step C) — `.stats` / `.pages` / `.tree`. "It shows its own internals."
3. **Optimizer flip + speedup** (step E) — `EXPLAIN` changes its mind and the clock proves it.

For each: pause, let the screen settle, and point — don't talk over the reveal.

---

## 4. Audience-tailoring notes

- **Database engineer:** spend your time on C, D, E and the EXPLAIN cost numbers;
  move fast through basic SQL (B). Expect deep questions — see §5. Have `DESIGN.md`
  open (failure analysis, WAL boundary).
- **Recruiter / non-specialist:** lead with the polished shell (B), the green CI
  badge, and the README; make **crash recovery (D)** the one demo they remember;
  keep internals (C) brief.
- **General professor / examiner:** run the full arc A→G; emphasise the test suite,
  the BNF grammar, and the honesty of the scope decisions. Be ready to trace any
  shown feature to its code.

---

## 5. Topics to rehearse (prepare your own answers from the code)

Prompts an examiner is likely to probe. **Answers are intentionally not written
here** — work them out from the listed files so you can defend them in your own
words.

- Slotted pages: why is a row's identity its *slot*, not its byte offset? → `queryx/storage/page.py`
- Buffer pool: eviction policy and write-back; what does the hit ratio mean? → `queryx/storage/buffer_pool.py`
- B+ tree vs a binary search tree — why is it shallow? What is fan-out? → `queryx/index/btree.py`
- B+ tree vs hash index — when does each win? Why can't hash range-scan? → `queryx/index/`, `benchmarks/REPORT.md`
- Volcano model: which operators are *blocking* and why? → `queryx/execution/operators.py`
- Optimizer: why does it pick a SeqScan on a small table? How is selectivity estimated? → `queryx/planner/`
- WAL: what exactly does it guarantee, and what is the checkpoint boundary? → `queryx/wal/`, README crash-recovery note
- The biggest thing you deliberately left out (transactions/MVCC) — why, and what would it take? → `DESIGN.md` (future work)

---

## 6. If something breaks mid-demo

- Always start demos from a fresh directory; the example scripts self-clean.
- If the shell gets into a confusing state: `.quit`, delete the demo dir, relaunch.
- The two example scripts are designed to run repeatably — if a run looks off, just re-run it.
- Worst case, fall back to the committed [benchmarks/REPORT.md](benchmarks/REPORT.md) and the README diagrams.
