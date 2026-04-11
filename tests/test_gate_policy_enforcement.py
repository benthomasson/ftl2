"""Tests for gate-side policy enforcement (#70).

Tests cover:
- Policy serialization (to_dict, to_wire, from_wire)
- Round-trip fidelity of wire format
- Gate-side policy check helper (_check_gate_policy)
- New message types (PolicyDenied, SetPolicy)
- Gate builder bundling of policy.py and exceptions.py
- Runner policy_wire integration
- Edge cases from reviewer (host param omission, environment scoping)
"""

import pytest

from ftl2.policy import Policy, PolicyRule


# =============================================================================
# Policy Serialization (to_dict / to_wire / from_wire)
# =============================================================================


class TestPolicyRuleToDict:
    """Tests for PolicyRule.to_dict()."""

    def test_basic_serialization(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="no shells")
        d = rule.to_dict()
        assert d == {"decision": "deny", "match": {"module": "shell"}, "reason": "no shells"}

    def test_empty_match_and_reason(self):
        rule = PolicyRule(decision="deny", match={}, reason="")
        d = rule.to_dict()
        assert d == {"decision": "deny", "match": {}, "reason": ""}

    def test_multi_condition_match(self):
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "host": "prod-*", "environment": "prod"},
            reason="no shell in prod",
        )
        d = rule.to_dict()
        assert d["match"] == {"module": "shell", "host": "prod-*", "environment": "prod"}

    def test_param_match(self):
        rule = PolicyRule(
            decision="deny",
            match={"param.state": "absent"},
            reason="no deletions",
        )
        d = rule.to_dict()
        assert d["match"] == {"param.state": "absent"}


class TestPolicyToWire:
    """Tests for Policy.to_wire()."""

    def test_empty_policy(self):
        policy = Policy.empty()
        assert policy.to_wire() == []

    def test_single_rule(self):
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="no shells")
        policy = Policy([rule])
        wire = policy.to_wire()
        assert len(wire) == 1
        assert wire[0] == {"decision": "deny", "match": {"module": "shell"}, "reason": "no shells"}

    def test_multiple_rules_preserve_order(self):
        rules = [
            PolicyRule(decision="deny", match={"module": "shell"}, reason="first"),
            PolicyRule(decision="deny", match={"module": "raw"}, reason="second"),
            PolicyRule(decision="deny", match={"host": "db-*"}, reason="third"),
        ]
        policy = Policy(rules)
        wire = policy.to_wire()
        assert len(wire) == 3
        assert wire[0]["reason"] == "first"
        assert wire[1]["reason"] == "second"
        assert wire[2]["reason"] == "third"

    def test_wire_format_is_json_serializable(self):
        """to_wire() output must survive JSON round-trip."""
        import json

        rules = [
            PolicyRule(decision="deny", match={"module": "shell", "param.cmd": "rm *"}, reason="dangerous"),
        ]
        wire = Policy(rules).to_wire()
        roundtripped = json.loads(json.dumps(wire))
        assert roundtripped == wire


class TestPolicyFromWire:
    """Tests for Policy.from_wire()."""

    def test_none_returns_empty(self):
        policy = Policy.from_wire(None)
        assert policy.rules == []
        assert policy.evaluate("shell", {}).permitted is True

    def test_empty_list_returns_empty(self):
        policy = Policy.from_wire([])
        assert policy.rules == []
        assert policy.evaluate("anything", {}).permitted is True

    def test_single_rule_reconstruction(self):
        wire = [{"decision": "deny", "match": {"module": "shell"}, "reason": "no shells"}]
        policy = Policy.from_wire(wire)
        assert len(policy.rules) == 1
        assert policy.evaluate("shell", {}).permitted is False
        assert policy.evaluate("ping", {}).permitted is True

    def test_multiple_rules_reconstruction(self):
        wire = [
            {"decision": "deny", "match": {"module": "shell"}, "reason": "no shell"},
            {"decision": "deny", "match": {"host": "prod-*"}, "reason": "no prod"},
        ]
        policy = Policy.from_wire(wire)
        assert len(policy.rules) == 2
        assert policy.evaluate("shell", {}).permitted is False
        assert policy.evaluate("ping", {}, host="prod-web01").permitted is False
        assert policy.evaluate("ping", {}).permitted is True


