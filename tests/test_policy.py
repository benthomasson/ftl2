"""Tests for policy engine."""

import tempfile
from pathlib import Path

import pytest

from ftl2.policy import Policy, PolicyDeniedError, PolicyResult, PolicyRule


class TestPolicyEvaluate:
    """Tests for Policy.evaluate()."""

    def test_empty_policy_permits_everything(self):
        policy = Policy.empty()
        result = policy.evaluate("shell", {"cmd": "rm -rf /"})
        assert result.permitted is True

    def test_single_deny_rule_matches(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="No shell")
        policy = Policy([rule])
        result = policy.evaluate("shell", {})
        assert result.permitted is False
        assert result.rule is rule
        assert result.reason == "No shell"

    def test_deny_rule_no_match_permits(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="No shell")
        policy = Policy([rule])
        result = policy.evaluate("ping", {})
        assert result.permitted is True

    def test_allow_rules_raise_error(self):
        with pytest.raises(ValueError, match="allow rules are not supported"):
            PolicyRule(decision="allow", match={"module": "shell"})

    def test_invalid_decision_raises_error(self):
        with pytest.raises(ValueError, match="Invalid decision 'dennied'"):
            PolicyRule(decision="dennied", match={"module": "shell"})

    def test_multiple_conditions_all_must_match(self):
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "environment": "prod"},
            reason="No shell in prod",
        )
        policy = Policy([rule])

        # Both match -> denied
        assert policy.evaluate("shell", {}, environment="prod").permitted is False
        # Only module matches -> permitted
        assert policy.evaluate("shell", {}, environment="dev").permitted is True
        # Only env matches -> permitted
        assert policy.evaluate("ping", {}, environment="prod").permitted is True

    def test_fnmatch_patterns(self):
        rule = PolicyRule(decision="deny", match={"host": "prod-*"}, reason="No prod")
        policy = Policy([rule])

        assert policy.evaluate("ping", {}, host="prod-web-01").permitted is False
        assert policy.evaluate("ping", {}, host="staging-web-01").permitted is True

    def test_module_wildcard(self):
        rule = PolicyRule(decision="deny", match={"module": "*.destructive"})
        policy = Policy([rule])

        assert policy.evaluate("ops.destructive", {}).permitted is False
        assert policy.evaluate("ops.safe", {}).permitted is True

    def test_param_matching(self):
        rule = PolicyRule(
            decision="deny",
            match={"param.state": "absent"},
            reason="No deletions",
        )
        policy = Policy([rule])

        assert policy.evaluate("file", {"state": "absent"}).permitted is False
        assert policy.evaluate("file", {"state": "present"}).permitted is True
        # Missing param -> empty string, no match
        assert policy.evaluate("file", {}).permitted is True

    def test_unknown_condition_key_raises_error(self):
        with pytest.raises(ValueError, match="Unknown match key 'bogus_key'"):
            PolicyRule(decision="deny", match={"bogus_key": "val"}, reason="bad")

    def test_first_matching_deny_wins(self):
        rules = [
            PolicyRule(decision="deny", match={"module": "shell"}, reason="first"),
            PolicyRule(decision="deny", match={"module": "shell"}, reason="second"),
        ]
        policy = Policy(rules)
        result = policy.evaluate("shell", {})
        assert result.reason == "first"


class TestPolicyFromFile:
    """Tests for Policy.from_file()."""

    def test_load_yaml(self, tmp_path):
        yaml_content = """\
rules:
  - decision: deny
    match:
      module: shell
      environment: prod
    reason: No shell in production
  - decision: deny
    match:
      host: "prod-*"
      param.state: absent
    reason: No deletions on prod hosts
"""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(yaml_content)

        policy = Policy.from_file(policy_file)
        assert len(policy.rules) == 2
        assert policy.rules[0].decision == "deny"
        assert policy.rules[0].match == {"module": "shell", "environment": "prod"}
        assert policy.rules[0].reason == "No shell in production"

        # Verify it actually works
        assert policy.evaluate("shell", {}, environment="prod").permitted is False
        assert policy.evaluate("ping", {}, environment="prod").permitted is True

    def test_empty_rules_file(self, tmp_path):
        policy_file = tmp_path / "empty.yaml"
        policy_file.write_text("rules: []\n")
        policy = Policy.from_file(policy_file)
        assert policy.evaluate("anything", {}).permitted is True


class TestPolicyDeniedError:
    """Tests for PolicyDeniedError."""

    def test_carries_rule(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="nope")
        err = PolicyDeniedError("Action denied", rule=rule)
        assert err.rule is rule
        assert str(err) == "Action denied"

    def test_is_ftl2error(self):
        from ftl2.exceptions import FTL2Error

        err = PolicyDeniedError("denied")
        assert isinstance(err, FTL2Error)

    def test_caught_by_except_ftl2error(self):
        from ftl2.exceptions import FTL2Error

        with pytest.raises(FTL2Error):
            raise PolicyDeniedError("denied")

    def test_has_error_context(self):
        err = PolicyDeniedError("denied")
        assert err.context is not None
        assert err.context.error_type == "PolicyDenied"
        assert err.context.message == "denied"

    def test_rule_defaults_to_none(self):
        err = PolicyDeniedError("denied")
        assert err.rule is None
