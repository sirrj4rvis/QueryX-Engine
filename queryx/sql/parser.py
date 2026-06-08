"""Recursive-descent parser — token stream to AST.

The parser consumes the lexer's tokens and builds the AST according to QueryX's
SQL grammar. It is *recursive descent*: there is roughly one method per grammar
rule, and rules that refer to other rules call the corresponding methods,
matching the grammar's structure directly in code. Operator precedence (e.g.
AND binds tighter than OR, comparisons tighter than AND) is handled by layering
the expression-parsing methods.

The grammar QueryX accepts is documented in BNF in DESIGN.md; this module is its
executable form. On malformed input the parser raises a clear syntax error
pointing at the offending token rather than failing cryptically.

Implemented in Phase 4. No logic yet.
"""