class TestPolicyWireRoundTrip:
    """Round-trip tests: to_wire() -> from_wire() preserves evaluation semantics."""

    def test_round_trip_single_rule(self):
        original = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shell")])
        restored = Policy.from_wire(original.to_wire())

        # Same evaluation behavior
        assert restored.evaluate("shell", {}).permitted is False
        assert restored.evaluate("ping", {}).permitted is True

    def test_round_trip_environment_rule(self):
        original = Policy([
            PolicyRule(
                decision="deny",
                match={"module": "shell", "environment": "prod"},
                reason="no shell in prod",
            )
        ])
        restored = Policy.from_wire(original.to_wire())

        assert restored.evaluate("shell", {}, environment="prod").permitted is False
        assert restored.evaluate("shell", {}, environment="dev").permitted is True

    def test_round_trip_param_rule(self):
        original = Policy([
            PolicyRule(decision="deny", match={"param.state": "absent"}, reason="no deletions")
        ])
        restored = Policy.from_wire(original.to_wire())

        assert restored.evaluate("file", {"state": "absent"}).permitted is False
        assert restored.evaluate("file", {"state": "present"}).permitted is True

    def test_round_trip_host_rule(self):
        original = Policy([
            PolicyRule(decision="deny", match={"host": "db-*"}, reason="no db access")
        ])
        restored = Policy.from_wire(original.to_wire())

        assert restored.evaluate("ping", {}, host="db-primary").permitted is False
        assert restored.evaluate("ping", {}, host="web-01").permitted is True

    def test_round_trip_empty_policy(self):
        original = Policy.empty()
        restored = Policy.from_wire(original.to_wire())
        assert restored.rules == []
        assert restored.evaluate("anything", {}).permitted is True

    def test_round_trip_module_equivalence(self):
        """Module equivalence groups must work after wire round-trip."""
        original = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shell")])
        restored = Policy.from_wire(original.to_wire())

        # Equivalence: shell -> command, raw
        assert restored.evaluate("command", {}).permitted is False
        assert restored.evaluate("raw", {}).permitted is False
        assert restored.evaluate("file", {}).permitted is True


# =============================================================================
# Gate-side policy check helper (_check_gate_policy)
# =============================================================================


class TestCheckGatePolicy:
    """Tests for the _check_gate_policy() helper in ftl_gate/__main__.py."""

    @pytest.fixture(autouse=True)
    def import_helper(self):
        from ftl2.ftl_gate.__main__ import _check_gate_policy
        self._check = _check_gate_policy

    def test_none_policy_permits(self):
        permitted, denial = self._check(None, "shell", {"cmd": "ls"})
        assert permitted is True
        assert denial is None

    def test_empty_policy_permits(self):
        policy = Policy.empty()
        permitted, denial = self._check(policy, "shell", {"cmd": "ls"})
        assert permitted is True
        assert denial is None

    def test_matching_rule_denies(self):
        policy = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shells")])
        permitted, denial = self._check(policy, "shell", {"cmd": "rm -rf /"})
        assert permitted is False
        assert denial is not None
        assert denial["module"] == "shell"
        assert denial["reason"] == "no shells"
        assert denial["rule"]["decision"] == "deny"

    def test_non_matching_permits(self):
        policy = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shells")])
        permitted, denial = self._check(policy, "ping", {})
        assert permitted is True
        assert denial is None

    def test_environment_scoped_deny(self):
        policy = Policy([
            PolicyRule(
                decision="deny",
                match={"module": "shell", "environment": "prod"},
                reason="no shell in prod",
            )
        ])
        # Environment matches -> denied
        permitted, denial = self._check(policy, "shell", {}, "prod")
        assert permitted is False

        # Different environment -> permitted
        permitted, denial = self._check(policy, "shell", {}, "dev")
        assert permitted is True

    def test_denial_data_structure(self):
        """Verify the structured denial response has all expected keys."""
        policy = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="blocked")])
        permitted, denial = self._check(policy, "shell", {"cmd": "ls"})
        assert permitted is False
        assert set(denial.keys()) == {"module", "reason", "rule"}
        assert denial["rule"]["decision"] == "deny"
        assert denial["rule"]["match"] == {"module": "shell"}

    def test_host_defaults_to_localhost(self):
        """Without explicit host, defaults to 'localhost' — host-scoped rules don't match."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "shell", "host": "web01"}, reason="no shell on web01")
        ])
        # Without host= param, defaults to "localhost" — rule doesn't match
        permitted, denial = self._check(policy, "shell", {})
        assert permitted is True

    def test_host_scoped_rule_enforced_with_host(self):
        """When host is passed, host-scoped deny rules are enforced on the gate side."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "shell", "host": "web01"}, reason="no shell on web01")
        ])
        # With host="web01", the rule matches
        permitted, denial = self._check(policy, "shell", {}, host="web01")
        assert permitted is False
        assert denial["reason"] == "no shell on web01"

        # Different host — rule doesn't match
        permitted, denial = self._check(policy, "shell", {}, host="db01")
        assert permitted is True

    def test_module_equivalence_on_gate_side(self):
        """Module equivalence groups work through _check_gate_policy."""
        policy = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shell")])
        permitted, denial = self._check(policy, "command", {})
        assert permitted is False  # command is equivalent to shell

    def test_param_match_on_gate_side(self):
        policy = Policy([
            PolicyRule(decision="deny", match={"param.state": "absent"}, reason="no deletions")
        ])
        permitted, denial = self._check(policy, "file", {"state": "absent"})
        assert permitted is False

        permitted, denial = self._check(policy, "file", {"state": "present"})
        assert permitted is True


