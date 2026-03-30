"""Tests for PolicyDeniedError hierarchy fix (#36).

Validates that PolicyDeniedError correctly extends FTL2Error so it can be
caught generically alongside all other FTL2 errors.
"""

import pytest

from ftl2.exceptions import ErrorContext, FTL2Error, GateError, InventoryError
from ftl2.policy import PolicyDeniedError, PolicyRule


class TestPolicyDeniedErrorHierarchy:
    """Core hierarchy tests — PolicyDeniedError must be a proper FTL2Error."""

    def test_isinstance_ftl2error(self):
        err = PolicyDeniedError("denied")
        assert isinstance(err, FTL2Error)

    def test_isinstance_exception(self):
        err = PolicyDeniedError("denied")
        assert isinstance(err, Exception)

    def test_caught_by_except_ftl2error(self):
        caught = False
        try:
            raise PolicyDeniedError("policy blocked this")
        except FTL2Error:
            caught = True
        assert caught, "except FTL2Error must catch PolicyDeniedError"

    def test_specific_catch_before_generic(self):
        """except PolicyDeniedError should still work before except FTL2Error."""
        caught_specific = False
        try:
            raise PolicyDeniedError("denied")
        except PolicyDeniedError:
            caught_specific = True
        except FTL2Error:
            pytest.fail("Generic handler should not fire when specific is first")
        assert caught_specific

    def test_mro_includes_ftl2error(self):
        assert FTL2Error in PolicyDeniedError.__mro__

    def test_subclass_check(self):
        assert issubclass(PolicyDeniedError, FTL2Error)
        assert issubclass(PolicyDeniedError, Exception)


class TestPolicyDeniedErrorContext:
    """ErrorContext integration tests."""

    def test_has_context_attribute(self):
        err = PolicyDeniedError("action blocked")
        assert hasattr(err, "context")
        assert err.context is not None

    def test_context_error_type(self):
        err = PolicyDeniedError("blocked")
        assert err.context.error_type == "PolicyDenied"

    def test_context_message(self):
        msg = "Policy denied shell on prod: no shell in production"
        err = PolicyDeniedError(msg)
        assert err.context.message == msg

    def test_context_is_error_context_instance(self):
        err = PolicyDeniedError("denied")
        assert isinstance(err.context, ErrorContext)

    def test_with_context_method(self):
        """FTL2Error.with_context() should work on PolicyDeniedError."""
        err = PolicyDeniedError("denied")
        err.with_context(host="prod-web-01", module="shell")
        assert err.context.host == "prod-web-01"
        assert err.context.module == "shell"

    def test_context_to_dict(self):
        err = PolicyDeniedError("denied")
        d = err.context.to_dict()
        assert d["error_type"] == "PolicyDenied"
        assert d["message"] == "denied"

    def test_context_format_text(self):
        err = PolicyDeniedError("denied")
        text = err.context.format_text()
        assert "PolicyDenied" in text
        assert "denied" in text


class TestPolicyDeniedErrorAttributes:
    """Attribute preservation and backward compatibility."""

    def test_rule_attribute_with_rule(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="nope")
        err = PolicyDeniedError("denied", rule=rule)
        assert err.rule is rule

    def test_rule_defaults_to_none(self):
        err = PolicyDeniedError("denied")
        assert err.rule is None

    def test_str_returns_message(self):
        err = PolicyDeniedError("Action denied by policy")
        assert str(err) == "Action denied by policy"

    def test_repr_contains_class_name(self):
        err = PolicyDeniedError("denied")
        assert "PolicyDeniedError" in repr(err)

    def test_args_tuple(self):
        err = PolicyDeniedError("denied")
        assert err.args == ("denied",)


class TestPolicyDeniedErrorCatchPatterns:
    """Real-world catch patterns that must work correctly."""

    def test_mixed_ftl2_errors_all_caught(self):
        """All FTL2 errors including PolicyDeniedError caught by one handler."""
        errors = [
            PolicyDeniedError("policy"),
            GateError("gate"),
            InventoryError("inventory"),
        ]
        for error in errors:
            caught = False
            try:
                raise error
            except FTL2Error:
                caught = True
            assert caught, f"{type(error).__name__} not caught by except FTL2Error"

    def test_executor_catch_pattern(self):
        """Simulate the executor's except FTL2Error handler (executor.py:326)."""
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="blocked")
        err = PolicyDeniedError(
            "Policy denied shell on prod-web-01: blocked", rule=rule
        )

        # Simulate what executor does: catch FTL2Error, extract context
        try:
            raise err
        except FTL2Error as e:
            error_msg = str(e)
            error_context = e.context
        else:
            pytest.fail("PolicyDeniedError should be caught by except FTL2Error")

        assert "Policy denied shell" in error_msg
        assert error_context.error_type == "PolicyDenied"

    def test_not_caught_by_unrelated_exception(self):
        """PolicyDeniedError should not be caught by unrelated exception types."""
        with pytest.raises(PolicyDeniedError):
            try:
                raise PolicyDeniedError("denied")
            except (ValueError, TypeError, KeyError):
                pytest.fail("Should not be caught by unrelated types")


class TestPolicyDeniedErrorRaiseSite:
    """Test the raise site in automation/context.py:483."""

    def test_raise_with_message_and_rule(self):
        """Matches the call pattern in context.py:483-486."""
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "environment": "prod"},
            reason="No shell in production",
        )
        err = PolicyDeniedError(
            f"Policy denied shell on prod-web-01: {rule.reason}",
            rule=rule,
        )
        assert str(err) == "Policy denied shell on prod-web-01: No shell in production"
        assert err.rule is rule
        assert isinstance(err, FTL2Error)
        assert err.context.error_type == "PolicyDenied"

    def test_raise_with_message_only(self):
        """Minimal invocation — still works."""
        err = PolicyDeniedError("denied")
        assert str(err) == "denied"
        assert err.rule is None
        assert isinstance(err, FTL2Error)
