"""Phase 3 benchmark — B+ tree vs. hash index vs. heap sequential scan.

Demonstrates the trade-offs the three access paths make, which is exactly what
the Phase 6 optimizer will reason about:

  * point lookup:  hash ~ B+ tree, both crush a heap seq scan
  * range scan:    B+ tree only — a hash index physically cannot do it
  * a seq scan is O(rows); both indexes are sub-linear

These are in-process timings with a warm buffer pool (no fsync per op), so they
measure structure/algorithm cost, not raw disk latency. The full charted suite
is Phase 8; this is a focused head-to-head. Standard library only.

Run:  python benchmarks/index_benchmark.py
"""

from __future__ import annotations

import random
import shutil
import tempfile
import time
from pathlib import Path

from queryx.index.btree import BPlusTree
from queryx.index.hash_index import HashIndex
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import HeapFile, RowId
from queryx.storage.page import ColumnType, deserialize_row, serialize_row
from queryx.storage.pager import Pager

N = 20_000          # rows / keys loaded into each structure
LOOKUPS = 2_000     # random point lookups timed against the indexes
HEAP_LOOKUPS = 200  # fewer, because each one scans the whole heap
POOL = 256          # buffer-pool capacity (pages)
SCHEMA = [ColumnType.INT, ColumnType.TEXT]


def _timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def _rate(count: int, seconds: float) -> str:
    return f"{count / seconds:,.0f} ops/s" if seconds > 0 else "n/a"


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="queryx_bench_"))
    try:
        keys = list(range(N))
        random.Random(7).shuffle(keys)
        lookup_keys = [random.Random(11).randint(0, N - 1) for _ in range(LOOKUPS)]

        # --- build each structure, timing the load ---
        btree_pager = Pager(str(workdir / "btree.qx"))
        btree = BPlusTree(BufferPool(btree_pager, capacity=POOL))
        hash_pager = Pager(str(workdir / "hash.qx"))
        hidx = HashIndex(BufferPool(hash_pager, capacity=POOL), num_buckets=256)
        heap_pager = Pager(str(workdir / "heap.qx"))
        heap = HeapFile(BufferPool(heap_pager, capacity=POOL))

        # Heap rows, recording the RowId each key landed at (the indexes point here).
        heap_rids: dict[int, RowId] = {}

        def load_heap() -> None:
            for k in keys:
                heap_rids[k] = heap.insert(serialize_row(SCHEMA, (k, f"name{k}")))

        heap_load = _timed(load_heap)
        btree_load = _timed(lambda: [btree.insert(k, heap_rids[k]) for k in keys])
        hash_load = _timed(lambda: [hidx.insert(k, heap_rids[k]) for k in keys])

        # --- point lookups ---
        def btree_points() -> None:
            for k in lookup_keys:
                btree.search(k)

        def hash_points() -> None:
            for k in lookup_keys:
                hidx.search(k)

        def heap_points() -> None:
            for k in lookup_keys[:HEAP_LOOKUPS]:
                for _rid, rec in heap.scan():
                    if deserialize_row(SCHEMA, rec)[0] == k:
                        break

        btree_pt = _timed(btree_points)
        hash_pt = _timed(hash_points)
        heap_pt = _timed(heap_points)

        # --- range scan (B+ tree only) ---
        def btree_range() -> None:
            for _ in range(100):
                list(btree.range_scan(N // 4, N // 4 + 500))

        btree_rng = _timed(btree_range)

        # --- report ---
        print(f"\nQueryX Phase 3 index benchmark  (N={N:,} rows, pool={POOL} pages)\n")
        print(f"{'operation':<26}{'B+ tree':>16}{'hash index':>16}{'heap seqscan':>16}")
        print("-" * 74)
        print(f"{'bulk load (insert)':<26}{_rate(N, btree_load):>16}"
              f"{_rate(N, hash_load):>16}{_rate(N, heap_load):>16}")
        print(f"{'point lookup':<26}{_rate(LOOKUPS, btree_pt):>16}"
              f"{_rate(LOOKUPS, hash_pt):>16}{_rate(HEAP_LOOKUPS, heap_pt):>16}")
        print(f"{'range scan [500 keys]':<26}{_rate(100, btree_rng):>16}"
              f"{'unsupported':>16}{'(via seqscan)':>16}")
        print("-" * 74)

        speedup = (heap_pt / HEAP_LOOKUPS) / (hash_pt / LOOKUPS) if hash_pt else 0
        print(f"\nPer-lookup: hash is ~{speedup:,.0f}x faster than a heap seq scan "
              f"at N={N:,}.")
        print("Range scan: the hash index has no ordering and cannot do it at all.\n")

        for p in (btree_pager, hash_pager, heap_pager):
            p.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
