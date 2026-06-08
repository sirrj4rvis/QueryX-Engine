"""The lexer (tokenizer) — SQL text to a flat token stream.

The lexer scans the input string left to right, one character at a time, and
groups characters into tokens: it skips whitespace, reads a run of letters and
decides whether it is a keyword or an identifier, reads digits into a number
literal, reads quoted text into a string literal, and matches operators
(including multi-character ones like <=, >=, !=, <>). Its output is a list (or
generator) of Tokens defined in tokens.py.

Lexing is intentionally "dumb": it knows nothing about grammar, only about what
a single token looks like. All structure is the parser's job. This separation is
standard compiler design and keeps both halves simple and testable.

Implemented in Phase 4. No logic yet.
"""
