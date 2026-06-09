"""QueryX index-vs-sequential-scan demo  --  why indexes matter.

A narrated demonstration, on ONE query, that an index turns a full-table scan
into a near-instant lookup. It uses only the public engine API: EXPLAIN to show
which access path the cost-based optimizer chose, and perf_counter timing to
measure the difference. It changes no engine internals.

    python examples/index_vs_seqscan_demo.py

Flow:
  1. Load enough rows that an index can actually win (on a tiny table a
     sequential scan is correctly cheaper, so the optimizer keeps using it).
  2. EXPLAIN `WHERE id = K` with NO index   -> SeqScan; time it.
  3. CREATE INDEX on id.
  4. EXPLAIN the same query                 -> IndexScan; time it.
  5. Report the measured speedup.

Reproducibility note: this demo intentionally reports timings, so the
millisecond values vary run-to-run. The STORY is invariant on every run:
the plan goes SeqScan -> IndexScan and the index scan is faster.
"""

from __future__ import annotations

import shutil
import tempfile
import time

from queryx.database import Database

N = 10_000          # rows -> many pages, so the index decisively beats a scan
CHUNK = 500         # rows per multi-row INSERT (fast bulk load)
KEY = 7_777         # a unique id to look up
REPS = 50           # repetitions per timing (averaged, after a warm-up)


def _avg_ms(db: Database, sql: str, reps: int) -> float:
    """Average wall-clock ms for one execution of ``sql`` (with a warm-up)."""
    db.execute(sql)  # warm-up (not timed)
    start = time.perf_counter()
    for _ in range(reps):
        db.execute(sql)
    return (time.perf_counter() - start) / reps * 1000.0


def _indent(text: str, n: int = 6) -> str:
    return "\n".join(" " * n + line for line in text.splitlines())


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="queryx_idxdemo_")
    query = f"SELECT id, kind FROM events WHERE id = {KEY}"
    try:
        print("=" * 64)
        print(" QueryX  --  Index vs Sequential Scan")
        print("=" * 64)
        print()

        # ---- [1/4] load --------------------------------------------------
        db = Database(workdir)
        db.execute("CREATE TABLE events (id INT, kind INT)")
        for base in range(0, N, CHUNK):
            tuples = ", ".join(f"({i}, {i % 100})" for i in range(base, min(base + CHUNK, N)))
            db.execute(f"INSERT INTO events VALUES {tuples}")
        print(f"[1/4] Loaded {N:,} rows into 'events'  (column 'id' is unique; no index yet)")
        print()

        # ---- [2/4] no index: sequential scan -----------------------------
        plan_seq = db.execute(f"EXPLAIN {query}")
        seq_ms = _avg_ms(db, query, REPS)
        print(f"[2/4] EXPLAIN {query}")
        print("      (no index on 'id' -> the optimizer must read every row)")
        print(_indent(plan_seq))
        print(f"      measured: {seq_ms:.3f} ms/query over {REPS} runs   [SEQUENTIAL SCAN]")
        print()

        # ---- [3/4] create the index --------------------------------------
        print("[3/4] CREATE INDEX idx_id ON events (id);")
        db.execute("CREATE INDEX idx_id ON events (id)")
        plan_idx = db.execute(f"EXPLAIN {query}")
        idx_ms = _avg_ms(db, query, REPS)
        print(f"      EXPLAIN {query}")
        print("      (an index now exists -> the optimizer chooses it)")
        print(_indent(plan_idx))
        print(f"      measured: {idx_ms:.3f} ms/query over {REPS} runs   [INDEX SCAN]")
        print()

        # ---- [4/4] verdict ----------------------------------------------
        result = db.execute(query).rows
        db.close()
        speedup = seq_ms / idx_ms if idx_ms > 0 else float("inf")
        chose_seq = "SeqScan" in plan_seq
        chose_idx = "IndexScan" in plan_idx
        print(f"[4/4] Query result: {result[0] if result else None}")
        print()
        print("=" * 64)
        print(f" SPEEDUP: ~{speedup:.0f}x faster with the index")
        print(f"   sequential scan : {seq_ms:.3f} ms/query  (visited all {N:,} rows)")
        print(f"   index scan      : {idx_ms:.3f} ms/query  (B+ tree descent, fetched 1 row)")
        print("=" * 64)

        # Self-check: the plans must be SeqScan then IndexScan, and the index faster.
        ok = chose_seq and chose_idx and idx_ms < seq_ms
        if not ok:
            print(" WARNING: expected SeqScan -> IndexScan with the index faster; "
                  f"got seqscan_plan={chose_seq}, index_plan={chose_idx}, faster={idx_ms < seq_ms}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
