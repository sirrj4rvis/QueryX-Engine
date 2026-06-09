"""Phase 8 — the QueryX benchmark suite.

Measures the four things QueryX has been claiming, so the claims become numbers:

  1. Insert throughput   — heap vs B+ tree vs hash (index maintenance cost)
  2. Point lookup        — SeqScan vs B+ tree vs hash (the index payoff)
  3. Range scan          — SeqScan vs B+ tree (hash cannot range)
  4. WAL overhead        — Database inserts with logging on vs off (durability tax)

It prints a table, writes a Markdown report (benchmarks/REPORT.md), and renders
bar charts (benchmarks/output/*.png) when matplotlib is available — degrading to
a text-only report if it is not.

HONESTY: these are in-process microbenchmarks (one machine, warm buffer pool,
small data, no per-op fsync). They show RELATIVE algorithmic behavior, not
production latencies. Standard library only, except matplotlib for the charts.

Run:  python benchmarks/benchmark_suite.py [--n 20000]
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import tempfile
import time
from pathlib import Path

from queryx.index.btree import BPlusTree
from queryx.index.hash_index import HashIndex
from queryx.storage.buffer_pool import BufferPool
from queryx.storage.heap_file import HeapFile, RowId
from queryx.storage.page import ColumnType, Page, deserialize_row, serialize_row
from queryx.storage.pager import Pager

SCHEMA = [ColumnType.INT, ColumnType.TEXT]
_POOL = 256


def _timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def _rate(count: int, seconds: float) -> float:
    return count / seconds if seconds > 0 else 0.0


def run_benchmarks(n: int = 20_000, seed: int = 7) -> dict:
    """Run all benchmarks against a temp workdir and return ops/s results."""
    workdir = Path(tempfile.mkdtemp(prefix="queryx_bench_"))
    pagers: list[Pager] = []
    try:
        keys = list(range(n))
        random.Random(seed).shuffle(keys)
        lookups = [random.Random(seed + 1).randint(0, n - 1) for _ in range(2_000)]

        def open_pager(name: str, use_wal: bool = False) -> Pager:
            p = Pager(str(workdir / name), use_wal=use_wal)
            pagers.append(p)
            return p

        # --- build the three structures, timing inserts (WAL off: structure cost) ---
        heap = HeapFile(BufferPool(open_pager("heap.qx"), capacity=_POOL))
        btree = BPlusTree(BufferPool(open_pager("btree.qx"), capacity=_POOL))
        hidx = HashIndex(BufferPool(open_pager("hash.qx"), capacity=_POOL), num_buckets=256)

        rids: dict[int, RowId] = {}

        def load_heap() -> None:
            for k in keys:
                rids[k] = heap.insert(serialize_row(SCHEMA, (k, f"n{k}")))

        insert = {
            "heap": _rate(n, _timed(load_heap)),
            "btree": _rate(n, _timed(lambda: [btree.insert(k, rids[k]) for k in keys])),
            "hash": _rate(n, _timed(lambda: [hidx.insert(k, rids[k]) for k in keys])),
        }

        # --- point lookup: seqscan does far fewer probes (each scans the table) ---
        heap_probes = 100

        def seqscan_points() -> None:
            for k in lookups[:heap_probes]:
                for _rid, rec in heap.scan():
                    if deserialize_row(SCHEMA, rec)[0] == k:
                        break

        point = {
            "seqscan": _rate(heap_probes, _timed(seqscan_points)),
            "btree": _rate(len(lookups), _timed(lambda: [btree.search(k) for k in lookups])),
            "hash": _rate(len(lookups), _timed(lambda: [hidx.search(k) for k in lookups])),
        }

        # --- range scan over a fixed window: B+ tree streams leaves; seqscan filters all ---
        reps, lo, width = 100, n // 4, 500
        hi = lo + width

        def seqscan_range() -> None:
            for _ in range(reps):
                [rec for _rid, rec in heap.scan()
                 if lo <= deserialize_row(SCHEMA, rec)[0] <= hi]

        rng = {
            "seqscan": _rate(reps, _timed(seqscan_range)),
            "btree": _rate(reps, _timed(lambda: [list(btree.range_scan(lo, hi)) for _ in range(reps)])),
        }

        # --- WAL overhead: isolate the write path — time raw page writes on vs off.
        # (Measured at the pager, where the WAL acts, not at the Database level
        # where per-statement catalog writes would swamp the signal.)
        page_writes = min(10_000, n)
        wal = {
            "wal_off": _measure_page_writes(workdir / "pw_off.qx", page_writes, use_wal=False),
            "wal_on": _measure_page_writes(workdir / "pw_on.qx", page_writes, use_wal=True),
        }

        return {"n": n, "insert": insert, "point_lookup": point, "range_scan": rng, "wal": wal}
    finally:
        for p in pagers:
            p.close()
        shutil.rmtree(workdir, ignore_errors=True)


def _measure_page_writes(path: Path, m: int, use_wal: bool) -> float:
    """Time m page writes through the pager, with the WAL on or off.

    Isolates the durability tax on the write path: WAL-on logs each page image
    (flush) and fsyncs at checkpoints; WAL-off only flushes the data page.
    """
    pager = Pager(str(path), use_wal=use_wal)
    try:
        n = pager.allocate_page()
        page = Page.empty()
        page.insert_record(b"x" * 64)
        seconds = _timed(lambda: [pager.write_page(n, page) for _ in range(m)])
        return _rate(m, seconds)
    finally:
        pager.close()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_CATEGORIES = [
    ("insert", "Insert throughput (ops/s)"),
    ("point_lookup", "Point lookup (ops/s)"),
    ("range_scan", "Range scan, 500-key window (ops/s)"),
    ("wal", "WAL overhead - page writes (ops/s)"),
]


def _fmt(v: float) -> str:
    return f"{v:,.0f}"


def print_report(results: dict) -> None:
    print(f"\nQueryX benchmark suite  (N={results['n']:,} rows, warm pool, no per-op fsync)\n")
    for key, title in _CATEGORIES:
        print(title)
        for label, value in results[key].items():
            print(f"    {label:<10} {_fmt(value):>14} ops/s")
        print()
    wal = results["wal"]
    if wal["wal_on"] and wal["wal_off"]:
        slowdown = wal["wal_off"] / wal["wal_on"]
        print(f"WAL makes page writes ~{slowdown:.1f}x slower "
              f"({_fmt(wal['wal_off'])} -> {_fmt(wal['wal_on'])} ops/s) - the cost of crash durability.")
    speedup = results["point_lookup"]["hash"] / max(results["point_lookup"]["seqscan"], 1e-9)
    print(f"Hash point lookup is ~{speedup:,.0f}x a sequential scan at N={results['n']:,}.\n")


def make_charts(results: dict, outdir: Path) -> bool:
    """Render bar charts to outdir; return False if matplotlib is unavailable."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    outdir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    fig.suptitle(f"QueryX Benchmark Suite (N={results['n']:,})", fontsize=14, fontweight="bold")
    for ax, (key, title) in zip(axes.flat, _CATEGORIES):
        data = results[key]
        labels = list(data.keys())
        values = list(data.values())
        bars = ax.bar(labels, values, color=["#4C72B0", "#55A868", "#C44E52", "#8172B3"][: len(labels)])
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("ops/s")
        for bar, v in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, v, _fmt(v), ha="center", va="bottom", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(outdir / "benchmarks.png", dpi=110)
    plt.close(fig)
    return True


