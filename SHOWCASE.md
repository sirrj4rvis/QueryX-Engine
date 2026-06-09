# QueryX — Showcase & Polish Prompt (FINAL, for Claude Code)

## Context

I have a COMPLETE, WORKING database engine: **QueryX: A Relational Database Engine Built from Scratch in Python**. Read CLAUDE.md for full project context. Implemented and frozen: page-based storage, heap files, slotted-page records, buffer pool with LRU eviction, disk-backed B+ tree, hash index, SQL lexer + recursive-descent parser, volcano execution engine, cost-based optimizer with EXPLAIN, write-ahead logging, crash recovery, and a benchmark suite.

This is my final-year major project and the engine is done. **I am NOT asking for new database features or changes to engine internals.** My goal is to make QueryX a polished, demo-ready project that impresses major-project professors, external examiners, recruiters, and database engineers — so it reads as a miniature SQLite/PostgreSQL, not a student assignment. The work is presentation, demos, and documentation only.

## Hard rules

1. **Engine is frozen.** Do not modify storage, indexing, parsing, execution, optimizer, WAL, or recovery logic — not even to make a demo work. If any showcase item appears to need an engine, schema, metric, or behavior change, STOP, name the exact files involved, explain why, and wait for my decision. Never expand scope implicitly.
2. **Audit before building.** Read my actual code and report what genuinely exists before building anything on top of it (see Item 0). I will only present numbers and behaviors my engine actually produces.
3. **Minimal, isolated edits.** Prefer focused incremental changes over rewriting files. Preserve existing functionality, comments, tests, and behavior. Before any edit, state which files change, why, the expected outcome, and whether existing behavior could be affected — then wait for my confirmation.
4. **One item at a time.** Complete one item fully — explain → (on my OK) edit → run the existing test suite → verify the affected behavior manually → summarize what was validated → give a concise commit message → STOP for my approval. Never batch items.
5. **Honesty and defensibility over polish.** Never fake a feature or make a claim not traceable to the code. Do not mass-generate interview answers, scripted algorithm explanations, or documentation I couldn't defend in a viva. If a statement isn't supported by the implementation, mark it speculation and keep it out of showcase material. A flagged limitation beats a demo that breaks or a claim I can't defend.
6. **Authentic to a database, not a web app.** Polish the terminal/REPL and docs. No web dashboard, no animated GUI — a clean shell reads as "engineer," a dashboard reads as "frontend project in a database costume." Prefer terminal demos, static ASCII diagrams, reproducible benchmarks, and documentation over cosmetic effects; when options exist, pick the one a database engineer reviewing the repo would most respect.
7. **Teach as you go.** For each item, briefly explain what you added and how it works so I can defend it under questioning.

## Work plan — in order, gated on my confirmation between items

### Item 0 — Audit (FIRST, no code)
Read the code and report, with file/line evidence:
- Does EXPLAIN output estimated cost only, or also actual cost / rows / pages touched?
- Are buffer-pool hit/miss counts or a hit ratio tracked anywhere?
- Does crash recovery handle only a clean process kill (redo-replay), or also a corrupted/torn page — and is there a test proving the corrupted-page case?
- What does the WAL write per mutation, and what does replay do on startup?
- What metrics does the benchmark suite produce, and in what format?

Then list every item below and classify each as: **Documentation-only / Demo-only / REPL-UI-only / Build-CI-only / Potential-engine-impact (flagged)**, and mark whether it uses **only existing data** or **needs new engine instrumentation (flagged)**. Also identify: files safe to modify, files to leave untouched (engine internals), docs needing updates, and which existing tests validate each item. NOTE: any dot-command named in later items (e.g. `.stats`, `.pages`) is illustrative only — build it ONLY if the audit confirms the underlying data already exists; otherwise tell me and skip it. No implementation begins until I confirm this audit.

### Item 1 — Crash-recovery demo (highest priority)
A single-command, narrated demo proving durability: insert rows, show the WAL growing, forcefully kill the process, restart, replay, show the data survived. Use the recovery path the engine actually supports — if recovery only handles a clean kill, build the clean-kill version and do NOT stage a page-corruption scenario I can't reliably reproduce. **The demo MUST run twice in a row with identical results — this is non-negotiable; a demo that fails on the re-run in front of an examiner undoes everything.** Provide the script, expected terminal output, and a short narration/talking-points block.

### Item 2 — REPL / shell polish (presentation layer only)
First identify the exact REPL entrypoint and output-formatting code. Then, touching presentation code ONLY (never execution/planning/optimizer/storage/index/recovery/timing logic): a startup banner (name + version); results as clean aligned boxed ASCII tables with a row-count + timing footer; EXPLAIN rendered as an indented plan tree (not a raw dict); a `.help` command. Add dot-commands such as `.stats` (buffer-pool hit ratio) or `.pages` (slotted-page layout) ONLY if the audit confirmed those numbers already exist — otherwise skip and tell me.

### Item 3 — Index-vs-sequential-scan speed demo
A narrated demo using existing EXPLAIN + timing only: query an unindexed column (EXPLAIN shows sequential scan), CREATE INDEX, rerun (EXPLAIN shows index scan), show the measured time difference. Provide script, expected output, talking points.

### Item 4 — README glow-up
Rewrite README.md to look internship/job-ready: one-line description; a **Mermaid** architecture diagram and a Mermaid query-lifecycle diagram; badges (tests, Python version, zero runtime dependencies, license); a feature matrix; a "How QueryX compares to SQLite/PostgreSQL" table; a 3-command quickstart; placeholders for the demo GIF and benchmark chart; and a Future Work section listing deliberately-deferred features (transactions, MVCC, joins, subqueries, etc.) framed as scope decisions. Every claim must be traceable to the code or audit. **All SQLite/PostgreSQL comparisons must be framed as architectural comparisons, never capability/parity comparisons; where QueryX simplifies a concept, state the simplification plainly.**

### Item 5 — Static B+ tree view (optional; only after Items 1–4)
A strictly read-only `.tree` dot-command printing the current B+ tree as static ASCII (nodes, keys, leaf links). It may inspect existing tree state but must NOT add instrumentation, metadata, persistence changes, or index-internal modifications for visualization. No animation.

### Item 6 — CI + LICENSE + hygiene
A GitHub Actions workflow running the existing pytest suite on push (green badge); a LICENSE file (ask me which license first); a .gitignore review; a cleanup pass. Before flagging any file for deletion, show evidence it isn't referenced by application code, tests, benchmarks, docs, build scripts, or demos — and prefer moving questionable files to a review list over deleting them.

### Item 7 — PRESENTATION.md (viva runbook, NOT answers)
A presentation story arc; the exact command runbook for the demos in order; wow-moment cues; and audience-tailoring notes (database engineer / recruiter / general professor). Write structure, sequencing, and discussion prompts ONLY. Do NOT write canned technical answers, scripted algorithm explanations, or cramming material — I will prepare those from my own understanding. The goal is understanding, not memorization.

## Success criteria
Done when: every showcased item is demonstrably backed by the existing code; no engine internals were changed for presentation; every demo runs repeatedly without failure; the README accurately reflects what's implemented and contains no misleading claims; the test suite passes after every item; each change is isolated and committed separately; a database engineer would view this as a serious educational engine, not a CRUD project; a professor can trace any showcased feature to real implementation evidence; and I can explain every demonstrated feature and design decision without relying on generated answers. When in doubt, choose correctness, reproducibility, and credibility over more features or polish.

## Start now with Item 0 (the audit). Write no showcase code until I confirm the audit results.