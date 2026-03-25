"""Tests for policy engine validation (issue #21 - silent failure modes)."""

import pytest

from ftl2.policy import Policy, PolicyRule


class TestDecisionValidation:
    """Validate that invalid decision strings are rejected at construction."""

    def test_typo_dennied_raises(self):
        with pytest.raises(ValueError, match="Invalid decision 'dennied'"):
            PolicyRule(decision="dennied", match={"module": "shell"})

    def test_typo_dney_raises(self):
        with pytest.raises(ValueError, match="Invalid decision 'dney'"):
            PolicyRule(decision="dney", match={"module": "shell"})

    def test_empty_decision_raises(self):
        with pytest.raises(ValueError, match="Invalid decision ''"):
            PolicyRule(decision="", match={"module": "shell"})

    def test_allow_rejected_with_clear_message(self):
        with pytest.raises(ValueError, match="allow rules are not supported"):
            PolicyRule(decision="allow", match={"module": "shell"})

    def test_deny_accepted(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"})
        assert rule.decision == "deny"

    def test_case_sensitive_deny(self):
        """'Deny' (capitalized) should be rejected — only lowercase is valid."""
        with pytest.raises(ValueError, match="Invalid decision 'Deny'"):
            PolicyRule(decision="Deny", match={"module": "shell"})


class TestMatchKeyValidation:
    """Validate that unknown match keys are rejected at construction."""

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="Unknown match key 'bogus'"):
            PolicyRule(decision="deny", match={"bogus": "val"})

    def test_param_dot_prefix_accepted(self):
        rule = PolicyRule(decision="deny", match={"param.state": "absent"})
        assert "param.state" in rule.match

    def test_valid_keys_accepted(self):
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "host": "prod-*", "environment": "prod"},
        )
        assert len(rule.match) == 3


class TestFromFileValidation:
    """Validate that YAML files with bad rules fail on load."""

    def test_yaml_with_typo_decision_fails(self, tmp_path):
        policy_file = tmp_path / "bad.yaml"
        policy_file.write_text(
            "rules:\n  - decision: dennied\n    match:\n      module: shell\n"
        )
        with pytest.raises(ValueError, match="Invalid decision 'dennied'"):
            Policy.from_file(policy_file)
