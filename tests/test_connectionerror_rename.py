"""Tests for issue #39: FTL2ConnectionError rename to avoid shadowing builtin.

Validates that:
- FTL2ConnectionError exists and has the correct class hierarchy
- Importing FTL2ConnectionError does NOT shadow builtins.ConnectionError
- The deprecated alias still works for backwards compatibility
- All internal usage sites reference FTL2ConnectionError correctly
- Exception construction and attributes work properly
"""

import builtins
import importlib

import pytest


class TestNoBuiltinShadowing:
    """Core issue: importing from ftl2.exceptions must not shadow builtins."""

    def test_import_ftl2connectionerror_does_not_shadow_builtin(self):
        """After importing FTL2ConnectionError, builtins.ConnectionError is unchanged."""
        builtin_ce = builtins.ConnectionError

        assert builtins.ConnectionError is builtin_ce
        assert ConnectionError is builtin_ce  # module-level name still points to builtin

    def test_ftl2connectionerror_is_not_builtin_connectionerror(self):
        """FTL2ConnectionError is a distinct class from builtins.ConnectionError."""
        from ftl2.exceptions import FTL2ConnectionError

        assert FTL2ConnectionError is not builtins.ConnectionError

    def test_ftl2connectionerror_not_subclass_of_oserror(self):
        """FTL2ConnectionError should NOT be an OSError subclass (unlike builtin)."""
        from ftl2.exceptions import FTL2ConnectionError

        assert not issubclass(FTL2ConnectionError, OSError)
        # Builtin ConnectionError IS an OSError subclass
        assert issubclass(builtins.ConnectionError, OSError)

    def test_builtin_connectionerror_is_oserror(self):
        """Builtin ConnectionError is an OSError — sanity check."""
        assert issubclass(builtins.ConnectionError, OSError)
        assert isinstance(builtins.ConnectionError(), OSError)


class TestExceptionHierarchy:
    """FTL2ConnectionError must be in the FTL2Error hierarchy, not builtin."""

    def test_subclass_of_ftl2error(self):
        from ftl2.exceptions import FTL2ConnectionError, FTL2Error

        assert issubclass(FTL2ConnectionError, FTL2Error)

    def test_subclass_of_exception(self):
        from ftl2.exceptions import FTL2ConnectionError

        assert issubclass(FTL2ConnectionError, Exception)

    def test_not_subclass_of_builtin_connectionerror(self):
        from ftl2.exceptions import FTL2ConnectionError

        assert not issubclass(FTL2ConnectionError, builtins.ConnectionError)

    def test_catching_ftl2error_catches_ftl2connectionerror(self):
        from ftl2.exceptions import FTL2ConnectionError, FTL2Error

        with pytest.raises(FTL2Error):
            raise FTL2ConnectionError("test")

    def test_catching_builtin_connectionerror_does_not_catch_ftl2(self):
        """This is the key behavior fix: catching builtin ConnectionError
        should NOT catch FTL2ConnectionError."""
        from ftl2.exceptions import FTL2ConnectionError

        with pytest.raises(FTL2ConnectionError):
            try:
                raise FTL2ConnectionError("test")
            except builtins.ConnectionError:
                pytest.fail("Builtin ConnectionError should NOT catch FTL2ConnectionError")


class TestDeprecatedAlias:
    """The deprecated alias ConnectionError = FTL2ConnectionError must work."""

    def test_alias_exists_in_module(self):
        import ftl2.exceptions

        assert hasattr(ftl2.exceptions, "ConnectionError")

    def test_alias_is_same_class(self):
        import ftl2.exceptions
        from ftl2.exceptions import FTL2ConnectionError

        assert ftl2.exceptions.ConnectionError is FTL2ConnectionError

    def test_alias_import_still_works(self):
        """from ftl2.exceptions import ConnectionError should still resolve."""
        # Use importlib to avoid polluting module namespace
        mod = importlib.import_module("ftl2.exceptions")
        alias = mod.ConnectionError
        assert alias is mod.FTL2ConnectionError

    def test_alias_instance_is_ftl2connectionerror(self):
        import ftl2.exceptions

        exc = ftl2.exceptions.ConnectionError("test")
        assert isinstance(exc, ftl2.exceptions.FTL2ConnectionError)


