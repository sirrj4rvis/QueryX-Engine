"""SQL layer (Phase 4) — turn SQL text into a structured AST.

This is the top of the stack: it converts a raw SQL string into a tree of
typed nodes that the rest of the engine can reason about. It does NOT execute
anything or decide how to run a query — it only captures *what was asked*.

Modules (built in Phase 4):
    tokens.py   The token types produced by the lexer (keywords, identifiers,
                literals, operators, punctuation).
    lexer.py    The lexer (tokenizer): scans the input string character by
                character and emits a flat stream of tokens, skipping
                whitespace and recognizing keywords vs. identifiers.
    ast.py      The Abstract Syntax Tree node classes — CreateTable, Insert,
                Select, Update, Delete, predicate/expression nodes, etc. These
                are plain data structures with no behavior.
    parser.py   A recursive-descent parser: consumes the token stream and
                builds an AST according to QueryX's SQL grammar (documented in
                BNF in DESIGN.md). One parse method per grammar rule.

Supported SQL is a deliberate, focused subset (see CLAUDE.md "SQL feature
scope"), fully integrated through the real pipeline — never string-matched.

Implemented in Phase 4. No logic yet.
"""
