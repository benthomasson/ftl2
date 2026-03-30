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


class TestPolicyFromFiles:
    """Tests for Policy.from_files()."""

    def test_concatenates_rules_in_order(self, tmp_path):
        (tmp_path / "base.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: base-shell\n"
        )
        (tmp_path / "extra.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: command}\n"
            "    reason: extra-command\n"
        )
        policy = Policy.from_files([tmp_path / "base.yaml", tmp_path / "extra.yaml"])
        assert len(policy.rules) == 2
        assert policy.rules[0].reason == "base-shell"
        assert policy.rules[1].reason == "extra-command"

    def test_empty_list_returns_empty_policy(self):
        policy = Policy.from_files([])
        assert policy.rules == []
        assert policy.evaluate("anything", {}).permitted is True

    def test_single_file_same_as_from_file(self, tmp_path):
        (tmp_path / "only.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: only\n"
        )
        from_files = Policy.from_files([tmp_path / "only.yaml"])
        from_file = Policy.from_file(tmp_path / "only.yaml")
        assert len(from_files.rules) == len(from_file.rules)
        assert from_files.rules[0].reason == from_file.rules[0].reason

    def test_first_match_wins_across_files(self, tmp_path):
        """Earlier file's rules are evaluated first (first-match-wins)."""
        (tmp_path / "first.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: from-first-file\n"
        )
        (tmp_path / "second.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: from-second-file\n"
        )
        policy = Policy.from_files([tmp_path / "first.yaml", tmp_path / "second.yaml"])
        result = policy.evaluate("shell", {})
        assert result.permitted is False
        assert result.reason == "from-first-file"


class TestPolicyFromDirectory:
    """Tests for Policy.from_directory()."""

    def test_loads_alphabetically(self, tmp_path):
        (tmp_path / "b.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: command}\n"
            "    reason: from-b\n"
        )
        (tmp_path / "a.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: from-a\n"
        )
        policy = Policy.from_directory(tmp_path)
        assert len(policy.rules) == 2
        # a.yaml sorts before b.yaml
        assert policy.rules[0].reason == "from-a"
        assert policy.rules[1].reason == "from-b"

    def test_ignores_non_yaml_files(self, tmp_path):
        (tmp_path / "policy.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: yaml-rule\n"
        )
        (tmp_path / "readme.txt").write_text("not a policy")
        (tmp_path / "notes.md").write_text("also not a policy")
        policy = Policy.from_directory(tmp_path)
        assert len(policy.rules) == 1

    def test_loads_yml_extension(self, tmp_path):
        (tmp_path / "policy.yml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match: {module: shell}\n"
            "    reason: yml-rule\n"
        )
        policy = Policy.from_directory(tmp_path)
        assert len(policy.rules) == 1
        assert policy.rules[0].reason == "yml-rule"

    def test_empty_directory_returns_empty_policy(self, tmp_path):
        policy = Policy.from_directory(tmp_path)
        assert policy.rules == []
        assert policy.evaluate("anything", {}).permitted is True

    def test_not_a_directory_raises(self, tmp_path):
        f = tmp_path / "file.yaml"
        f.write_text("rules: []\n")
        with pytest.raises(NotADirectoryError):
            Policy.from_directory(f)

    def test_from_file_rejects_directory(self, tmp_path):
        with pytest.raises(IsADirectoryError, match="is a directory"):
            Policy.from_file(tmp_path)


class TestPolicyCaseSensitivity:
    """Tests that pattern matching is case-sensitive on all platforms (issue #42)."""

    def test_host_pattern_is_case_sensitive(self):
        rule = PolicyRule(decision="deny", match={"host": "PROD-*"}, reason="No prod")
        policy = Policy([rule])
        # Exact case matches
        assert policy.evaluate("ping", {}, host="PROD-web-01").permitted is False
        # Different case must NOT match (platform-independent)
        assert policy.evaluate("ping", {}, host="prod-web-01").permitted is True
        assert policy.evaluate("ping", {}, host="Prod-web-01").permitted is True

    def test_module_pattern_is_case_sensitive(self):
        rule = PolicyRule(decision="deny", match={"module": "Shell"}, reason="No Shell")
        policy = Policy([rule])
        assert policy.evaluate("Shell", {}).permitted is False
        assert policy.evaluate("shell", {}).permitted is True
        assert policy.evaluate("SHELL", {}).permitted is True

    def test_environment_pattern_is_case_sensitive(self):
        rule = PolicyRule(decision="deny", match={"environment": "Prod"}, reason="No prod")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, environment="Prod").permitted is False
        assert policy.evaluate("ping", {}, environment="prod").permitted is True
        assert policy.evaluate("ping", {}, environment="PROD").permitted is True

    def test_param_pattern_is_case_sensitive(self):
        rule = PolicyRule(decision="deny", match={"param.state": "Absent"}, reason="No delete")
        policy = Policy([rule])
        assert policy.evaluate("file", {"state": "Absent"}).permitted is False
        assert policy.evaluate("file", {"state": "absent"}).permitted is True

    def test_wildcard_pattern_is_case_sensitive(self):
        rule = PolicyRule(decision="deny", match={"host": "DB-*"}, reason="No DB")
        policy = Policy([rule])
        assert policy.evaluate("ping", {}, host="DB-primary").permitted is False
        assert policy.evaluate("ping", {}, host="db-primary").permitted is True


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