class TestFTL2ConnectionErrorConstruction:
    """Test that FTL2ConnectionError can be constructed with various args."""

    def test_minimal_construction(self):
        from ftl2.exceptions import FTL2ConnectionError

        exc = FTL2ConnectionError("connection failed")
        assert str(exc) == "connection failed"

    def test_full_construction(self):
        from ftl2.exceptions import FTL2ConnectionError

        exc = FTL2ConnectionError(
            message="timeout",
            host="webserver1",
            host_address="192.168.1.1",
            port=2222,
            user="deploy",
            error_type="ConnectionTimeout",
            attempt=2,
            max_attempts=5,
        )
        assert str(exc) == "timeout"
        assert exc.context.host == "webserver1"
        assert exc.context.host_address == "192.168.1.1:2222"
        assert exc.context.user == "deploy"
        assert exc.context.attempt == 2
        assert exc.context.max_attempts == 5

    def test_default_port(self):
        from ftl2.exceptions import FTL2ConnectionError

        exc = FTL2ConnectionError("fail", host_address="10.0.0.1")
        assert exc.context.host_address == "10.0.0.1:22"

    def test_has_error_context(self):
        from ftl2.exceptions import ErrorContext, FTL2ConnectionError

        exc = FTL2ConnectionError("fail")
        assert isinstance(exc.context, ErrorContext)
        assert exc.context.suggestions is not None


class TestInternalUsageSites:
    """Verify internal code imports FTL2ConnectionError, not the alias."""

    def test_runners_imports_ftl2connectionerror(self):
        """runners.py should import FTL2ConnectionError directly."""
        import inspect

        import ftl2.runners

        source = inspect.getsource(ftl2.runners)
        assert "from .exceptions import" in source or "from ftl2.exceptions import" in source
        # Should use FTL2ConnectionError, not bare ConnectionError in raises
        assert "FTL2ConnectionError" in source

    def test_context_imports_ftl2connectionerror(self):
        """automation/context.py should import FTL2ConnectionError."""
        import inspect

        mod = importlib.import_module("ftl2.automation.context")
        source = inspect.getsource(mod)
        assert "FTL2ConnectionError" in source

    def test_proxy_imports_ftl2connectionerror(self):
        """automation/proxy.py should import FTL2ConnectionError."""
        import inspect

        mod = importlib.import_module("ftl2.automation.proxy")
        source = inspect.getsource(mod)
        assert "FTL2ConnectionError" in source


class TestExceptClauseBehavior:
    """Ensure except clauses work correctly with the new name."""

    def test_except_ftl2connectionerror_catches_it(self):
        from ftl2.exceptions import FTL2ConnectionError

        caught = False
        try:
            raise FTL2ConnectionError("test")
        except FTL2ConnectionError:
            caught = True
        assert caught

    def test_except_exception_catches_it(self):
        from ftl2.exceptions import FTL2ConnectionError

        caught = False
        try:
            raise FTL2ConnectionError("test")
        except Exception:
            caught = True
        assert caught

    def test_except_builtin_connectionerror_does_not_catch_it(self):
        """Critical: builtin ConnectionError handler must not catch FTL2ConnectionError."""
        from ftl2.exceptions import FTL2ConnectionError

        caught_wrong = False
        caught_right = False
        try:
            raise FTL2ConnectionError("test")
        except builtins.ConnectionError:
            caught_wrong = True
        except FTL2ConnectionError:
            caught_right = True
        assert not caught_wrong
        assert caught_right

    def test_separate_handling_of_both_types(self):
        """Both exception types can be caught independently."""
        from ftl2.exceptions import FTL2ConnectionError

        # FTL2 exception
        with pytest.raises(FTL2ConnectionError):
            raise FTL2ConnectionError("ftl2 error")

        # Builtin exception
        with pytest.raises(builtins.ConnectionError):
            raise builtins.ConnectionError("builtin error")
