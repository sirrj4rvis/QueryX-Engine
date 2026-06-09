# QueryX Benchmark Report

Workload: **N = 20,000** rows. In-process microbenchmarks with a warm
buffer pool and no per-operation fsync — they show *relative* algorithmic
behavior, not production latencies. Regenerate with
`python benchmarks/benchmark_suite.py`.

## Insert throughput (ops/s)

| variant | ops/s |
|---|---:|
| heap | 33,013 |
| btree | 7,759 |
| hash | 37,306 |

## Point lookup (ops/s)

| variant | ops/s |
|---|---:|
| seqscan | 77 |
| btree | 8,459 |
| hash | 21,648 |

## Range scan, 500-key window (ops/s)

| variant | ops/s |
|---|---:|
| seqscan | 34 |
| btree | 1,350 |

## WAL overhead - page writes (ops/s)

| variant | ops/s |
|---|---:|
| wal_off | 70,113 |
| wal_on | 25,606 |

**WAL overhead:** page writes are ~2.7x slower with logging on (70,113 -> 25,606 ops/s) — the price of crash durability.

## Takeaways

- A hash point lookup is ~**280x** faster than a sequential scan at this size.
- The B+ tree trails hash on point lookups but is the only index that range-scans.
- Indexes cost insert throughput to maintain; the WAL costs more, for durability.

## Charts

![benchmarks](output/benchmarks.png)
