"""Tests for auth classification specificity in _classify_error_message and _classify_exception.

Verifies that the auth substring check uses specific phrases rather than
matching the bare substring "auth", which would cause false positives
for words like "authorization", "authenticate", or "author".

Closes #35
"""

import pytest

from ftl2.exceptions import ErrorTypes
from ftl2.retry import _classify_error_message, _classify_exception


class TestClassifyErrorMessageAuth:
    """Test _classify_error_message auth detection specificity."""

    @pytest.mark.parametrize("message", [
        "authentication failed",
        "Authentication Failed: invalid key",
        "SSH authentication error",
        "AUTHENTICATION ERROR",
        "invalid credentials",
        "Invalid Credentials for user admin",
        "login failed",
        "Login Failed: bad password",
        "unauthorized",
        "401 Unauthorized",
        "request unauthorized: token expired",
        "failed to authenticate with proxy",
        "Failed to authenticate: key mismatch",
    ])
    def test_true_positives(self, message: str) -> None:
        """Messages that should be classified as AUTHENTICATION_FAILED."""
        assert _classify_error_message(message) == ErrorTypes.AUTHENTICATION_FAILED

    @pytest.mark.parametrize("message", [
        "authorization denied for resource",
        "AuthorizationError: insufficient scope",
        "the author of this module is unknown",
        "auth token refresh in progress",
        "oauth2 flow started",
        "error in authz middleware",
    ])
    def test_false_positives_now_fixed(self, message: str) -> None:
        """Messages that previously matched 'auth' but should NOT be AUTHENTICATION_FAILED."""
        result = _classify_error_message(message)
        assert result != ErrorTypes.AUTHENTICATION_FAILED, (
            f"'{message}' should not match AUTHENTICATION_FAILED, got {result}"
        )

    def test_unrelated_errors_unchanged(self) -> None:
        """Other error classifications still work correctly."""
        assert _classify_error_message("connection refused") == ErrorTypes.CONNECTION_REFUSED
        assert _classify_error_message("timeout waiting") == ErrorTypes.CONNECTION_TIMEOUT
        assert _classify_error_message("permission denied") == ErrorTypes.PERMISSION_DENIED
        assert _classify_error_message("host unreachable") == ErrorTypes.HOST_UNREACHABLE
        assert _classify_error_message("module foo not found") == ErrorTypes.MODULE_NOT_FOUND
        assert _classify_error_message("something else") == ErrorTypes.UNKNOWN


class TestClassifyExceptionAuth:
    """Test _classify_exception auth detection specificity."""

    def _make_exc(self, class_name: str, message: str = "error") -> Exception:
        """Create an exception with a dynamic class name."""
        exc_class = type(class_name, (Exception,), {})
        return exc_class(message)

    @pytest.mark.parametrize("exc_name", [
        "AuthenticationError",
        "AuthenticationFailed",
        "UnauthorizedError",
    ])
    def test_true_positives(self, exc_name: str) -> None:
        """Exception types that should be classified as AUTHENTICATION_FAILED."""
        exc = self._make_exc(exc_name)
        assert _classify_exception(exc) == ErrorTypes.AUTHENTICATION_FAILED

    @pytest.mark.parametrize("exc_name", [
        "AuthorizationError",
        "AuthorError",
        "OAuthError",
    ])
    def test_false_positives_now_fixed(self, exc_name: str) -> None:
        """Exception types that previously matched 'auth' but should NOT be AUTHENTICATION_FAILED."""
        exc = self._make_exc(exc_name)
        result = _classify_exception(exc)
        assert result != ErrorTypes.AUTHENTICATION_FAILED, (
            f"Exception '{exc_name}' should not match AUTHENTICATION_FAILED, got {result}"
        )