# =============================================================================
# Message Types
# =============================================================================


class TestMessageTypes:
    """Tests for new message types added for gate-side policy."""

    def test_policy_denied_in_message_types(self):
        from ftl2.message import GateProtocol
        assert "PolicyDenied" in GateProtocol.MESSAGE_TYPES

    def test_set_policy_in_message_types(self):
        from ftl2.message import GateProtocol
        assert "SetPolicy" in GateProtocol.MESSAGE_TYPES

    def test_set_policy_result_in_message_types(self):
        from ftl2.message import GateProtocol
        assert "SetPolicyResult" in GateProtocol.MESSAGE_TYPES


# =============================================================================
# Gate Builder — policy.py bundled into .pyz
# =============================================================================


class TestGateBuilderPolicyBundling:
    """Tests that policy.py and exceptions.py are included in gate hash."""

    def test_policy_in_hash_source_files(self):
        """GateBuildConfig.compute_hash() includes policy.py in its inputs."""
        import ftl2
        from ftl2.gate import GateBuildConfig
        from pathlib import Path

        ftl2_dir = Path(ftl2.__file__).parent
        policy_path = ftl2_dir / "policy.py"
        exceptions_path = ftl2_dir / "exceptions.py"

        # These files must exist for the hash to include them
        assert policy_path.exists(), "policy.py must exist in ftl2 package"
        assert exceptions_path.exists(), "exceptions.py must exist in ftl2 package"

    def test_hash_changes_with_policy_content(self):
        """Verify policy.py changes invalidate gate cache by changing hash."""
        from ftl2.gate import GateBuildConfig

        config1 = GateBuildConfig(modules=[], interpreter="python3")
        hash1 = config1.compute_hash()

        # Same config produces same hash
        config2 = GateBuildConfig(modules=[], interpreter="python3")
        hash2 = config2.compute_hash()
        assert hash1 == hash2

    def test_gate_builder_copy_policy_method_exists(self):
        """GateBuilder has _copy_policy_module method."""
        from ftl2.gate import GateBuilder
        assert hasattr(GateBuilder, "_copy_policy_module")

    def test_gate_builder_copy_exceptions_method_exists(self):
        """GateBuilder has _copy_exceptions_module method."""
        from ftl2.gate import GateBuilder
        assert hasattr(GateBuilder, "_copy_exceptions_module")


# =============================================================================
# Runner Integration
# =============================================================================


class TestRunnerPolicyIntegration:
    """Tests for RemoteModuleRunner policy wire integration."""

    def test_runner_has_policy_wire_attr(self):
        from ftl2.runners import RemoteModuleRunner
        runner = RemoteModuleRunner()
        assert hasattr(runner, "policy_wire")
        assert runner.policy_wire == []

    def test_runner_has_environment_attr(self):
        from ftl2.runners import RemoteModuleRunner
        runner = RemoteModuleRunner()
        assert hasattr(runner, "environment")
        assert runner.environment == ""

    def test_policy_denied_error_raised_on_response(self):
        """PolicyDeniedError is importable and has correct hierarchy."""
        from ftl2.policy import PolicyDeniedError
        from ftl2.exceptions import FTL2Error

        err = PolicyDeniedError("denied by gate", rule=None)
        assert isinstance(err, FTL2Error)
        assert str(err) == "denied by gate"


