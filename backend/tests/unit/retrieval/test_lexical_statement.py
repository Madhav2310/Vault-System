"""S11.03 AC6: the lexical statement never interpolates user text."""

import uuid

from app.retrieval.lexical import lexical_statement


def test_bound_params_only() -> None:
    hostile = "x'); DROP TABLE chunks; --"
    for q in (hostile, '"' + hostile + '"'):
        sql, params = lexical_statement(uuid.uuid4(), [uuid.uuid4()], q, 5)
        assert "DROP" not in sql and params["q"] == q
