"""Runtime policy enforcement verification.

These tests verify that the policy engine actually prevents execution
at runtime — not just that it raises an exception, but that the denied
action's side effects never occur.

This closes the gap identified by belief ai-safety-claims-structurally-fragile:
safety conclusions were derived from "issues resolved" rather than
"behavior verified."

Requires localhost SSH (same as Layer 2 integration tests).
"""


import pytest

from ftl2.automation.context import AutomationContext
from ftl2.policy import PolicyDeniedError

pytestmark = pytest.mark.integration


# ── Local execution (no SSH) ────────────────────────────────────────────


async def test_local_policy_denial_prevents_side_effects(tmp_path):
    """Denied command never creates the marker file (local execution)."""
    marker = tmp_path / "must_not_exist.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: command\n"
        "    reason: blocked by test policy\n"
    )

    async with AutomationContext(
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        with pytest.raises(PolicyDeniedError):
            await ftl.command(cmd=f"touch {marker}")

    assert not marker.exists(), "Denied command created a file — policy did not prevent execution"


async def test_local_policy_denial_shell_equivalence(tmp_path):
    """Denying 'shell' also blocks 'command' via equivalence groups."""
    marker = tmp_path / "equiv_marker.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: shell\n"
        "    reason: shell equivalence test\n"
    )

    async with AutomationContext(
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        with pytest.raises(PolicyDeniedError):
            await ftl.command(cmd=f"touch {marker}")

    assert not marker.exists(), "Equivalent module bypassed policy"


async def test_local_permitted_module_still_executes(tmp_path):
    """Permitted modules execute normally when policy is active."""
    marker = tmp_path / "should_exist.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: shell\n"
        "    reason: only shell denied\n"
    )

    async with AutomationContext(
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        await ftl.file(path=str(marker), state="touch")

    assert marker.exists(), "Permitted module was blocked"


# ── Remote execution via SSH ────────────────────────────────────────────


async def test_remote_policy_denial_prevents_side_effects(
    tmp_path, localhost_ssh_host, localhost_ssh_inventory,
):
    """Denied command never executes on remote host (real SSH path).

    This is the key test: policy must block execution BEFORE the command
    reaches the gate subprocess on the remote side.
    """
    marker = tmp_path / "remote_must_not_exist.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: command\n"
        "    reason: blocked for remote test\n"
    )

    async with AutomationContext(
        inventory=localhost_ssh_inventory,
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        # run_on catches exceptions and returns error ExecuteResults
        results = await ftl.run_on(
            localhost_ssh_host, "command", cmd=f"touch {marker}",
        )

    assert len(results) == 1
    r = results[0]
    assert not r.success, "Denied module should not succeed"
    assert "denied" in r.error.lower() or "policy" in r.error.lower()
    assert not marker.exists(), (
        "Denied command created a file on remote host — "
        "policy did not prevent execution"
    )


async def test_remote_policy_denial_with_audit_trail(
    tmp_path, localhost_ssh_host, localhost_ssh_inventory,
):
    """Denied remote module produces audit trail but no action results."""
    import json

    marker = tmp_path / "audit_marker.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: command\n"
        "    reason: audit trail test\n"
    )
    record_file = tmp_path / "audit.json"

    async with AutomationContext(
        inventory=localhost_ssh_inventory,
        policy=str(policy_file),
        record=str(record_file),
        quiet=True,
    ) as ftl:
        await ftl.run_on(
            localhost_ssh_host, "command", cmd=f"touch {marker}",
        )

    assert not marker.exists(), "Denied command executed despite policy"

    # Verify audit trail recorded the denial
    data = json.loads(record_file.read_text())
    denied = [d for d in data["policy_decisions"] if d["decision"] == "denied"]
    assert len(denied) == 1
    assert denied[0]["module"] == "command"


async def test_remote_permitted_module_executes_with_policy(
    tmp_path, localhost_ssh_host, localhost_ssh_inventory,
):
    """Permitted modules execute normally on remote host under active policy."""
    marker = tmp_path / "remote_permitted.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: shell\n"
        "    reason: only shell denied\n"
    )

    async with AutomationContext(
        inventory=localhost_ssh_inventory,
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        results = await ftl.run_on(
            localhost_ssh_host, "file", path=str(marker), state="touch",
        )

    assert results[0].success
    assert marker.exists(), "Permitted module was blocked by policy"


async def test_remote_policy_host_scoped_denial(
    tmp_path, localhost_ssh_host, localhost_ssh_inventory,
):
    """Policy deny rule scoped to a specific host pattern blocks execution."""
    marker = tmp_path / "host_scoped.txt"
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "rules:\n"
        "  - decision: deny\n"
        "    match:\n"
        "      module: command\n"
        "      host: localhost-ssh\n"
        "    reason: denied on this host\n"
    )

    async with AutomationContext(
        inventory=localhost_ssh_inventory,
        policy=str(policy_file),
        quiet=True,
    ) as ftl:
        results = await ftl.run_on(
            localhost_ssh_host, "command", cmd=f"touch {marker}",
        )

    assert not results[0].success
    assert not marker.exists(), "Host-scoped deny did not prevent execution"
