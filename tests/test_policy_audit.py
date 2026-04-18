"""Tests for policy audit trail functionality (#71).

Tests structured to avoid triggering module execution (which requires
ftl2.ftl_modules.systemd, not available in this workspace). Instead we:
- Test PolicyRule.to_dict() directly (pure unit tests)
- Call _check_policy() directly on AutomationContext instances
- Test denial paths via automation() (short-circuits before execution)
- Test _write_policy_audit_line() and _write_recording() directly
"""

import json
import time
import uuid

import pytest

from ftl2 import automation
from ftl2.automation.context import AutomationContext
from ftl2.policy import Policy, PolicyDeniedError, PolicyRule

# ── PolicyRule.to_dict() ──────────────────────────────────────────────


class TestPolicyRuleToDict:
    """Tests for PolicyRule.to_dict() serialization."""

    def test_to_dict_basic(self):
        rule = PolicyRule(
            decision="deny",
            match={"module": "shell", "environment": "prod"},
            reason="No shell in production",
        )
        d = rule.to_dict()
        assert d == {
            "decision": "deny",
            "match": {"module": "shell", "environment": "prod"},
            "reason": "No shell in production",
        }

    def test_to_dict_empty_match(self):
        rule = PolicyRule(decision="deny", match={}, reason="block everything")
        d = rule.to_dict()
        assert d["match"] == {}
        assert d["reason"] == "block everything"

    def test_to_dict_no_reason(self):
        rule = PolicyRule(decision="deny", match={"module": "raw"})
        d = rule.to_dict()
        assert d["reason"] == ""

    def test_to_dict_with_param_match(self):
        """Param-based match keys are preserved in to_dict()."""
        rule = PolicyRule(
            decision="deny",
            match={"module": "command", "param.cmd": "rm *"},
            reason="No destructive commands",
        )
        d = rule.to_dict()
        assert d["match"] == {"module": "command", "param.cmd": "rm *"}


# ── _check_policy() direct tests ─────────────────────────────────────


class TestCheckPolicyDirect:
    """Test _check_policy() by calling it directly on AutomationContext.

    This avoids module execution entirely — we only test the policy
    evaluation → audit event → file write → raise pipeline.
    """

    def _make_context(self, *, policy_rules=None, policy_audit=None, on_event=None):
        """Build an AutomationContext with the given policy config."""
        ctx = AutomationContext(
            policy=None,
            policy_audit=str(policy_audit) if policy_audit else None,
            on_event=on_event,
        )
        if policy_rules:
            ctx._policy = Policy(policy_rules)
        return ctx

    def test_permitted_appends_to_policy_decisions(self):
        """A permitted evaluation appends an event to _policy_decisions."""
        ctx = self._make_context()
        ctx._check_policy("file", {"path": "/tmp/x", "state": "touch"})
        assert len(ctx._policy_decisions) == 1
        ev = ctx._policy_decisions[0]
        assert ev["event"] == "policy_evaluation"
        assert ev["decision"] == "permitted"
        assert ev["module"] == "file"
        assert ev["host"] == "localhost"
        assert ev["rule"] is None

    def test_denied_appends_then_raises(self):
        """A denied evaluation appends an event AND raises PolicyDeniedError."""
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="nope")
        ctx = self._make_context(policy_rules=[rule])
        with pytest.raises(PolicyDeniedError, match="nope"):
            ctx._check_policy("shell", {"cmd": "echo hi"})
        assert len(ctx._policy_decisions) == 1
        ev = ctx._policy_decisions[0]
        assert ev["decision"] == "denied"
        assert ev["rule"]["decision"] == "deny"
        assert ev["reason"] == "nope"

    def test_session_id_in_events(self):
        """Every audit event carries the context's session_id."""
        ctx = self._make_context()
        ctx._check_policy("ping", {})
        assert ctx._policy_decisions[0]["session_id"] == ctx.session_id

    def test_session_id_is_valid_uuid4(self):
        """session_id is a valid UUID4 string."""
        ctx = self._make_context()
        parsed = uuid.UUID(ctx.session_id, version=4)
        assert str(parsed) == ctx.session_id

    def test_multiple_evaluations_accumulate(self):
        """Multiple calls accumulate in _policy_decisions in order."""
        ctx = self._make_context()
        ctx._check_policy("file", {"path": "/a"})
        ctx._check_policy("copy", {"src": "/a", "dest": "/b"})
        ctx._check_policy("ping", {})
        assert len(ctx._policy_decisions) == 3
        assert [e["module"] for e in ctx._policy_decisions] == ["file", "copy", "ping"]

    def test_on_event_callback_receives_event(self):
        """The on_event callback fires for each policy evaluation."""
        events = []
        ctx = self._make_context(on_event=events.append)
        ctx._check_policy("file", {"path": "/tmp/x"})
        policy_events = [e for e in events if e.get("event") == "policy_evaluation"]
        assert len(policy_events) == 1
        assert policy_events[0]["module"] == "file"
        assert policy_events[0]["decision"] == "permitted"

    def test_audit_params_override(self):
        """When audit_params is provided, it appears in the event instead of params."""
        ctx = self._make_context()
        original = {"url": "http://example.com"}
        injected = {"url": "http://example.com", "bearer_token": "SECRET"}
        ctx._check_policy("uri", injected, audit_params=original)
        ev = ctx._policy_decisions[0]
        # The event's params should be based on original (pre-injection),
        # not the injected params
        assert "bearer_token" not in json.dumps(ev["params"])

    def test_environment_in_event(self):
        """The context's environment label appears in audit events."""
        ctx = AutomationContext(
            policy=None,
            environment="production",
        )
        ctx._check_policy("file", {"path": "/tmp/x"})
        assert ctx._policy_decisions[0]["environment"] == "production"

    def test_host_parameter_passed_through(self):
        """When host is specified, it appears in the audit event."""
        ctx = self._make_context()
        ctx._check_policy("file", {"path": "/tmp/x"}, host="web-01")
        assert ctx._policy_decisions[0]["host"] == "web-01"