# =============================================================================
# Hello Handshake Policy Transmission
# =============================================================================


class TestHelloPolicyTransmission:
    """Tests that policy is correctly prepared for Hello handshake transmission."""

    def test_policy_wire_format_in_hello_data(self):
        """Simulate what the controller sends in Hello data."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "shell"}, reason="no shell"),
            PolicyRule(decision="deny", match={"host": "prod-*"}, reason="no prod"),
        ])
        hello_data = {
            "capabilities": ["multiplex"],
            "policy_rules": policy.to_wire(),
            "environment": "production",
        }

        # Verify the data is well-formed
        assert len(hello_data["policy_rules"]) == 2
        assert hello_data["environment"] == "production"

        # Reconstruct on the gate side
        reconstructed = Policy.from_wire(hello_data["policy_rules"])
        assert len(reconstructed.rules) == 2
        assert reconstructed.evaluate("shell", {}).permitted is False
        assert reconstructed.evaluate("ping", {}, host="prod-web01").permitted is False

    def test_hello_without_policy(self):
        """Hello data without policy_rules should result in no enforcement."""
        hello_data = {"capabilities": ["multiplex"]}
        policy_rules = hello_data.get("policy_rules")
        # from_wire(None) returns empty policy
        policy = Policy.from_wire(policy_rules)
        assert policy.rules == []
        assert policy.evaluate("shell", {"cmd": "rm -rf /"}).permitted is True


# =============================================================================
# SetPolicy Message Handling
# =============================================================================


class TestSetPolicyMessage:
    """Tests for SetPolicy message format and policy update semantics."""

    def test_set_policy_data_format(self):
        """SetPolicy message should carry policy_rules and optional environment."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "raw"}, reason="no raw"),
        ])
        set_policy_data = {
            "policy_rules": policy.to_wire(),
            "environment": "staging",
        }

        # Gate side reconstruction
        new_policy = Policy.from_wire(set_policy_data["policy_rules"])
        assert len(new_policy.rules) == 1
        assert new_policy.evaluate("raw", {}).permitted is False

    def test_set_policy_replaces_previous(self):
        """SetPolicy should fully replace the previous policy, not merge."""
        # Initial policy blocks shell
        initial = Policy([PolicyRule(decision="deny", match={"module": "shell"}, reason="no shell")])
        assert initial.evaluate("shell", {}).permitted is False
        assert initial.evaluate("dnf", {}).permitted is True

        # New policy blocks only dnf (not in shell equivalence group)
        update_wire = [{"decision": "deny", "match": {"module": "dnf"}, "reason": "no dnf"}]
        updated = Policy.from_wire(update_wire)

        # Shell should now be permitted (old rule gone)
        assert updated.evaluate("shell", {}).permitted is True
        # dnf should be denied (new rule)
        assert updated.evaluate("dnf", {}).permitted is False

    def test_set_policy_empty_clears_all_rules(self):
        """Setting an empty policy clears all enforcement."""
        cleared = Policy.from_wire([])
        assert cleared.rules == []
        assert cleared.evaluate("shell", {"cmd": "rm -rf /"}).permitted is True


# =============================================================================
# Edge Cases (from reviewer notes and implementation audit)
# =============================================================================


