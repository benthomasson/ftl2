"""Extended tests for gate lifecycle management commands (issue #68).

Covers edge cases and scenarios not in test_gate_lifecycle.py:
- Multi-host deploy (group)
- Deploy with partial failure
- Drain exception handling and timeout passthrough
- Upgrade parallel strategy
- Upgrade with no existing gate (skip drain)
- Restart with no cached gate (reconnect only)
- Decommission full lifecycle (drain + close + decommission)
- Decommission cleanup=False
- Decommission with no cached gate
- Become-aware cache keys on upgrade, restart, decommission
- SSHHost.connection property guard
- Decommission exception during SSH operations
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ftl2.types import BecomeConfig, HostConfig, gate_cache_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx_with_hosts(hosts: list[HostConfig], group_name: str | None = None):
    """Build a minimal AutomationContext with inventory and runner mocks."""
    from ftl2.automation.context import AutomationContext

    ctx = AutomationContext.__new__(AutomationContext)
    ctx._inventory = MagicMock()
    ctx._remote_runner = MagicMock()

    if group_name:
        group = MagicMock()
        group.list_hosts.return_value = hosts
        ctx._inventory.get_group.return_value = group
    else:
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {h.name: h for h in hosts}

    return ctx


# ---------------------------------------------------------------------------
# gate_deploy — multi-host and error handling
# ---------------------------------------------------------------------------

class TestGateDeployMultiHost:
    """Tests for gate_deploy operating on multiple hosts."""

    @pytest.mark.asyncio
    async def test_deploy_group_deploys_all_hosts(self):
        """gate_deploy on a group deploys to every host in the group."""
        hosts = [
            HostConfig(name="web01", ansible_host="1.1.1.1"),
            HostConfig(name="web02", ansible_host="2.2.2.2"),
            HostConfig(name="web03", ansible_host="3.3.3.3"),
        ]
        ctx = _make_ctx_with_hosts(hosts, group_name="webservers")
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_deploy("webservers")

        assert len(results) == 3
        assert all(r["status"] == "ok" for r in results)
        assert [r["host"] for r in results] == ["web01", "web02", "web03"]
        assert ctx._get_or_create_gate.call_count == 3

    @pytest.mark.asyncio
    async def test_deploy_partial_failure(self):
        """If one host fails during deploy, remaining hosts still attempted."""
        hosts = [
            HostConfig(name="web01", ansible_host="1.1.1.1"),
            HostConfig(name="web02", ansible_host="2.2.2.2"),
        ]
        ctx = _make_ctx_with_hosts(hosts, group_name="webservers")

        call_count = 0

        async def mock_create(host, register_subsystem=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if host.name == "web01":
                raise ConnectionError("SSH connection refused")
            return MagicMock()

        ctx._get_or_create_gate = mock_create

        results = await ctx.gate_deploy("webservers")

        assert len(results) == 2
        assert results[0]["host"] == "web01"
        assert results[0]["status"] == "error"
        assert "SSH connection refused" in results[0]["message"]
        assert results[1]["host"] == "web02"
        assert results[1]["status"] == "ok"


# ---------------------------------------------------------------------------
# gate_drain — error handling and timeout
# ---------------------------------------------------------------------------

class TestGateDrainEdgeCases:
    """Edge cases for gate_drain."""

    @pytest.mark.asyncio
    async def test_drain_exception_returns_error(self):
        """Exception during _drain_gate is captured as error status."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        cache_key = gate_cache_key(host.name, host.become_config)
        ctx._remote_runner.gate_cache = {cache_key: MagicMock()}
        ctx._remote_runner._drain_gate = AsyncMock(
            side_effect=asyncio.TimeoutError("Drain timed out")
        )

        results = await ctx.gate_drain("web01", timeout_seconds=5)

        assert len(results) == 1
        assert results[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_drain_passes_custom_timeout(self):
        """timeout_seconds is forwarded to _drain_gate."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        cache_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {cache_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(
            return_value={"status": "drained", "completed": 0, "in_flight": 0}
        )

        await ctx.gate_drain("web01", timeout_seconds=42)

        ctx._remote_runner._drain_gate.assert_called_once_with(mock_gate, 42)

    @pytest.mark.asyncio
    async def test_drain_default_timeout(self):
        """Default timeout_seconds is 300."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        cache_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {cache_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(
            return_value={"status": "drained", "completed": 0, "in_flight": 0}
        )

        await ctx.gate_drain("web01")

        ctx._remote_runner._drain_gate.assert_called_once_with(mock_gate, 300)


# ---------------------------------------------------------------------------
# gate_upgrade — parallel strategy and no-existing-gate
# ---------------------------------------------------------------------------