# ── JSONL file writing ────────────────────────────────────────────────


class TestPolicyAuditJsonlFile:
    """Tests for JSON-lines policy audit file persistence."""

    def test_jsonl_file_written_on_permit(self, tmp_path):
        """Permitted evaluations are written to the JSONL file."""
        audit_file = tmp_path / "audit.jsonl"
        ctx = AutomationContext(
            policy=None,
            policy_audit=str(audit_file),
        )
        ctx._check_policy("file", {"path": "/tmp/x", "state": "touch"})

        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "policy_evaluation"
        assert event["decision"] == "permitted"
        assert event["module"] == "file"
        # Timestamp in file is ISO-8601 (not epoch)
        assert "T" in event["timestamp"]
        assert event["session_id"] == ctx.session_id

    def test_jsonl_file_written_on_deny(self, tmp_path):
        """Denied evaluations are also written to the JSONL file."""
        audit_file = tmp_path / "audit.jsonl"
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="blocked")
        ctx = AutomationContext(policy=None, policy_audit=str(audit_file))
        ctx._policy = Policy([rule])

        with pytest.raises(PolicyDeniedError):
            ctx._check_policy("shell", {"cmd": "echo hi"})

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["decision"] == "denied"
        assert event["rule"]["reason"] == "blocked"

    def test_jsonl_appends_multiple_events(self, tmp_path):
        """Multiple evaluations append as separate lines (not overwrite)."""
        audit_file = tmp_path / "audit.jsonl"
        ctx = AutomationContext(policy=None, policy_audit=str(audit_file))
        ctx._check_policy("file", {"path": "/a"})
        ctx._check_policy("copy", {"src": "/a", "dest": "/b"})

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["module"] == "file"
        assert json.loads(lines[1])["module"] == "copy"

    def test_no_file_written_when_policy_audit_is_none(self, tmp_path):
        """When policy_audit is None, no file is created."""
        ctx = AutomationContext(policy=None, policy_audit=None)
        ctx._check_policy("file", {"path": "/tmp/x"})
        # No files should be created in tmp_path
        assert list(tmp_path.iterdir()) == []

    def test_jsonl_each_line_is_valid_json(self, tmp_path):
        """Every line in the JSONL file is independently parseable JSON."""
        audit_file = tmp_path / "audit.jsonl"
        rule = PolicyRule(decision="deny", match={"module": "raw"}, reason="no raw")
        ctx = AutomationContext(policy=None, policy_audit=str(audit_file))
        ctx._policy = Policy([rule])

        ctx._check_policy("file", {"path": "/tmp/a"})  # permitted
        ctx._check_policy("ping", {})  # permitted
        with pytest.raises(PolicyDeniedError):
            ctx._check_policy("raw", {"cmd": "whoami"})  # denied

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "event" in parsed
            assert "timestamp" in parsed
            assert "session_id" in parsed


# ── Record file integration ──────────────────────────────────────────


