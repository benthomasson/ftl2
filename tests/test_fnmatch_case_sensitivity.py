"""Tests for fnmatch case-sensitivity fix (issue #42).

Verifies that all pattern matching across policy.py, host_filter.py, and
automation/context.py uses fnmatch.fnmatchcase for platform-independent
case-sensitive matching.
"""

import fnmatch

import pytest

from ftl2.host_filter import filter_hosts, match_host, parse_limit_pattern
from ftl2.policy import Policy, PolicyRule


# ---------------------------------------------------------------------------
# Policy engine case-sensitivity tests
# ---------------------------------------------------------------------------


class TestPolicyCaseSensitiveMatching:
    """Case-sensitivity tests for Policy._matches() across all match keys."""

    # -- Host matching --

    def test_host_uppercase_pattern_rejects_lowercase(self):
        rule = PolicyRule(decision="deny", match={"host": "PROD-*"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="prod-web-01").permitted is True

    def test_host_lowercase_pattern_rejects_uppercase(self):
        rule = PolicyRule(decision="deny", match={"host": "prod-*"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="PROD-web-01").permitted is True

    def test_host_exact_case_matches(self):
        rule = PolicyRule(decision="deny", match={"host": "prod-*"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="prod-web-01").permitted is False

    # -- Module matching --

    def test_module_mixed_case_no_match(self):
        rule = PolicyRule(decision="deny", match={"module": "ansible.builtin.Shell"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ansible.builtin.shell", {}).permitted is True
        assert policy.evaluate("ansible.builtin.SHELL", {}).permitted is True
        assert policy.evaluate("ansible.builtin.Shell", {}).permitted is False

    # -- Environment matching --

    def test_env_case_mismatch_no_match(self):
        rule = PolicyRule(decision="deny", match={"environment": "Production"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, environment="production").permitted is True
        assert policy.evaluate("ping", {}, environment="PRODUCTION").permitted is True
        assert policy.evaluate("ping", {}, environment="Production").permitted is False

    # -- Param matching --

    def test_param_case_mismatch_no_match(self):
        rule = PolicyRule(decision="deny", match={"param.state": "ABSENT"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("file", {"state": "absent"}).permitted is True
        assert policy.evaluate("file", {"state": "Absent"}).permitted is True
        assert policy.evaluate("file", {"state": "ABSENT"}).permitted is False

    # -- Glob character class patterns --

    def test_character_class_uppercase_only(self):
        """[A-Z]* should NOT match lowercase-initial strings."""
        rule = PolicyRule(decision="deny", match={"host": "[A-Z]*"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="Prod-01").permitted is False
        assert policy.evaluate("ping", {}, host="prod-01").permitted is True

    # -- Multi-condition with mixed case --

    def test_multi_condition_case_sensitive(self):
        """Both conditions must match in exact case."""
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "environment": "PROD"},
            reason="x",
        )
        policy = Policy([rule])
        # Both match
        assert policy.evaluate("shell", {}, environment="PROD").permitted is False
        # Module matches, env case wrong
        assert policy.evaluate("shell", {}, environment="prod").permitted is True
        # Env matches, module case wrong
        assert policy.evaluate("Shell", {}, environment="PROD").permitted is True

    # -- Wildcard * still matches all case variants --

    def test_bare_wildcard_matches_any_case(self):
        """A bare '*' pattern should match everything regardless of case."""
        rule = PolicyRule(decision="deny", match={"host": "*"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="ANYTHING").permitted is False
        assert policy.evaluate("ping", {}, host="anything").permitted is False
        assert policy.evaluate("ping", {}, host="AnYtHiNg").permitted is False

    # -- Question mark wildcard --

    def test_question_mark_case_sensitive(self):
        """? matches any single char but surrounding literal chars are case-sensitive."""
        rule = PolicyRule(decision="deny", match={"host": "DB-?"}, reason="x")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="DB-1").permitted is False
        assert policy.evaluate("ping", {}, host="db-1").permitted is True


# ---------------------------------------------------------------------------
# Host filter case-sensitivity tests
# ---------------------------------------------------------------------------


class TestHostFilterCaseSensitive:
    """Case-sensitivity tests for host_filter.match_host()."""

    def test_include_pattern_case_sensitive(self):
        assert match_host("web-01", set(), {"web-*"}, set()) is True
        assert match_host("WEB-01", set(), {"web-*"}, set()) is False

    def test_exclude_pattern_case_sensitive(self):
        assert match_host("db-01", set(), set(), {"db-*"}) is False
        assert match_host("DB-01", set(), set(), {"db-*"}) is True  # not excluded

    def test_exclude_overrides_include_same_case(self):
        assert match_host("web-01", set(), {"web-*"}, {"web-01"}) is False

    def test_include_exact_is_case_sensitive(self):
        """Exact match uses set membership, inherently case-sensitive."""
        assert match_host("Web-01", {"web-01"}, set(), set()) is False
        assert match_host("web-01", {"web-01"}, set(), set()) is True

    def test_filter_hosts_case_sensitive(self):
        hosts = {"prod-web-01": {}, "PROD-web-02": {}, "staging-01": {}}
        result = filter_hosts(hosts, "prod-*")
        assert "prod-web-01" in result
        assert "PROD-web-02" not in result
        assert "staging-01" not in result

    def test_filter_hosts_exclude_case_sensitive(self):
        hosts = {"db-01": {}, "DB-02": {}, "web-01": {}}
        result = filter_hosts(hosts, "!db-*")
        assert "db-01" not in result
        assert "DB-02" in result  # not excluded (case mismatch)
        assert "web-01" in result


# ---------------------------------------------------------------------------
# Direct fnmatchcase verification
# ---------------------------------------------------------------------------


class TestFnmatchcaseDirectBehavior:
    """Sanity-check that fnmatchcase behaves as expected on this platform."""

    def test_case_sensitive_literal(self):
        assert fnmatch.fnmatchcase("hello", "hello") is True
        assert fnmatch.fnmatchcase("Hello", "hello") is False

    def test_case_sensitive_glob(self):
        assert fnmatch.fnmatchcase("PROD-01", "PROD-*") is True
        assert fnmatch.fnmatchcase("prod-01", "PROD-*") is False

    def test_empty_string_matches_star(self):
        assert fnmatch.fnmatchcase("", "*") is True

    def test_empty_matches_empty(self):
        assert fnmatch.fnmatchcase("", "") is True