class TestGateUpgradeEdgeCases:
    """Edge cases for gate_upgrade."""

    @pytest.mark.asyncio
    async def test_parallel_upgrade_all_hosts(self):
        """strategy='parallel' upgrades all hosts concurrently."""
        hosts = [
            HostConfig(name="web01", ansible_host="1.1.1.1"),
            HostConfig(name="web02", ansible_host="2.2.2.2"),
        ]
        ctx = _make_ctx_with_hosts(hosts, group_name="webservers")

        for h in hosts:
            key = gate_cache_key(h.name, h.become_config)
            ctx._remote_runner.gate_cache[key] = MagicMock()

        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_upgrade("webservers", strategy="parallel")

        assert len(results) == 2
        assert all(r["status"] == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_upgrade_no_existing_gate_skips_drain(self):
        """Upgrade with no cached gate skips drain/close, just creates new gate."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        # Empty gate cache — no existing gate
        ctx._remote_runner.gate_cache = {}
        ctx._remote_runner._drain_gate = AsyncMock()
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_upgrade("web01")

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        # Drain and close should NOT have been called
        ctx._remote_runner._drain_gate.assert_not_called()
        ctx._remote_runner._close_gate.assert_not_called()
        # But gate creation should still happen
        ctx._get_or_create_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_rolling_upgrade_all_succeed(self):
        """Rolling upgrade proceeds through all hosts when none fail."""
        hosts = [
            HostConfig(name="web01", ansible_host="1.1.1.1"),
            HostConfig(name="web02", ansible_host="2.2.2.2"),
            HostConfig(name="web03", ansible_host="3.3.3.3"),
        ]
        ctx = _make_ctx_with_hosts(hosts, group_name="webservers")

        for h in hosts:
            key = gate_cache_key(h.name, h.become_config)
            ctx._remote_runner.gate_cache[key] = MagicMock()

        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_upgrade("webservers", strategy="rolling")

        assert len(results) == 3
        assert all(r["status"] == "ok" for r in results)

    @pytest.mark.asyncio
    async def test_upgrade_become_aware_cache_key(self):
        """gate_upgrade uses become-aware cache key to find gates."""
        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="root",
        )
        ctx = _make_ctx_with_hosts([host])

        become_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {become_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_upgrade("web01")

        assert results[0]["status"] == "ok"
        # Drain should have been called with the correct gate
        ctx._remote_runner._drain_gate.assert_called_once_with(mock_gate, 300)


# ---------------------------------------------------------------------------
# gate_restart — no cached gate
# ---------------------------------------------------------------------------

class TestGateRestartEdgeCases:
    """Edge cases for gate_restart."""

    @pytest.mark.asyncio
    async def test_restart_no_cached_gate_just_reconnects(self):
        """Restart with no cached gate skips drain/close, just creates new gate."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        ctx._remote_runner.gate_cache = {}
        ctx._remote_runner._drain_gate = AsyncMock()
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_restart("web01")

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        ctx._remote_runner._drain_gate.assert_not_called()
        ctx._remote_runner._close_gate.assert_not_called()
        ctx._get_or_create_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_become_aware_cache_key(self):
        """gate_restart uses become-aware cache key to find gates."""
        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="root",
        )
        ctx = _make_ctx_with_hosts([host])

        become_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {become_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_restart("web01")

        assert results[0]["status"] == "ok"
        ctx._remote_runner._drain_gate.assert_called_once()
        ctx._remote_runner._close_gate.assert_called_once()


# ---------------------------------------------------------------------------
# gate_decommission — full lifecycle, cleanup, and edge cases
# ---------------------------------------------------------------------------

class TestGateDecommissionEdgeCases:
    """Edge cases for gate_decommission."""

    @pytest.mark.asyncio
    async def test_decommission_full_lifecycle(self):
        """Decommission drains, closes, then runs SSH decommission."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        cache_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {cache_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._remote_runner._decommission_gate_subsystem = AsyncMock(
            return_value={"status": "ok", "message": "Gate subsystem decommissioned"}
        )

        mock_ssh = MagicMock()
        mock_ssh.connection = MagicMock()
        ctx._get_ssh_connection = AsyncMock(return_value=mock_ssh)

        results = await ctx.gate_decommission("web01", cleanup=True)

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        ctx._remote_runner._drain_gate.assert_called_once()
        ctx._remote_runner._close_gate.assert_called_once()
        ctx._remote_runner._decommission_gate_subsystem.assert_called_once_with(
            mock_ssh.connection, cleanup=True
        )

    @pytest.mark.asyncio
    async def test_decommission_no_cached_gate(self):
        """Decommission with no cached gate skips drain/close, runs SSH decommission."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        ctx._remote_runner.gate_cache = {}
        ctx._remote_runner._drain_gate = AsyncMock()
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._remote_runner._decommission_gate_subsystem = AsyncMock(
            return_value={"status": "ok", "message": "Subsystem not registered (already decommissioned)"}
        )

        mock_ssh = MagicMock()
        mock_ssh.connection = MagicMock()
        ctx._get_ssh_connection = AsyncMock(return_value=mock_ssh)

        results = await ctx.gate_decommission("web01")

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        ctx._remote_runner._drain_gate.assert_not_called()
        ctx._remote_runner._close_gate.assert_not_called()
        ctx._remote_runner._decommission_gate_subsystem.assert_called_once()

    @pytest.mark.asyncio
    async def test_decommission_cleanup_false(self):
        """cleanup=False is forwarded to _decommission_gate_subsystem."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        ctx._remote_runner.gate_cache = {}
        ctx._remote_runner._decommission_gate_subsystem = AsyncMock(
            return_value={"status": "ok", "message": "Gate subsystem decommissioned"}
        )

        mock_ssh = MagicMock()
        mock_ssh.connection = MagicMock()
        ctx._get_ssh_connection = AsyncMock(return_value=mock_ssh)

        await ctx.gate_decommission("web01", cleanup=False)

        ctx._remote_runner._decommission_gate_subsystem.assert_called_once_with(
            mock_ssh.connection, cleanup=False
        )

    @pytest.mark.asyncio
    async def test_decommission_become_aware_cache_key(self):
        """gate_decommission uses become-aware cache key to find gates."""
        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="root",
        )
        ctx = _make_ctx_with_hosts([host])

        become_key = gate_cache_key(host.name, host.become_config)
        mock_gate = MagicMock()
        ctx._remote_runner.gate_cache = {become_key: mock_gate}
        ctx._remote_runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        ctx._remote_runner._close_gate = AsyncMock()
        ctx._remote_runner._decommission_gate_subsystem = AsyncMock(
            return_value={"status": "ok", "message": "Decommissioned"}
        )

        mock_ssh = MagicMock()
        mock_ssh.connection = MagicMock()
        ctx._get_ssh_connection = AsyncMock(return_value=mock_ssh)

        results = await ctx.gate_decommission("web01")

        assert results[0]["status"] == "ok"
        # Should have drained and closed the correctly-keyed gate
        ctx._remote_runner._drain_gate.assert_called_once()
        ctx._remote_runner._close_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_decommission_ssh_exception_returns_error(self):
        """Exception during SSH decommission is captured as error."""
        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx = _make_ctx_with_hosts([host])

        ctx._remote_runner.gate_cache = {}
        ctx._get_ssh_connection = AsyncMock(
            side_effect=ConnectionError("SSH connect failed")
        )

        results = await ctx.gate_decommission("web01")

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "SSH connect failed" in results[0]["message"]


# ---------------------------------------------------------------------------
# SSHHost.connection property
# ---------------------------------------------------------------------------

class TestSSHHostConnectionProperty:
    """Tests for SSHHost.connection property guard."""

    def test_connection_raises_when_not_connected(self):
        """SSHHost.connection raises RuntimeError before connect() is called."""
        from ftl2.ssh import SSHHost

        ssh = SSHHost.__new__(SSHHost)
        ssh._conn = None

        with pytest.raises(RuntimeError, match="Not connected"):
            _ = ssh.connection

    def test_connection_returns_conn_when_connected(self):
        """SSHHost.connection returns the underlying connection after connect()."""
        from ftl2.ssh import SSHHost

        ssh = SSHHost.__new__(SSHHost)
        mock_conn = MagicMock()
        ssh._conn = mock_conn

        assert ssh.connection is mock_conn


# ---------------------------------------------------------------------------
# gate_cache_key unit tests
# ---------------------------------------------------------------------------

class TestGateCacheKey:
    """Tests for gate_cache_key behavior."""

    def test_bare_key_without_become(self):
        """No become config returns plain host name."""
        assert gate_cache_key("web01") == "web01"

    def test_bare_key_with_become_disabled(self):
        """become=False returns plain host name."""
        bc = BecomeConfig(become=False)
        assert gate_cache_key("web01", bc) == "web01"

    def test_become_key_with_sudo(self):
        """become=True with sudo returns composite key."""
        bc = BecomeConfig(become=True, become_user="root", become_method="sudo")
        assert gate_cache_key("web01", bc) == "web01:become=root:method=sudo"

    def test_become_key_with_doas(self):
        """become=True with doas returns composite key."""
        bc = BecomeConfig(become=True, become_user="admin", become_method="doas")
        assert gate_cache_key("web01", bc) == "web01:become=admin:method=doas"

    def test_host_become_config_property(self):
        """HostConfig.become_config generates the correct BecomeConfig."""
        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="deploy",
        )
        bc = host.become_config
        assert bc.become is True
        assert bc.become_user == "deploy"
        key = gate_cache_key(host.name, bc)
        assert "deploy" in key