class TestPolicyAuditInRecordFile:
    """Tests for policy decisions embedded in the record file."""

    def test_policy_decisions_in_record(self, tmp_path):
        """Record file includes policy_decisions array and session_id."""
        record_file = tmp_path / "audit.json"
        ctx = AutomationContext(policy=None, record=str(record_file))
        ctx._start_time = time.time()
        ctx._check_policy("file", {"path": "/tmp/x", "state": "touch"})
        # Manually write the recording (normally done in __aexit__)
        ctx._write_recording()

        data = json.loads(record_file.read_text())
        assert "policy_decisions" in data
        assert len(data["policy_decisions"]) == 1
        assert data["policy_decisions"][0]["decision"] == "permitted"
        assert data["policy_decisions"][0]["module"] == "file"
        assert "session_id" in data
        assert data["session_id"] == ctx.session_id

    def test_record_written_with_only_denied_decisions(self, tmp_path):
        """Record file is written even when all actions are denied (no execution results)."""
        record_file = tmp_path / "audit.json"
        rule = PolicyRule(decision="deny", match={"module": "shell"}, reason="blocked")
        ctx = AutomationContext(policy=None, record=str(record_file))
        ctx._policy = Policy([rule])
        ctx._start_time = time.time()

        with pytest.raises(PolicyDeniedError):
            ctx._check_policy("shell", {"cmd": "echo bad"})

        # The __aexit__ condition is: if self._record_file and (self._results or self._policy_decisions)
        # Since _policy_decisions is non-empty, recording should be written
        assert len(ctx._policy_decisions) == 1
        assert len(ctx._results) == 0
        ctx._write_recording()

        assert record_file.exists()
        data = json.loads(record_file.read_text())
        assert len(data["policy_decisions"]) == 1
        assert data["policy_decisions"][0]["decision"] == "denied"
        assert len(data["actions"]) == 0

    def test_record_timestamps_are_iso8601(self, tmp_path):
        """Timestamps in the record file are ISO-8601, not epoch floats."""
        record_file = tmp_path / "audit.json"
        ctx = AutomationContext(policy=None, record=str(record_file))
        ctx._start_time = time.time()
        ctx._check_policy("ping", {})
        ctx._write_recording()

        data = json.loads(record_file.read_text())
        ts = data["policy_decisions"][0]["timestamp"]
        assert isinstance(ts, str)
        assert "T" in ts  # ISO-8601 format


# ── Denied path via automation() context manager ─────────────────────


class TestPolicyAuditViaDenialPath:
    """Tests using the automation() context manager on denial paths.

    These don't trigger module execution so they avoid the missing
    systemd module.
    """

    @pytest.mark.asyncio
    async def test_deny_emits_event_via_automation(self, tmp_path):
        """Denied actions emit policy_evaluation event through automation()."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: No shell allowed\n"
        )
        events = []
        async with automation(
            policy=str(policy_file), on_event=events.append
        ) as ftl:
            with pytest.raises(PolicyDeniedError):
                await ftl.shell(cmd="echo hello")

        policy_events = [e for e in events if e["event"] == "policy_evaluation"]
        assert len(policy_events) == 1
        ev = policy_events[0]
        assert ev["decision"] == "denied"
        assert ev["module"] == "shell"
        assert ev["rule"]["match"] == {"module": "shell"}

    @pytest.mark.asyncio
    async def test_deny_writes_jsonl_via_automation(self, tmp_path):
        """Denied actions write to JSONL file through automation()."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: command\n"
            "    reason: blocked\n"
        )
        audit_file = tmp_path / "policy-audit.jsonl"
        async with automation(
            policy=str(policy_file), policy_audit=str(audit_file)
        ) as ftl:
            with pytest.raises(PolicyDeniedError):
                await ftl.command(cmd="whoami")

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["decision"] == "denied"
        assert event["module"] == "command"

    @pytest.mark.asyncio
    async def test_deny_writes_record_via_automation(self, tmp_path):
        """Record file is written with denied decisions through automation()."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: blocked\n"
        )
        record_file = tmp_path / "audit.json"
        async with automation(
            policy=str(policy_file), record=str(record_file)
        ) as ftl:
            with pytest.raises(PolicyDeniedError):
                await ftl.shell(cmd="echo bad")

        assert record_file.exists()
        data = json.loads(record_file.read_text())
        assert len(data["policy_decisions"]) == 1
        assert data["policy_decisions"][0]["decision"] == "denied"
        assert len(data["actions"]) == 0

    @pytest.mark.asyncio
    async def test_session_id_accessible_on_context(self, tmp_path):
        """session_id property is accessible on the automation context."""
        async with automation() as ftl:
            sid = ftl.session_id
            assert isinstance(sid, str)
            assert len(sid) == 36  # UUID4 format
            # Validate it's a real UUID
            uuid.UUID(sid, version=4)

    @pytest.mark.asyncio
    async def test_equivalence_group_denial_audited(self, tmp_path):
        """Denying 'shell' also blocks 'command' via equivalence, and both are audited."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: No shell-like modules\n"
        )
        audit_file = tmp_path / "audit.jsonl"
        async with automation(
            policy=str(policy_file), policy_audit=str(audit_file)
        ) as ftl:
            with pytest.raises(PolicyDeniedError):
                await ftl.command(cmd="whoami")

        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["decision"] == "denied"
        assert event["module"] == "command"
        # The rule that matched was the shell deny rule
        assert event["rule"]["match"]["module"] == "shell"
