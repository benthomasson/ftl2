"""Tests for gate lifecycle management commands (issue #68).

Tests cover:
- GateDrain protocol message handling (serial and multiplexed modes)
- Drain rejection of new work
- _decommission_gate_subsystem SSH operations
- gate_upgrade rolling strategy stops on failure
- gate_restart force=True skips drain
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.message import GateProtocol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MemoryWriter:
    """Async writer that captures bytes in a buffer."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass


def make_reader_from_messages(messages: list) -> asyncio.StreamReader:
    """Build a StreamReader pre-loaded with length-prefixed JSON messages."""
    buf = bytearray()
    for msg in messages:
        json_bytes = json.dumps(msg).encode("utf-8")
        length_prefix = f"{len(json_bytes):08x}".encode("ascii")
        buf.extend(length_prefix)
        buf.extend(json_bytes)
    reader = asyncio.StreamReader()
    reader.feed_data(bytes(buf))
    reader.feed_eof()
    return reader


def parse_responses(writer: MemoryWriter) -> list:
    """Parse all length-prefixed JSON messages from a MemoryWriter buffer."""
    data = bytes(writer.buffer)
    results = []
    offset = 0
    while offset < len(data):
        length_hex = data[offset:offset + 8].decode("ascii")
        length = int(length_hex, 16)
        offset += 8
        json_bytes = data[offset:offset + length]
        offset += length
        results.append(json.loads(json_bytes))
    return results


# ---------------------------------------------------------------------------
# Protocol message type tests
# ---------------------------------------------------------------------------

class TestGateDrainProtocol:
    """Tests for GateDrain and GateDrainResult message types."""

    def test_message_types_include_drain(self):
        """GateDrain and GateDrainResult are registered message types."""
        protocol = GateProtocol()
        assert "GateDrain" in protocol.MESSAGE_TYPES
        assert "GateDrainResult" in protocol.MESSAGE_TYPES
        assert "Goodbye" in protocol.MESSAGE_TYPES

    @pytest.mark.asyncio
    async def test_send_gate_drain_message(self):
        """GateDrain can be sent with timeout_seconds."""
        protocol = GateProtocol()
        writer = MemoryWriter()
        await protocol.send_message(writer, "GateDrain", {"timeout_seconds": 60})

        responses = parse_responses(writer)
        assert len(responses) == 1
        assert responses[0][0] == "GateDrain"
        assert responses[0][1]["timeout_seconds"] == 60

    @pytest.mark.asyncio
    async def test_send_gate_drain_result_message(self):
        """GateDrainResult can be sent with status fields."""
        protocol = GateProtocol()
        writer = MemoryWriter()
        await protocol.send_message(writer, "GateDrainResult", {
            "status": "drained",
            "completed": 3,
            "in_flight": 0,
        })

        responses = parse_responses(writer)
        assert len(responses) == 1
        assert responses[0][0] == "GateDrainResult"
        assert responses[0][1]["status"] == "drained"
        assert responses[0][1]["completed"] == 3
        assert responses[0][1]["in_flight"] == 0

    @pytest.mark.asyncio
    async def test_send_gate_drain_with_id(self):
        """GateDrain works as a multiplexed 3-tuple message."""
        protocol = GateProtocol()
        writer = MemoryWriter()
        await protocol.send_message_with_id(
            writer, "GateDrain", {"timeout_seconds": 120}, msg_id=42,
        )

        responses = parse_responses(writer)
        assert len(responses) == 1
        assert responses[0] == ["GateDrain", {"timeout_seconds": 120}, 42]


# ---------------------------------------------------------------------------
# Gate-side drain handler tests (multiplexed mode simulation)
# ---------------------------------------------------------------------------