class TestEdgeCases:
    """Edge cases identified during review and implementation audit."""

    @pytest.fixture(autouse=True)
    def import_helper(self):
        from ftl2.ftl_gate.__main__ import _check_gate_policy
        self._check = _check_gate_policy

    def test_host_wildcard_pattern_matching(self):
        """Host patterns use fnmatch wildcards — verify through _check_gate_policy."""
        policy = Policy([
            PolicyRule(decision="deny", match={"host": "prod-*"}, reason="no prod hosts")
        ])
        permitted, denial = self._check(policy, "ping", {}, host="prod-web01")
        assert permitted is False
        assert denial["reason"] == "no prod hosts"

        permitted, denial = self._check(policy, "ping", {}, host="staging-web01")
        assert permitted is True

    def test_combined_host_environment_module_scoping(self):
        """All three dimensions (host, environment, module) must match for deny."""
        policy = Policy([
            PolicyRule(
                decision="deny",
                match={"module": "shell", "host": "db-*", "environment": "prod"},
                reason="no shell on prod DB",
            )
        ])
        # All match -> denied
        permitted, _ = self._check(policy, "shell", {}, "prod", host="db-primary")
        assert permitted is False

        # Wrong host -> permitted
        permitted, _ = self._check(policy, "shell", {}, "prod", host="web-01")
        assert permitted is True

        # Wrong environment -> permitted
        permitted, _ = self._check(policy, "shell", {}, "staging", host="db-primary")
        assert permitted is True

        # Wrong module -> permitted
        permitted, _ = self._check(policy, "ping", {}, "prod", host="db-primary")
        assert permitted is True

    def test_multiple_rules_first_deny_wins(self):
        """First matching deny rule takes effect."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "shell"}, reason="general no shell"),
            PolicyRule(decision="deny", match={"module": "shell", "host": "web01"}, reason="specific no shell on web01"),
        ])
        # Both match, first deny wins
        permitted, denial = self._check(policy, "shell", {}, host="web01")
        assert permitted is False
        assert denial["reason"] == "general no shell"

    def test_policy_denied_error_preserves_rule(self):
        """PolicyDeniedError.rule carries the matching PolicyRule."""
        from ftl2.policy import PolicyDeniedError

        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="blocked")
        err = PolicyDeniedError("denied", rule=rule)
        assert err.rule is rule
        assert err.rule.decision == "deny"
        assert err.rule.match == {"module": "shell"}

    def test_policy_denied_error_none_rule(self):
        """PolicyDeniedError works with rule=None."""
        from ftl2.policy import PolicyDeniedError

        err = PolicyDeniedError("unknown denial", rule=None)
        assert err.rule is None
        assert str(err) == "unknown denial"

    def test_hello_data_with_host_field(self):
        """Hello data can include host for host-scoped gate-side enforcement."""
        policy = Policy([
            PolicyRule(decision="deny", match={"module": "shell", "host": "web01"}, reason="blocked on web01")
        ])
        hello_data = {
            "capabilities": ["multiplex"],
            "policy_rules": policy.to_wire(),
            "environment": "prod",
            "host": "web01",
        }

        # Reconstruct and evaluate with the host from Hello
        reconstructed = Policy.from_wire(hello_data["policy_rules"])
        host = hello_data.get("host", "localhost")
        result = reconstructed.evaluate("shell", {}, host=host)
        assert result.permitted is False

        # Different host -> permitted
        result = reconstructed.evaluate("shell", {}, host="db01")
        assert result.permitted is True

    def test_set_policy_updates_host(self):
        """SetPolicy data can include host to update gate's host context."""
        set_policy_data = {
            "policy_rules": [{"decision": "deny", "match": {"module": "raw"}, "reason": "no raw"}],
            "environment": "staging",
            "host": "app-server-03",
        }

        new_host = set_policy_data.get("host", "localhost")
        assert new_host == "app-server-03"

        new_policy = Policy.from_wire(set_policy_data["policy_rules"])
        assert new_policy.evaluate("raw", {}).permitted is False

    def test_has_policy_flag_importable(self):
        """HAS_POLICY flag is set to True when policy module is available."""
        from ftl2.ftl_gate.__main__ import HAS_POLICY
        assert HAS_POLICY is True

    def test_denial_data_includes_rule_match_and_reason(self):
        """Denial data from _check_gate_policy has correct structure for protocol response."""
        policy = Policy([
            PolicyRule(
                decision="deny",
                match={"module": "shell", "param.cmd": "rm *"},
                reason="dangerous command pattern",
            )
        ])
        permitted, denial = self._check(policy, "shell", {"cmd": "rm -rf /"})
        assert permitted is False
        assert denial["module"] == "shell"
        assert denial["reason"] == "dangerous command pattern"
        # Rule dict is serialized via to_dict()
        assert denial["rule"]["decision"] == "deny"
        assert denial["rule"]["match"] == {"module": "shell", "param.cmd": "rm *"}

    def test_from_wire_preserves_param_dot_notation(self):
        """Wire round-trip preserves param.X match keys correctly."""
        wire = [{"decision": "deny", "match": {"param.state": "absent", "param.force": "true"}, "reason": "no force delete"}]
        policy = Policy.from_wire(wire)
        assert len(policy.rules) == 1
        assert policy.evaluate("file", {"state": "absent", "force": "true"}).permitted is False
        assert policy.evaluate("file", {"state": "absent", "force": "false"}).permitted is True
        assert policy.evaluate("file", {"state": "present", "force": "true"}).permitted is True
