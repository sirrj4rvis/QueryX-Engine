"""Recursive-descent parser — token stream to AST.

The parser consumes the lexer's tokens and builds an AST according to QueryX's
SQL grammar (documented in BNF in DESIGN.md). It is *recursive descent*: roughly
one method per grammar rule, and rules that reference other rules call the
corresponding methods, so the code mirrors the grammar's structure directly.

Operator precedence in WHERE predicates is encoded by LAYERING the expression
methods, lowest-precedence first: OR binds loosest, then AND, then NOT, then the
comparison operators, then primaries (literals, columns, parenthesized
expressions). Each layer only climbs to the next-tighter one, so
`a = 1 OR b = 2 AND NOT c = 3` parses as `a=1 OR (b=2 AND (NOT c=3))` with no
precedence table needed.

On malformed input the parser raises SQLSyntaxError pointing at the offending
token. It parses exactly one statement (an optional trailing semicolon is
allowed) and then expects end-of-input.

Complexity: O(n) in the number of tokens — each token is consumed once, with
bounded lookahead.
"""

from __future__ import annotations

from typing import Optional

from queryx.sql import ast
from queryx.sql.lexer import tokenize
from queryx.sql.tokens import SQLSyntaxError, Token, TokenType
from queryx.storage.page import ColumnType

_COMPARISONS = (
    TokenType.EQ, TokenType.NEQ, TokenType.LT,
    TokenType.GT, TokenType.LTE, TokenType.GTE,
)
_AGGREGATES = {
    TokenType.COUNT: "COUNT", TokenType.SUM: "SUM", TokenType.AVG: "AVG",
    TokenType.MIN: "MIN", TokenType.MAX: "MAX",
}


