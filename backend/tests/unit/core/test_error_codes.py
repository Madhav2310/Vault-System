from app.core.error_codes import ERROR_SPECS, ErrorCode


def test_all_codes_complete() -> None:
    assert set(ERROR_SPECS) == set(ErrorCode)
    for code in ErrorCode:
        spec = code.spec
        assert 400 <= spec.status <= 599, code
        assert isinstance(spec.retryable, bool) and spec.title, code
        assert code.value == code.value.lower()
