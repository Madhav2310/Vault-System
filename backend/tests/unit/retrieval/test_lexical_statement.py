"""The lexical statement never interpolates user text."""

import uuid

from app.retrieval.lexical import lexical_statement


def test_bound_params_only() -> None:
    hostile = "x'); DROP TABLE chunks; --"
    for q in (hostile, '"' + hostile + '"'):
        sql, params = lexical_statement(uuid.uuid4(), [uuid.uuid4()], q, 5)
        assert "DROP" not in sql and params["q"] == q


def test_matched_lexemes_counted_once_per_row_not_per_lexeme() -> None:
    sql, _ = lexical_statement(uuid.uuid4(), [uuid.uuid4()], "termination notice thirty days required", 5)
    assert "@@ quote_literal(l)::tsquery" not in sql  # one tsquery parse and match per lexeme per row
    assert "ts_delete(c.tsv_en, qq.lex)" in sql
