"""Tester-stage tests for auth classification fix (issue #35).

These tests complement the implementer's test_classify_error_auth.py with
additional edge cases from the reviewer's notes:
- Case variations
- Substring boundary behavior ("unauthorizedaccess")
- Exception message fallback path
- Mixed type-name/message scenarios
- Interaction with PERMANENT_ERRORS (retry behavior)

Closes #35
"""

import pytest

from ftl2.exceptions import ErrorTypes
from ftl2.retry import (
    _classify_error_message,
    _classify_exception,
    is_permanent_error,
)

# ---------------------------------------------------------------------------
# _classify_error_message: additional edge cases
# ---------------------------------------------------------------------------

class TestMessageEdgeCases:
    """Edge cases for _classify_error_message not covered by implementer tests."""

    @pytest.mark.parametrize("message,expected", [
        # Case variations — all should match AUTHENTICATION_FAILED
        ("AUTHENTICATION FAILED", ErrorTypes.AUTHENTICATION_FAILED),
        ("Authentication Error", ErrorTypes.AUTHENTICATION_FAILED),
        ("FAILED TO AUTHENTICATE", ErrorTypes.AUTHENTICATION_FAILED),
        ("INVALID CREDENTIALS", ErrorTypes.AUTHENTICATION_FAILED),
        ("LOGIN FAILED", ErrorTypes.AUTHENTICATION_FAILED),
        ("UNAUTHORIZED", ErrorTypes.AUTHENTICATION_FAILED),
        # Mixed case
        ("aUtHeNtIcAtIoN fAiLeD", ErrorTypes.AUTHENTICATION_FAILED),
    ])
    def test_case_insensitive_matching(self, message, expected):
        """All phrase matches should be case-insensitive."""
        assert _classify_error_message(message) == expected

    @pytest.mark.parametrize("message", [
        # "unauthorized" as substring in a longer word — still matches
        "unauthorizedaccess",
        "unauthorizedexception thrown",
    ])
    def test_unauthorized_substring_matches(self, message):
        """'unauthorized' as prefix of a compound word should still match.

        This is correct behavior — 'unauthorizedaccess' is still an auth failure.
        """
        assert _classify_error_message(message) == ErrorTypes.AUTHENTICATION_FAILED

    @pytest.mark.parametrize("message", [
        # These contain "auth" but NOT as part of any specific phrase
        "preauth negotiation failed",
        "authoring system error",
        "authentic signature mismatch",
        "authority validation failed",
    ])
    def test_auth_substring_no_match(self, message):
        """Messages with 'auth' as substring of unrelated words must NOT match."""
        assert _classify_error_message(message) != ErrorTypes.AUTHENTICATION_FAILED

    def test_empty_string(self):
        """Empty message should return UNKNOWN."""
        assert _classify_error_message("") == ErrorTypes.UNKNOWN

    def test_phrase_at_message_boundaries(self):
        """Phrases at start, middle, end of messages."""
        assert _classify_error_message("authentication failed") == ErrorTypes.AUTHENTICATION_FAILED
        assert _classify_error_message("error: authentication failed on host X") == ErrorTypes.AUTHENTICATION_FAILED
        assert _classify_error_message("host X: authentication failed") == ErrorTypes.AUTHENTICATION_FAILED

    def test_multiple_phrases_first_wins(self):
        """When a message matches multiple classifications, first match wins."""
        # "timeout" check comes before auth check
        msg = "timeout during authentication failed attempt"
        assert _classify_error_message(msg) == ErrorTypes.CONNECTION_TIMEOUT

    def test_auth_phrase_with_surrounding_punctuation(self):
        """Phrases should match even when surrounded by punctuation."""
        assert _classify_error_message("[authentication failed]") == ErrorTypes.AUTHENTICATION_FAILED
        assert _classify_error_message("error: 'login failed' for user") == ErrorTypes.AUTHENTICATION_FAILED


