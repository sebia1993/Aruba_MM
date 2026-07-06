"""Stability tests for cleanup module."""

from aruba_mm_cleanup.cleanup import classify_delete_response


class StrWithStripError(str):
    """A string subclass that raises RuntimeError when strip() is called."""

    def strip(self):
        raise RuntimeError("strip method failed")


class StrWithCasefoldError(str):
    """A string subclass that returns self from strip() and raises RuntimeError from casefold()."""

    def strip(self):
        return self

    def casefold(self):
        raise RuntimeError("casefold method failed")


class StrWithContainsError(str):
    """A string subclass whose __contains__ method raises RuntimeError."""

    def __contains__(self, other):
        raise RuntimeError("__contains__ method failed")


def test_classify_delete_response_handles_strip_error():
    """Test that classify_delete_response handles str subclasses with failing strip() method."""
    problematic_str = StrWithStripError("some text")

    status, error = classify_delete_response(problematic_str)

    assert status == "unknown"
    assert "삭제 명령 응답 판정 불가" in error


def test_classify_delete_response_handles_casefold_error():
    """Test that classify_delete_response handles str subclasses with failing casefold() method."""
    problematic_str = StrWithCasefoldError("some text")

    status, error = classify_delete_response(problematic_str)

    assert status == "unknown"
    assert "삭제 명령 응답 판정 불가" in error


def test_classify_delete_response_handles_contains_error():
    """Test that classify_delete_response handles str subclasses with failing __contains__ method in markers."""
    class FaultyNormalized(str):
        def strip(self):
            return self

        def casefold(self):
            return StrWithContainsError("some text")

    problematic_str = FaultyNormalized("some text")

    status, error = classify_delete_response(problematic_str)

    assert status == "unknown"
    assert "삭제 명령 응답 판정 불가" in error
