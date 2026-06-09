"""Phase 4, slice 1 — lexer tests.

Prove the tokenizer recognizes every token kind, handles the fiddly cases
(multi-char operators, string escapes, case-insensitive keywords, comments),
and reports clear positioned errors — so the parser can trust its input.
"""

import pytest

from queryx.sql.lexer import tokenize
from queryx.sql.tokens import SQLSyntaxError, TokenType


def types(sql):
    return [t.type for t in tokenize(sql)]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_empty_input_is_just_eof():
    toks = tokenize("")
    assert len(toks) == 1
    assert toks[0].type == TokenType.EOF


def test_stream_always_ends_with_eof():
    assert types("SELECT")[-1] == TokenType.EOF


# ---------------------------------------------------------------------------
# Keywords vs identifiers
# ---------------------------------------------------------------------------


def test_keywords_are_case_insensitive():
    assert types("select") == [TokenType.SELECT, TokenType.EOF]
    assert types("SeLeCt") == [TokenType.SELECT, TokenType.EOF]


def test_identifiers_keep_case():
    toks = tokenize("Users userName")
    assert toks[0].type == TokenType.IDENT and toks[0].lexeme == "Users"
    assert toks[1].type == TokenType.IDENT and toks[1].lexeme == "userName"


def test_integer_type_aliases():
    assert types("INT") == [TokenType.INT, TokenType.EOF]
    assert types("INTEGER") == [TokenType.INT, TokenType.EOF]


def test_identifier_with_underscore_and_digits():
    toks = tokenize("_id col2 a1b2")
    assert all(t.type == TokenType.IDENT for t in toks[:3])
    assert [t.lexeme for t in toks[:3]] == ["_id", "col2", "a1b2"]


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


def test_number_literal_value():
    toks = tokenize("42")
    assert toks[0].type == TokenType.NUMBER
    assert toks[0].value == 42


def test_string_literal_value():
    toks = tokenize("'alice'")
    assert toks[0].type == TokenType.STRING
    assert toks[0].value == "alice"


def test_string_with_escaped_quote():
    toks = tokenize("'O''Brien'")
    assert toks[0].value == "O'Brien"


def test_empty_string_literal():
    toks = tokenize("''")
    assert toks[0].type == TokenType.STRING and toks[0].value == ""


# ---------------------------------------------------------------------------
# Operators & punctuation
# ---------------------------------------------------------------------------


def test_comparison_operators():
    assert types("= != <> < > <= >=") == [
        TokenType.EQ, TokenType.NEQ, TokenType.NEQ,
        TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE,
        TokenType.EOF,
    ]


def test_punctuation_and_star():
    assert types("( ) , ; *") == [
        TokenType.LPAREN, TokenType.RPAREN, TokenType.COMMA,
        TokenType.SEMICOLON, TokenType.STAR, TokenType.EOF,
    ]


def test_operators_need_no_surrounding_space():
    assert types("age>=30") == [
        TokenType.IDENT, TokenType.GTE, TokenType.NUMBER, TokenType.EOF,
    ]


# ---------------------------------------------------------------------------
# Whitespace & comments
# ---------------------------------------------------------------------------


def test_whitespace_is_ignored():
    assert types("  SELECT\t\n  *  ") == [TokenType.SELECT, TokenType.STAR, TokenType.EOF]


def test_line_comment_is_skipped():
    sql = "SELECT * -- this is a comment\nFROM users"
    assert types(sql) == [
        TokenType.SELECT, TokenType.STAR, TokenType.FROM, TokenType.IDENT, TokenType.EOF,
    ]


# ---------------------------------------------------------------------------
# A realistic full statement
# ---------------------------------------------------------------------------


def test_full_select_statement():
    sql = "SELECT name FROM users WHERE age >= 30 AND name <> 'bob'"
    assert types(sql) == [
        TokenType.SELECT, TokenType.IDENT, TokenType.FROM, TokenType.IDENT,
        TokenType.WHERE, TokenType.IDENT, TokenType.GTE, TokenType.NUMBER,
        TokenType.AND, TokenType.IDENT, TokenType.NEQ, TokenType.STRING,
        TokenType.EOF,
    ]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def test_unterminated_string_errors():
    with pytest.raises(SQLSyntaxError):
        tokenize("'no closing quote")


def test_bare_bang_errors():
    with pytest.raises(SQLSyntaxError):
        tokenize("a ! b")


def test_unexpected_character_errors():
    with pytest.raises(SQLSyntaxError):
        tokenize("a @ b")


def test_number_glued_to_letters_errors():
    with pytest.raises(SQLSyntaxError):
        tokenize("12abc")


def test_error_carries_position():
    try:
        tokenize("SELECT @")
    except SQLSyntaxError as e:
        assert e.position == 7
    else:
        pytest.fail("expected SQLSyntaxError")
