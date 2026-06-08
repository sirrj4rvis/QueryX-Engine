"""Token definitions for the SQL lexer.

A *token* is the smallest meaningful unit of SQL text: a keyword (SELECT, FROM),
an identifier (a table or column name), a literal (42, 'alice'), an operator
(=, <, >=), or punctuation (comma, parenthesis). This module will define the
token kinds (likely an enum) and a small Token record carrying its kind, its
literal text, and its position in the source (for error messages).

Keeping token definitions separate from the lexer lets both the lexer (which
produces tokens) and the parser (which consumes them) depend on one shared
vocabulary without depending on each other's logic.

Implemented in Phase 4. No logic yet.
"""