class Parser:
    """Builds an AST from a token list produced by the lexer."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    # -- token cursor -------------------------------------------------------

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        token = self._tokens[self._pos]
        if token.type != TokenType.EOF:
            self._pos += 1
        return token

    def _check(self, type_: TokenType) -> bool:
        return self._peek().type == type_

    def _match(self, *types: TokenType) -> Optional[Token]:
        """Consume and return the next token if it is one of ``types``, else None."""
        if self._peek().type in types:
            return self._advance()
        return None

    def _expect(self, type_: TokenType, what: str) -> Token:
        if self._check(type_):
            return self._advance()
        raise self._error(f"expected {what}")

    def _error(self, message: str) -> SQLSyntaxError:
        token = self._peek()
        near = token.lexeme if token.type != TokenType.EOF else "end of input"
        return SQLSyntaxError(f"{message}, found {near!r}", token.position)

    def _identifier(self, what: str = "an identifier") -> str:
        return self._expect(TokenType.IDENT, what).lexeme

    # -- entry point --------------------------------------------------------

    def parse(self) -> ast.Statement:
        statement = self._statement()
        self._match(TokenType.SEMICOLON)  # optional trailing ;
        if not self._check(TokenType.EOF):
            raise self._error("unexpected tokens after statement")
        return statement

    def _statement(self) -> ast.Statement:
        type_ = self._peek().type
        if type_ == TokenType.SELECT:
            return self._select()
        if type_ == TokenType.INSERT:
            return self._insert()
        if type_ == TokenType.CREATE:
            return self._create()
        if type_ == TokenType.DROP:
            return self._drop()
        if type_ == TokenType.UPDATE:
            return self._update()
        if type_ == TokenType.DELETE:
            return self._delete()
        raise self._error("expected a statement (SELECT/INSERT/UPDATE/DELETE/CREATE/DROP)")

    # -- SELECT -------------------------------------------------------------

    def _select(self) -> ast.Select:
        self._expect(TokenType.SELECT, "SELECT")
        distinct = self._match(TokenType.DISTINCT) is not None
        projections = self._select_list()
        self._expect(TokenType.FROM, "FROM")
        table = self._identifier("a table name")
        where = self._optional_where()
        order_by = self._optional_order_by()
        limit = self._optional_limit()
        return ast.Select(
            table=table, projections=projections, distinct=distinct,
            where=where, order_by=order_by, limit=limit,
        )

    def _select_list(self) -> list[ast.Expr]:
        items = [self._select_item()]
        while self._match(TokenType.COMMA):
            items.append(self._select_item())
        return items

    def _select_item(self) -> ast.Expr:
        if self._match(TokenType.STAR):
            return ast.Star()
        if self._peek().type in _AGGREGATES:
            return self._aggregate()
        return ast.Column(self._identifier("a column name"))

    def _aggregate(self) -> ast.Aggregate:
        func = _AGGREGATES[self._advance().type]
        self._expect(TokenType.LPAREN, "'(' after aggregate function")
        if func == "COUNT" and self._match(TokenType.STAR):
            arg: ast.Expr = ast.Star()
        else:
            arg = ast.Column(self._identifier("a column name inside the aggregate"))
        self._expect(TokenType.RPAREN, "')' to close the aggregate")
        return ast.Aggregate(func=func, arg=arg)

    def _optional_where(self) -> Optional[ast.Expr]:
        if self._match(TokenType.WHERE):
            return self._expression()
        return None

    def _optional_order_by(self) -> Optional[list[ast.OrderItem]]:
        if not self._match(TokenType.ORDER):
            return None
        self._expect(TokenType.BY, "BY after ORDER")
        items = [self._order_item()]
        while self._match(TokenType.COMMA):
            items.append(self._order_item())
        return items

    def _order_item(self) -> ast.OrderItem:
        column = self._identifier("a column name in ORDER BY")
        descending = False
        if self._match(TokenType.DESC):
            descending = True
        else:
            self._match(TokenType.ASC)  # optional, the default
        return ast.OrderItem(column=column, descending=descending)

    def _optional_limit(self) -> Optional[int]:
        if self._match(TokenType.LIMIT):
            token = self._expect(TokenType.NUMBER, "a number after LIMIT")
            return int(token.value)  # type: ignore[arg-type]
        return None

    # -- expressions (precedence climbing via layered methods) --------------

    def _expression(self) -> ast.Expr:
        return self._or_expr()

    def _or_expr(self) -> ast.Expr:
        left = self._and_expr()
        while self._match(TokenType.OR):
            left = ast.Or(left, self._and_expr())
        return left

    def _and_expr(self) -> ast.Expr:
        left = self._not_expr()
        while self._match(TokenType.AND):
            left = ast.And(left, self._not_expr())
        return left

    def _not_expr(self) -> ast.Expr:
        if self._match(TokenType.NOT):
            return ast.Not(self._not_expr())  # allow NOT NOT ...
        return self._comparison()

    def _comparison(self) -> ast.Expr:
        left = self._primary()
        op = self._match(*_COMPARISONS)
        if op is not None:
            right = self._primary()
            return ast.Comparison(op=op.type, left=left, right=right)
        return left

    def _primary(self) -> ast.Expr:
        if self._match(TokenType.LPAREN):
            inner = self._expression()
            self._expect(TokenType.RPAREN, "')' to close a parenthesized expression")
            return inner
        return self._literal_or_column()

    def _literal_or_column(self) -> ast.Expr:
        # optional unary minus on a number literal
        negate = self._match(TokenType.MINUS) is not None
        if self._check(TokenType.NUMBER):
            value = int(self._advance().value)  # type: ignore[arg-type]
            return ast.Literal(value=-value if negate else value, type=ColumnType.INT)
        if negate:
            raise self._error("expected a number after '-'")
        if self._check(TokenType.STRING):
            return ast.Literal(value=self._advance().value, type=ColumnType.TEXT)
        if self._check(TokenType.IDENT):
            return ast.Column(self._advance().lexeme)
        raise self._error("expected a column, number, or string")

    # -- INSERT -------------------------------------------------------------

    def _insert(self) -> ast.Insert:
        self._expect(TokenType.INSERT, "INSERT")
        self._expect(TokenType.INTO, "INTO")
        table = self._identifier("a table name")
        columns: Optional[list[str]] = None
        if self._match(TokenType.LPAREN):
            columns = [self._identifier("a column name")]
            while self._match(TokenType.COMMA):
                columns.append(self._identifier("a column name"))
            self._expect(TokenType.RPAREN, "')' after the column list")
        self._expect(TokenType.VALUES, "VALUES")
        self._expect(TokenType.LPAREN, "'(' before the value list")
        values = [self._value()]
        while self._match(TokenType.COMMA):
            values.append(self._value())
        self._expect(TokenType.RPAREN, "')' after the value list")
        return ast.Insert(table=table, columns=columns, values=values)

    def _value(self) -> ast.Literal:
        """A literal value (number or string), with optional unary minus."""
        negate = self._match(TokenType.MINUS) is not None
        if self._check(TokenType.NUMBER):
            value = int(self._advance().value)  # type: ignore[arg-type]
            return ast.Literal(value=-value if negate else value, type=ColumnType.INT)
        if negate:
            raise self._error("expected a number after '-'")
        if self._check(TokenType.STRING):
            return ast.Literal(value=self._advance().value, type=ColumnType.TEXT)
        raise self._error("expected a literal value")

    # -- UPDATE / DELETE ----------------------------------------------------

    def _update(self) -> ast.Update:
        self._expect(TokenType.UPDATE, "UPDATE")
        table = self._identifier("a table name")
        self._expect(TokenType.SET, "SET")
        assignments = [self._assignment()]
        while self._match(TokenType.COMMA):
            assignments.append(self._assignment())
        where = self._optional_where()
        return ast.Update(table=table, assignments=assignments, where=where)

    def _assignment(self) -> ast.Assignment:
        column = self._identifier("a column name")
        self._expect(TokenType.EQ, "'=' in assignment")
        return ast.Assignment(column=column, value=self._value())

    def _delete(self) -> ast.Delete:
        self._expect(TokenType.DELETE, "DELETE")
        self._expect(TokenType.FROM, "FROM")
        table = self._identifier("a table name")
        where = self._optional_where()
        return ast.Delete(table=table, where=where)

    # -- CREATE / DROP ------------------------------------------------------

    def _create(self) -> ast.Statement:
        self._expect(TokenType.CREATE, "CREATE")
        if self._match(TokenType.TABLE):
            return self._create_table()
        if self._match(TokenType.INDEX):
            return self._create_index()
        raise self._error("expected TABLE or INDEX after CREATE")

    def _create_table(self) -> ast.CreateTable:
        table = self._identifier("a table name")
        self._expect(TokenType.LPAREN, "'(' before the column definitions")
        columns = [self._column_def()]
        while self._match(TokenType.COMMA):
            columns.append(self._column_def())
        self._expect(TokenType.RPAREN, "')' after the column definitions")
        return ast.CreateTable(table=table, columns=columns)

    def _column_def(self) -> ast.ColumnDef:
        name = self._identifier("a column name")
        if self._match(TokenType.INT):
            col_type = ColumnType.INT
        elif self._match(TokenType.TEXT):
            col_type = ColumnType.TEXT
        else:
            raise self._error("expected a column type (INT or TEXT)")
        return ast.ColumnDef(name=name, type=col_type)

    def _create_index(self) -> ast.CreateIndex:
        name = self._identifier("an index name")
        self._expect(TokenType.ON, "ON")
        table = self._identifier("a table name")
        self._expect(TokenType.LPAREN, "'(' before the indexed column")
        column = self._identifier("a column name")
        self._expect(TokenType.RPAREN, "')' after the indexed column")
        return ast.CreateIndex(name=name, table=table, column=column)

    def _drop(self) -> ast.Statement:
        self._expect(TokenType.DROP, "DROP")
        if self._match(TokenType.TABLE):
            return ast.DropTable(table=self._identifier("a table name"))
        if self._match(TokenType.INDEX):
            return ast.DropIndex(name=self._identifier("an index name"))
        raise self._error("expected TABLE or INDEX after DROP")


def parse(sql: str) -> ast.Statement:
    """Lex and parse ``sql`` into a single statement AST."""
    return Parser(tokenize(sql)).parse()
