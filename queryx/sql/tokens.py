"""Token definitions — the shared vocabulary of the lexer and parser.

A *token* is the smallest meaningful unit of SQL text: a keyword (SELECT), an
identifier (a table/column name), a literal (42, 'alice'), an operator (>=), or
punctuation (a comma, a parenthesis). The lexer produces tokens; the parser
consumes them. Keeping the token kinds here lets both depend on one vocabulary
without depending on each other.

Also defines SQLSyntaxError, raised by both the lexer (bad character) and the
parser (bad structure), carrying the source position so errors point at the
offending text rather than failing cryptically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    # literals & identifiers
    IDENT = auto()      # users, name, age
    NUMBER = auto()     # 42  (integer literal)
    STRING = auto()     # 'alice' (string literal)

    # punctuation
    COMMA = auto()      # ,
    LPAREN = auto()     # (
    RPAREN = auto()     # )
    SEMICOLON = auto()  # ;
    STAR = auto()       # *   (SELECT *, COUNT(*))
    MINUS = auto()      # -   (unary minus on a numeric literal)

    # comparison operators
    EQ = auto()         # =
    NEQ = auto()        # != or <>
    LT = auto()         # <
    GT = auto()         # >
    LTE = auto()        # <=
    GTE = auto()        # >=

    # keywords
    CREATE = auto()
    TABLE = auto()
    DROP = auto()
    INSERT = auto()
    INTO = auto()
    VALUES = auto()
    SELECT = auto()
    DISTINCT = auto()
    FROM = auto()
    WHERE = auto()
    ORDER = auto()
    BY = auto()
    ASC = auto()
    DESC = auto()
    LIMIT = auto()
    UPDATE = auto()
    SET = auto()
    DELETE = auto()
    INDEX = auto()
    ON = auto()
    EXPLAIN = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    # scalar aggregate functions
    COUNT = auto()
    SUM = auto()
    AVG = auto()
    MIN = auto()
    MAX = auto()
    # column types
    INT = auto()        # INT / INTEGER both map here
    TEXT = auto()

    # end of input
    EOF = auto()


#: Reserved words, upper-cased, mapped to their token type. SQL keywords are
#: case-insensitive, so the lexer upper-cases a word before looking it up here;
#: anything not present is an identifier.
KEYWORDS: dict[str, TokenType] = {
    "CREATE": TokenType.CREATE,
    "TABLE": TokenType.TABLE,
    "DROP": TokenType.DROP,
    "INSERT": TokenType.INSERT,
    "INTO": TokenType.INTO,
    "VALUES": TokenType.VALUES,
    "SELECT": TokenType.SELECT,
    "DISTINCT": TokenType.DISTINCT,
    "FROM": TokenType.FROM,
    "WHERE": TokenType.WHERE,
    "ORDER": TokenType.ORDER,
    "BY": TokenType.BY,
    "ASC": TokenType.ASC,
    "DESC": TokenType.DESC,
    "LIMIT": TokenType.LIMIT,
    "UPDATE": TokenType.UPDATE,
    "SET": TokenType.SET,
    "DELETE": TokenType.DELETE,
    "INDEX": TokenType.INDEX,
    "ON": TokenType.ON,
    "EXPLAIN": TokenType.EXPLAIN,
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "NOT": TokenType.NOT,
    "COUNT": TokenType.COUNT,
    "SUM": TokenType.SUM,
    "AVG": TokenType.AVG,
    "MIN": TokenType.MIN,
    "MAX": TokenType.MAX,
    "INT": TokenType.INT,
    "INTEGER": TokenType.INT,  # alias
    "TEXT": TokenType.TEXT,
}


@dataclass(frozen=True)
class Token:
    """A single lexical token.

    ``lexeme`` is the exact source text; ``value`` is the decoded payload for
    literals (an int for NUMBER, a str for STRING) and None otherwise;
    ``position`` is the 0-based index of the token's first character in the
    source, for error messages.
    """

    type: TokenType
    lexeme: str
    position: int
    value: object = None


class SQLSyntaxError(Exception):
    """A lexing or parsing error, with the source position of the problem."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"{message} (at position {position})")
        self.message = message
        self.position = position