class TestMultiplexedDrain:
    """Tests for the multiplexed main loop drain behavior.

    These test the logic by importing and running main_multiplexed
    with crafted input streams.
    """

    @pytest.mark.asyncio
    async def test_drain_no_inflight_tasks(self):
        """GateDrain with no in-flight tasks returns immediate drained status."""
        from ftl2.ftl_gate.__main__ import main_multiplexed

        reader = make_reader_from_messages([
            ["GateDrain", {"timeout_seconds": 10}, 1],
            ["Shutdown", {}, 2],
        ])
        writer = MemoryWriter()
        protocol = GateProtocol()

        watcher = MagicMock()
        watcher.stop = MagicMock()
        monitor = MagicMock()
        monitor.stop = MagicMock()

        await main_multiplexed(reader, writer, protocol, watcher, monitor, "testhash")

        responses = parse_responses(writer)
        # Should have GateDrainResult and Goodbye
        drain_resp = [r for r in responses if r[0] == "GateDrainResult"]
        assert len(drain_resp) == 1
        assert drain_resp[0][1]["status"] == "drained"
        assert drain_resp[0][1]["completed"] == 0
        assert drain_resp[0][1]["in_flight"] == 0
        assert drain_resp[0][2] == 1  # msg_id preserved

    @pytest.mark.asyncio
    async def test_drain_rejects_new_work(self):
        """After GateDrain, Module requests are rejected with Error."""
        from ftl2.ftl_gate.__main__ import main_multiplexed

        reader = make_reader_from_messages([
            ["GateDrain", {"timeout_seconds": 10}, 1],
            ["Module", {"module_name": "ping", "module_args": {}}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        protocol = GateProtocol()

        watcher = MagicMock()
        watcher.stop = MagicMock()
        monitor = MagicMock()
        monitor.stop = MagicMock()

        await main_multiplexed(reader, writer, protocol, watcher, monitor, "testhash")

        responses = parse_responses(writer)
        # Find the Error response for msg_id=2
        error_resp = [r for r in responses if r[0] == "Error" and len(r) == 3 and r[2] == 2]
        assert len(error_resp) == 1
        assert "draining" in error_resp[0][1]["message"].lower()

    @pytest.mark.asyncio
    async def test_drain_allows_info_after_drain(self):
        """After GateDrain, non-work messages like Info are still handled."""
        from ftl2.ftl_gate.__main__ import main_multiplexed

        reader = make_reader_from_messages([
            ["GateDrain", {"timeout_seconds": 10}, 1],
            ["Info", {}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        protocol = GateProtocol()

        watcher = MagicMock()
        watcher.stop = MagicMock()
        monitor = MagicMock()
        monitor.stop = MagicMock()

        await main_multiplexed(reader, writer, protocol, watcher, monitor, "testhash")

        responses = parse_responses(writer)
        # Info should get InfoResult (not Error)
        info_resp = [r for r in responses if r[0] == "InfoResult"]
        assert len(info_resp) == 1


# ---------------------------------------------------------------------------
# Client-side _drain_gate tests
# ---------------------------------------------------------------------------

class TestDrainGateClient:
    """Tests for RemoteModuleRunner._drain_gate."""

    @pytest.mark.asyncio
    async def test_drain_multiplexed_gate(self):
        """_drain_gate sends GateDrain and returns the result dict."""
        from ftl2.runners import Gate, RemoteModuleRunner

        protocol = GateProtocol()
        runner = RemoteModuleRunner()
        runner.protocol = protocol

        # Build a mock gate
        gate = Gate.__new__(Gate)
        gate._msg_counter = 0
        gate._pending = {}
        gate._write_lock = asyncio.Lock()
        gate.multiplexed = True

        # Mock gate_process.stdin as a writer
        writer = MemoryWriter()
        mock_process = MagicMock()
        mock_process.stdin = writer
        gate.gate_process = mock_process

        # Pre-populate the future with a result (simulating the reader loop)
        async def drain_with_response():
            # Start the drain in a task
            task = asyncio.create_task(runner._drain_gate(gate, timeout_seconds=60))
            # Give it a moment to send the message and create the future
            await asyncio.sleep(0.01)
            # Resolve the pending future
            for msg_id, future in gate._pending.items():
                if not future.done():
                    future.set_result(("GateDrainResult", {
                        "status": "drained", "completed": 2, "in_flight": 0,
                    }))
            return await task

        result = await drain_with_response()
        assert result["status"] == "drained"
        assert result["completed"] == 2

    @pytest.mark.asyncio
    async def test_drain_serial_gate(self):
        """_drain_gate in serial mode sends message and reads response."""
        from ftl2.runners import RemoteModuleRunner

        protocol = GateProtocol()
        runner = RemoteModuleRunner()
        runner.protocol = protocol

        from ftl2.runners import Gate
        gate = Gate.__new__(Gate)
        gate.multiplexed = False

        writer = MemoryWriter()
        mock_process = MagicMock()
        mock_process.stdin = writer

        # Pre-build stdout with a GateDrainResult response
        mock_process.stdout = make_reader_from_messages([
            ["GateDrainResult", {"status": "drained", "completed": 0, "in_flight": 0}],
        ])
        gate.gate_process = mock_process

        result = await runner._drain_gate(gate, timeout_seconds=30)
        assert result["status"] == "drained"
        assert result["in_flight"] == 0


# ---------------------------------------------------------------------------
# _decommission_gate_subsystem tests
# ---------------------------------------------------------------------------

class TestDecommissionGateSubsystem:
    """Tests for RemoteModuleRunner._decommission_gate_subsystem."""

    @pytest.mark.asyncio
    async def test_decommission_not_root(self):
        """Returns error when not running as root."""
        from ftl2.runners import RemoteModuleRunner

        runner = RemoteModuleRunner()

        conn = AsyncMock()
        id_result = MagicMock()
        id_result.stdout = "1000"
        conn.run = AsyncMock(return_value=id_result)

        result = await runner._decommission_gate_subsystem(conn, cleanup=True)
        assert result["status"] == "error"
        assert "root" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_decommission_already_removed(self):
        """Returns ok when subsystem not registered."""
        from ftl2.runners import RemoteModuleRunner

        runner = RemoteModuleRunner()

        call_count = 0

        async def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if "id -u" in cmd:
                result.stdout = "0"
                return result
            elif "grep -q" in cmd:
                result.exit_status = 1
                return result
            return result

        conn = AsyncMock()
        conn.run = mock_run

        result = await runner._decommission_gate_subsystem(conn, cleanup=True)
        assert result["status"] == "ok"
        assert "already decommissioned" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_decommission_success(self):
        """Successfully removes subsystem, reloads sshd, deletes binary."""
        from ftl2.runners import RemoteModuleRunner

        runner = RemoteModuleRunner()
        commands_run = []

        async def mock_run(cmd, **kwargs):
            commands_run.append(cmd)
            result = MagicMock()
            if "id -u" in cmd:
                result.stdout = "0"
            elif "grep -q" in cmd:
                result.exit_status = 0
            return result

        conn = AsyncMock()
        conn.run = mock_run

        result = await runner._decommission_gate_subsystem(conn, cleanup=True)
        assert result["status"] == "ok"

        # Verify the right commands were run
        sed_cmds = [c for c in commands_run if "sed" in c]
        assert len(sed_cmds) == 1
        reload_cmds = [c for c in commands_run if "systemctl reload" in c]
        assert len(reload_cmds) == 1
        rm_cmds = [c for c in commands_run if "rm -f" in c]
        assert len(rm_cmds) == 1


# ---------------------------------------------------------------------------
# AutomationContext lifecycle method tests
# ---------------------------------------------------------------------------

class TestGateUpgradeRolling:
    """Tests for gate_upgrade rolling strategy."""

    @pytest.mark.asyncio
    async def test_rolling_stops_on_failure(self):
        """Rolling upgrade stops at the first failed host."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()

        host1 = HostConfig(name="web01", ansible_host="1.1.1.1")
        host2 = HostConfig(name="web02", ansible_host="2.2.2.2")
        host3 = HostConfig(name="web03", ansible_host="3.3.3.3")

        group = MagicMock()
        group.list_hosts.return_value = [host1, host2, host3]
        ctx._inventory.get_group.return_value = group

        runner = MagicMock()
        runner.gate_cache = {"web01": MagicMock(), "web02": MagicMock()}

        # web01 drains ok, web02 drain raises
        drain_call_count = 0

        async def mock_drain(gate, timeout=300):
            nonlocal drain_call_count
            drain_call_count += 1
            if drain_call_count == 2:
                raise RuntimeError("Connection lost")
            return {"status": "drained"}

        runner._drain_gate = mock_drain
        runner._close_gate = AsyncMock()
        ctx._remote_runner = runner

        # Mock _get_or_create_gate to succeed for web01
        create_count = 0
        async def mock_create_gate(host, **kwargs):
            nonlocal create_count
            create_count += 1
            return MagicMock()

        ctx._get_or_create_gate = mock_create_gate

        results = await ctx.gate_upgrade("webservers", strategy="rolling", drain_timeout=10)

        # Should have 2 results: web01 ok, web02 error (web03 not attempted)
        assert len(results) == 2
        assert results[0]["status"] == "ok"
        assert results[0]["host"] == "web01"
        assert results[1]["status"] == "error"
        assert results[1]["host"] == "web02"


class TestGateRestartForce:
    """Tests for gate_restart force=True."""

    @pytest.mark.asyncio
    async def test_force_restart_skips_drain(self):
        """force=True skips the drain step."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()

        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        runner = MagicMock()
        runner.gate_cache = {"web01": MagicMock()}
        runner._drain_gate = AsyncMock()
        runner._close_gate = AsyncMock()
        ctx._remote_runner = runner

        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_restart("web01", force=True)

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        # _drain_gate should NOT have been called
        runner._drain_gate.assert_not_called()
        # _close_gate should have been called
        runner._close_gate.assert_called_once()

    @pytest.mark.asyncio
    async def test_graceful_restart_drains_first(self):
        """Without force, drain is called before shutdown."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()

        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        runner = MagicMock()
        runner.gate_cache = {"web01": MagicMock()}
        runner._drain_gate = AsyncMock(return_value={"status": "drained"})
        runner._close_gate = AsyncMock()
        ctx._remote_runner = runner

        ctx._get_or_create_gate = AsyncMock(return_value=MagicMock())

        results = await ctx.gate_restart("web01")

        assert results[0]["status"] == "ok"
        runner._drain_gate.assert_called_once()
        runner._close_gate.assert_called_once()


class TestResolveHosts:
    """Tests for _resolve_hosts helper."""

    def test_resolve_group(self):
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()
        ctx._remote_runner = MagicMock()

        host1 = HostConfig(name="web01", ansible_host="1.1.1.1")
        host2 = HostConfig(name="web02", ansible_host="2.2.2.2")

        group = MagicMock()
        group.list_hosts.return_value = [host1, host2]
        ctx._inventory.get_group.return_value = group

        result = ctx._resolve_hosts("webservers")
        assert len(result) == 2
        assert result[0].name == "web01"

    def test_resolve_single_host(self):
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()
        ctx._remote_runner = MagicMock()
        ctx._inventory.get_group.return_value = None

        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        result = ctx._resolve_hosts("web01")
        assert len(result) == 1
        assert result[0].name == "web01"

    def test_resolve_unknown_raises(self):
        from ftl2.automation.context import AutomationContext

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()
        ctx._remote_runner = MagicMock()
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {}

        with pytest.raises(ValueError, match="Unknown host or group"):
            ctx._resolve_hosts("nonexistent")

    def test_resolve_raises_without_context_manager(self):
        """_resolve_hosts raises RuntimeError when _remote_runner is None."""
        from ftl2.automation.context import AutomationContext

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()
        ctx._remote_runner = None

        with pytest.raises(RuntimeError, match="active context manager"):
            ctx._resolve_hosts("web01")


class TestGateDeploySubsystem:
    """Tests for gate_deploy forcing subsystem registration."""

    @pytest.mark.asyncio
    async def test_gate_deploy_passes_register_subsystem_true(self):
        """gate_deploy must pass register_subsystem=True to _get_or_create_gate."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()
        ctx._inventory.get_group.return_value = None

        host = HostConfig(name="web01", ansible_host="1.1.1.1")
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        runner = MagicMock()
        ctx._remote_runner = runner

        create_calls = []

        async def mock_create(host, register_subsystem=None, **kwargs):
            create_calls.append({"host": host.name, "register_subsystem": register_subsystem})
            return MagicMock()

        ctx._get_or_create_gate = mock_create

        results = await ctx.gate_deploy("web01")

        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert len(create_calls) == 1
        assert create_calls[0]["register_subsystem"] is True


class TestBecomeConfigCacheKey:
    """Tests for lifecycle methods using correct become-aware cache keys."""

    @pytest.mark.asyncio
    async def test_drain_finds_gate_with_become(self):
        """gate_drain uses become_config in the cache key to find the gate."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig, gate_cache_key

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()

        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="root",
        )
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        # The gate is cached with the become-aware key
        become_key = gate_cache_key(host.name, host.become_config)
        assert become_key == "web01:become=root:method=sudo"

        mock_gate = MagicMock()
        runner = MagicMock()
        runner.gate_cache = {become_key: mock_gate}
        runner._drain_gate = AsyncMock(return_value={"status": "drained", "completed": 0, "in_flight": 0})
        ctx._remote_runner = runner

        results = await ctx.gate_drain("web01", timeout_seconds=10)

        assert len(results) == 1
        assert results[0]["status"] == "drained"
        runner._drain_gate.assert_called_once_with(mock_gate, 10)

    @pytest.mark.asyncio
    async def test_drain_misses_without_become_key(self):
        """gate_drain reports error if gate is only cached under bare host name
        but host has become enabled (proves the cache key matters)."""
        from ftl2.automation.context import AutomationContext
        from ftl2.types import HostConfig

        ctx = AutomationContext.__new__(AutomationContext)
        ctx._inventory = MagicMock()

        host = HostConfig(
            name="web01", ansible_host="1.1.1.1",
            ansible_become=True, ansible_become_user="root",
        )
        ctx._inventory.get_group.return_value = None
        ctx._inventory.get_all_hosts.return_value = {"web01": host}

        runner = MagicMock()
        # Gate cached under bare key — wrong for a become host
        runner.gate_cache = {"web01": MagicMock()}
        ctx._remote_runner = runner

        results = await ctx.gate_drain("web01")

        assert len(results) == 1
        assert results[0]["status"] == "error"
        assert "No active gate connection" in results[0]["message"]
