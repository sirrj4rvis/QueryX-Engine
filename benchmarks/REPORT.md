# QueryX Benchmark Report

Workload: **N = 20,000** rows. In-process microbenchmarks with a warm
buffer pool and no per-operation fsync — they show *relative* algorithmic
behavior, not production latencies. Regenerate with
`python benchmarks/benchmark_suite.py`.

## Insert throughput (ops/s)

| variant | ops/s |
|---|---:|
| heap | 36,074 |
| btree | 3,655 |
| hash | 12,022 |

## Point lookup (ops/s)

| variant | ops/s |
|---|---:|
| seqscan | 29 |
| btree | 2,769 |
| hash | 9,162 |

## Range scan, 500-key window (ops/s)

| variant | ops/s |
|---|---:|
| seqscan | 10 |
| btree | 373 |

## WAL overhead - page writes (ops/s)

| variant | ops/s |
|---|---:|
| wal_off | 15,937 |
| wal_on | 7,785 |

**WAL overhead:** page writes are ~2.0x slower with logging on (15,937 -> 7,785 ops/s) — the price of crash durability.

## Takeaways

- A hash point lookup is ~**315x** faster than a sequential scan at this size.
- The B+ tree trails hash on point lookups but is the only index that range-scans.
- Indexes cost insert throughput to maintain; the WAL costs more, for durability.

## Charts

![benchmarks](output/benchmarks.png)