def write_report(results: dict, outdir: Path, charts: bool) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# QueryX Benchmark Report",
        "",
        f"Workload: **N = {results['n']:,}** rows. In-process microbenchmarks with a warm",
        "buffer pool and no per-operation fsync — they show *relative* algorithmic",
        "behavior, not production latencies. Regenerate with",
        "`python benchmarks/benchmark_suite.py`.",
        "",
    ]
    for key, title in _CATEGORIES:
        lines += [f"## {title}", "", "| variant | ops/s |", "|---|---:|"]
        lines += [f"| {label} | {_fmt(v)} |" for label, v in results[key].items()]
        lines.append("")
    wal = results["wal"]
    if wal["wal_on"] and wal["wal_off"]:
        slowdown = wal["wal_off"] / wal["wal_on"]
        lines += [f"**WAL overhead:** page writes are ~{slowdown:.1f}x slower with logging on "
                  f"({_fmt(wal['wal_off'])} -> {_fmt(wal['wal_on'])} ops/s) — the price of crash durability.", ""]
    speedup = results["point_lookup"]["hash"] / max(results["point_lookup"]["seqscan"], 1e-9)
    lines += [
        "## Takeaways",
        "",
        f"- A hash point lookup is ~**{speedup:,.0f}x** faster than a sequential scan at this size.",
        "- The B+ tree trails hash on point lookups but is the only index that range-scans.",
        "- Indexes cost insert throughput to maintain; the WAL costs more, for durability.",
        "",
    ]
    if charts:
        lines += ["## Charts", "", "![benchmarks](output/benchmarks.png)", ""]
    (outdir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="QueryX benchmark suite")
    parser.add_argument("--n", type=int, default=20_000, help="rows/keys to load")
    args = parser.parse_args()

    results = run_benchmarks(n=args.n)
    print_report(results)

    bench_dir = Path(__file__).resolve().parent
    charts = make_charts(results, bench_dir / "output")
    write_report(results, bench_dir, charts)
    print(f"Report written to {bench_dir / 'REPORT.md'}"
          + (f"; charts in {bench_dir / 'output' / 'benchmarks.png'}" if charts
             else " (matplotlib not installed — charts skipped)"))


if __name__ == "__main__":
    main()
