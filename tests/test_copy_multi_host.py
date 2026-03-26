"""Tests for copy() multi-host fan-out (issue #27).

Verifies that HostScopedProxy.copy() operates on ALL hosts in a group,
not just the first one. Always returns a list of result dicts.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ftl2.automation.proxy import HostScopedProxy


def _make_host_config(name, become=False):
    """Create a mock host config."""
    hc = MagicMock()
    hc.name = name
    hc.ansible_host = name
    hc.ansible_port = 22
    bc = MagicMock()
    bc.effective = become
    bc.with_overrides = MagicMock(return_value=bc)
    hc.become_config = bc
    return hc


def _make_ssh_mock():
    """Create a mock SSH connection that tracks calls."""
    ssh = AsyncMock()
    ssh.read_file_or_none = AsyncMock(return_value=None)
    ssh.write_file = AsyncMock()
    ssh.run = AsyncMock(return_value=("", "", 0))
    ssh.stat = AsyncMock(return_value=None)
    ssh.chmod = AsyncMock()
    ssh.chown = AsyncMock()
    ssh.rename = AsyncMock()
    return ssh


def _make_context_with_hosts(host_configs):
    """Create a mock context returning the given host configs."""
    ctx = MagicMock()
    ctx.hosts = MagicMock()
    ctx.hosts.__getitem__ = MagicMock(return_value=host_configs)
    ctx._get_ssh_connection = AsyncMock()
    ctx.verbose = False
    ctx.quiet = True
    return ctx


class TestCopyMultiHost:
    """Tests for copy() fan-out to multiple hosts."""

    @pytest.mark.asyncio
    async def test_copy_fans_out_to_all_hosts(self):
        """copy() should execute on every host in the group, not just the first."""
        hosts = [_make_host_config("web1"), _make_host_config("web2"), _make_host_config("web3")]
        ssh_mocks = {h.name: _make_ssh_mock() for h in hosts}
        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=lambda h: ssh_mocks[h.name])

        proxy = HostScopedProxy(ctx, "webservers")
        results = await proxy.copy(content="hello", dest="/tmp/test.txt")

        assert isinstance(results, list)
        assert len(results) == 3

        # Each SSH mock should have had write_file called
        for name, ssh in ssh_mocks.items():
            ssh.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_copy_single_host_returns_list(self):
        """copy() with a single remote host still returns a list."""
        hosts = [_make_host_config("web1")]
        ssh = _make_ssh_mock()
        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(return_value=ssh)

        proxy = HostScopedProxy(ctx, "web1")
        results = await proxy.copy(content="hello", dest="/tmp/test.txt")

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["changed"] is True
        assert results[0]["host"] == "web1"

    @pytest.mark.asyncio
    async def test_copy_multi_host_results_have_host_key(self):
        """Each result dict should contain a 'host' key identifying the target."""
        hosts = [_make_host_config("db1"), _make_host_config("db2")]
        ssh_mocks = {h.name: _make_ssh_mock() for h in hosts}
        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=lambda h: ssh_mocks[h.name])

        proxy = HostScopedProxy(ctx, "databases")
        results = await proxy.copy(content="config", dest="/etc/app.conf")

        host_names = {r["host"] for r in results}
        assert host_names == {"db1", "db2"}

    @pytest.mark.asyncio
    async def test_copy_multi_host_idempotent(self):
        """copy() returns changed=False for hosts where content already matches."""
        hosts = [_make_host_config("app1"), _make_host_config("app2")]
        ssh1 = _make_ssh_mock()
        ssh1.read_file_or_none = AsyncMock(return_value=b"hello")  # already has content
        ssh2 = _make_ssh_mock()
        ssh2.read_file_or_none = AsyncMock(return_value=None)  # needs content

        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=lambda h: ssh1 if h.name == "app1" else ssh2)

        proxy = HostScopedProxy(ctx, "apps")
        results = await proxy.copy(content="hello", dest="/tmp/test.txt")

        assert isinstance(results, list)
        r_by_host = {r["host"]: r for r in results}
        assert r_by_host["app1"]["changed"] is False
        assert r_by_host["app2"]["changed"] is True

    @pytest.mark.asyncio
    async def test_copy_multi_host_concurrent_execution(self):
        """copy() runs on all hosts concurrently via asyncio.gather."""
        execution_order = []

        async def slow_write(path, content):
            execution_order.append(("start", path))
            await asyncio.sleep(0.01)
            execution_order.append(("end", path))

        hosts = [_make_host_config("h1"), _make_host_config("h2")]
        ssh_mocks = {}
        for h in hosts:
            ssh = _make_ssh_mock()
            ssh.write_file = AsyncMock(side_effect=slow_write)
            ssh_mocks[h.name] = ssh

        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=lambda h: ssh_mocks[h.name])

        proxy = HostScopedProxy(ctx, "group")
        results = await proxy.copy(content="data", dest="/tmp/f.txt")

        assert len(results) == 2
        # Both should have started before either finished (concurrent)
        starts = [i for i, (action, _) in enumerate(execution_order) if action == "start"]
        ends = [i for i, (action, _) in enumerate(execution_order) if action == "end"]
        assert len(starts) == 2
        assert len(ends) == 2
        # At least one "start" should precede both "end"s (concurrent execution)
        assert starts[1] < ends[0]

    @pytest.mark.asyncio
    async def test_copy_no_hosts_raises_error(self):
        """copy() raises ValueError when no hosts found for target."""
        ctx = MagicMock()
        ctx.hosts = MagicMock()
        ctx.hosts.__getitem__ = MagicMock(return_value=[])
        ctx.verbose = False
        ctx.quiet = True

        proxy = HostScopedProxy(ctx, "nonexistent_group")
        with pytest.raises(ValueError, match="No hosts found"):
            await proxy.copy(content="hello", dest="/tmp/test.txt")

    @pytest.mark.asyncio
    async def test_copy_multi_host_with_src_file(self):
        """copy() with src file fans out to all hosts."""
        import tempfile
        from pathlib import Path

        hosts = [_make_host_config("s1"), _make_host_config("s2")]
        ssh_mocks = {h.name: _make_ssh_mock() for h in hosts}
        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=lambda h: ssh_mocks[h.name])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("file content")
            src_path = f.name

        try:
            proxy = HostScopedProxy(ctx, "servers")
            results = await proxy.copy(src=src_path, dest="/opt/app/config.txt")

            assert isinstance(results, list)
            assert len(results) == 2
            for ssh in ssh_mocks.values():
                ssh.write_file.assert_called_once()
        finally:
            import os
            os.unlink(src_path)

    @pytest.mark.asyncio
    async def test_copy_partial_failure(self):
        """If one host fails, other hosts still succeed with failed host marked."""
        hosts = [_make_host_config("ok1"), _make_host_config("fail1"), _make_host_config("ok2")]

        ssh_ok = _make_ssh_mock()
        ssh_fail = AsyncMock()

        async def raise_on_connect(h):
            if h.name == "fail1":
                raise ConnectionError("SSH timeout")
            return ssh_ok

        ctx = _make_context_with_hosts(hosts)
        ctx._get_ssh_connection = AsyncMock(side_effect=raise_on_connect)

        proxy = HostScopedProxy(ctx, "mixed")
        results = await proxy.copy(content="data", dest="/tmp/test.txt")

        assert isinstance(results, list)
        assert len(results) == 3

        r_by_host = {r["host"]: r for r in results}

        # Successful hosts
        assert r_by_host["ok1"]["changed"] is True
        assert "failed" not in r_by_host["ok1"]
        assert r_by_host["ok2"]["changed"] is True
        assert "failed" not in r_by_host["ok2"]

        # Failed host
        assert r_by_host["fail1"]["failed"] is True
        assert r_by_host["fail1"]["changed"] is False
        assert "SSH timeout" in r_by_host["fail1"]["error"]