# ---------------------------------------------------------------------------
# _classify_exception: additional edge cases
# ---------------------------------------------------------------------------

class TestExceptionEdgeCases:
    """Edge cases for _classify_exception."""

    def _make_exc(self, class_name, message="error"):
        """Create an exception with a dynamic class name."""
        exc_class = type(class_name, (Exception,), {})
        return exc_class(message)

    def test_exception_message_fallback(self):
        """RuntimeError with auth message should classify via message fallback."""
        exc = RuntimeError("authentication failed")
        assert _classify_exception(exc) == ErrorTypes.AUTHENTICATION_FAILED

    def test_exception_message_fallback_no_match(self):
        """RuntimeError with non-auth message should return UNKNOWN."""
        exc = RuntimeError("something broke")
        assert _classify_exception(exc) == ErrorTypes.UNKNOWN

    def test_authorization_exception_with_auth_message(self):
        """AuthorizationError with auth-failed message: type doesn't match, message does.

        The exception name 'authorizationerror' does NOT contain 'authentication'
        or 'unauthorized', so type-based check fails. But the message 'authentication
        failed' triggers the message fallback. This is correct — the message says
        auth failed.
        """
        exc = self._make_exc("AuthorizationError", "authentication failed")
        assert _classify_exception(exc) == ErrorTypes.AUTHENTICATION_FAILED

    def test_oauth_exception_no_auth_message(self):
        """OAuthError with non-auth message should NOT match auth."""
        exc = self._make_exc("OAuthError", "token refresh timeout")
        # "timeout" is in the message, so it falls back to message classification
        # and "timeout" check fires first
        assert _classify_exception(exc) == ErrorTypes.CONNECTION_TIMEOUT

    def test_oauth_exception_generic_message(self):
        """OAuthError with generic message should return UNKNOWN."""
        exc = self._make_exc("OAuthError", "flow interrupted")
        assert _classify_exception(exc) == ErrorTypes.UNKNOWN

    @pytest.mark.parametrize("exc_name", [
        "SSHAuthenticationError",
        "ProxyAuthenticationFailed",
        "HttpUnauthorizedError",
    ])
    def test_compound_exception_names_match(self, exc_name):
        """Compound exception names containing 'authentication'/'unauthorized' should match."""
        exc = self._make_exc(exc_name)
        assert _classify_exception(exc) == ErrorTypes.AUTHENTICATION_FAILED

    @pytest.mark.parametrize("exc_name", [
        "AuthzError",
        "PreAuthError",
        "AuthTokenExpiredError",
    ])
    def test_compound_exception_names_no_match(self, exc_name):
        """Exception names with 'auth' but not 'authentication'/'unauthorized' should NOT match."""
        exc = self._make_exc(exc_name)
        assert _classify_exception(exc) != ErrorTypes.AUTHENTICATION_FAILED


# ---------------------------------------------------------------------------
# Integration: PERMANENT_ERRORS interaction
# ---------------------------------------------------------------------------

class TestPermanentErrorInteraction:
    """Verify that AUTHENTICATION_FAILED is still a permanent error,
    and that the fix doesn't break retry behavior for correctly classified errors."""

    def test_auth_failed_is_permanent(self):
        """AUTHENTICATION_FAILED should be in PERMANENT_ERRORS (no retry)."""
        assert is_permanent_error(ErrorTypes.AUTHENTICATION_FAILED) is True

    def test_unknown_is_not_permanent(self):
        """UNKNOWN should NOT be permanent — previously overbroad matches
        were marked permanent, skipping retries incorrectly."""
        assert is_permanent_error(ErrorTypes.UNKNOWN) is False

    def test_false_positive_now_retries(self):
        """An 'authorization' error that was previously classified as
        AUTHENTICATION_FAILED (permanent, no retry) should now be UNKNOWN
        and eligible for retry."""
        result = _classify_error_message("authorization denied")
        assert result == ErrorTypes.UNKNOWN
        assert is_permanent_error(result) is False
