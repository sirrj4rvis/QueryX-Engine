"""The lexer (tokenizer) — SQL text to a flat token stream.

The lexer scans the input left to right, one character at a time, and groups
characters into tokens: it skips whitespace and `-- line comments`, reads a run
of letters/digits/underscores and decides keyword vs. identifier, reads digits
into a NUMBER, reads single-quoted text into a STRING (with '' as an escaped
quote), and matches operators including the multi-character <=, >=, <>, !=.

Lexing is intentionally "dumb": it knows what a single token looks like, not how
tokens combine — all structure is the parser's job. This separation keeps both
halves simple and independently testable. The output always ends with an EOF
token so the parser has a definite stopping point.

Complexity: O(n) in the length of the input — each character is examined a
constant number of times.
"""

from __future__ import annotations

from .tokens import KEYWORDS, SQLSyntaxError, Token, TokenType


class Lexer:
    """Turns a SQL string into a list of Tokens (ending with EOF)."""

    def __init__(self, source: str) -> None:
        self._src = source
        self._pos = 0
        self._tokens: list[Token] = []

    def tokenize(self) -> list[Token]:
        while not self._at_end():
            self._skip_trivia()
            if self._at_end():
                break
            self._scan_token()
        self._tokens.append(Token(TokenType.EOF, "", self._pos))
        return self._tokens

    # -- helpers ------------------------------------------------------------

    def _at_end(self) -> bool:
        return self._pos >= len(self._src)

    def _peek(self, ahead: int = 0) -> str:
        i = self._pos + ahead
        return self._src[i] if i < len(self._src) else ""

    def _advance(self) -> str:
        ch = self._src[self._pos]
        self._pos += 1
        return ch

    def _add(self, type_: TokenType, lexeme: str, start: int, value: object = None) -> None:
        self._tokens.append(Token(type_, lexeme, start, value))

    def _skip_trivia(self) -> None:
        """Skip whitespace and -- to-end-of-line comments."""
        while not self._at_end():
            ch = self._peek()
            if ch in " \t\r\n":
                self._pos += 1
            elif ch == "-" and self._peek(1) == "-":
                while not self._at_end() and self._peek() != "\n":
                    self._pos += 1
            else:
                break

    # -- token scanning -----------------------------------------------------

    def _scan_token(self) -> None:
        start = self._pos
        ch = self._peek()

        if ch.isalpha() or ch == "_":
            self._scan_word(start)
        elif ch.isdigit():
            self._scan_number(start)
        elif ch == "'":
            self._scan_string(start)
        else:
            self._scan_symbol(start)

    def _scan_word(self, start: int) -> None:
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            self._pos += 1
        lexeme = self._src[start:self._pos]
        type_ = KEYWORDS.get(lexeme.upper(), TokenType.IDENT)
        self._add(type_, lexeme, start)

    def _scan_number(self, start: int) -> None:
        while not self._at_end() and self._peek().isdigit():
            self._pos += 1
        # Reject a number immediately glued to an identifier (e.g. 12abc).
        if not self._at_end() and (self._peek().isalpha() or self._peek() == "_"):
            raise SQLSyntaxError(f"invalid number literal near {self._src[start:self._pos + 1]!r}", start)
        lexeme = self._src[start:self._pos]
        self._add(TokenType.NUMBER, lexeme, start, int(lexeme))

    def _scan_string(self, start: int) -> None:
        self._advance()  # opening quote
        chars: list[str] = []
        while True:
            if self._at_end():
                raise SQLSyntaxError("unterminated string literal", start)
            ch = self._advance()
            if ch == "'":
                if self._peek() == "'":  # '' is an escaped single quote
                    self._advance()
                    chars.append("'")
                else:
                    break  # closing quote
            else:
                chars.append(ch)
        lexeme = self._src[start:self._pos]
        self._add(TokenType.STRING, lexeme, start, "".join(chars))

    def _scan_symbol(self, start: int) -> None:
        ch = self._advance()
        if ch == ",":
            self._add(TokenType.COMMA, ch, start)
        elif ch == "(":
            self._add(TokenType.LPAREN, ch, start)
        elif ch == ")":
            self._add(TokenType.RPAREN, ch, start)
        elif ch == ";":
            self._add(TokenType.SEMICOLON, ch, start)
        elif ch == ".":
            self._add(TokenType.DOT, ch, start)
        elif ch == "*":
            self._add(TokenType.STAR, ch, start)
        elif ch == "-":
            # A "--" comment is consumed in _skip_trivia, so a lone '-' here is
            # a minus sign (used as unary minus on numeric literals).
            self._add(TokenType.MINUS, ch, start)
        elif ch == "=":
            self._add(TokenType.EQ, ch, start)
        elif ch == "<":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.LTE, "<=", start)
            elif self._peek() == ">":
                self._advance()
                self._add(TokenType.NEQ, "<>", start)
            else:
                self._add(TokenType.LT, ch, start)
        elif ch == ">":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.GTE, ">=", start)
            else:
                self._add(TokenType.GT, ch, start)
        elif ch == "!":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.NEQ, "!=", start)
            else:
                raise SQLSyntaxError("expected '=' after '!'", start)
        else:
            raise SQLSyntaxError(f"unexpected character {ch!r}", start)


def tokenize(source: str) -> list[Token]:
    """Convenience: lex ``source`` into a token list (ending with EOF)."""
    return Lexer(source).tokenize()
