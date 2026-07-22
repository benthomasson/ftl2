"""Tests for AutomationContext remote error handling (GH-75).

Validates that _execute_remote_via_gate and _execute_multiplexed return
ExecuteResult with success=False instead of raising exceptions, matching
the errors-as-data contract.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.automation.context import AutomationContext
from ftl2.exceptions import FTL2ConnectionError
from ftl2.ftl_modules.executor import ExecuteResult
from ftl2.message import GateProtocol, ProtocolError
from ftl2.types import HostConfig


def _make_context_with_mocks():
    """Build an AutomationContext with a mocked remote runner."""
    with patch.object(AutomationContext, '_check_name_collisions'):
        ctx = AutomationContext()

    # Create mocked remote runner
    runner = MagicMock()
    runner.gate_cache = {}
    runner.protocol = GateProtocol()
    ctx._remote_runner = runner
    ctx._gate_locks = {}

    return ctx


def _make_host(name="web01"):
    return HostConfig(name=name, ansible_host="192.168.1.10")


class TestSerialPathErrorHandling:
    """_execute_remote_via_gate returns ExecuteResult on failure."""

    @pytest.mark.asyncio
    async def test_gate_creation_failure_returns_result(self):
        """SSH connection failure returns ExecuteResult, not exception."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        with patch.object(
            ctx, '_get_or_create_gate',
            side_effect=FTL2ConnectionError("SSH connection refused"),
        ):
            result = await ctx._execute_remote_via_gate(host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "SSH connection refused" in result.error
        assert result.module == "ping"
        assert result.host == "web01"

    @pytest.mark.asyncio
    async def test_broken_pipe_returns_result(self):
        """BrokenPipeError during send returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        # Mock gate creation to succeed
        gate = MagicMock()
        gate.multiplexed = False
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate.gate_process.stdout = MagicMock()

        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch.object(
            ctx._remote_runner.protocol, 'send_message',
            side_effect=BrokenPipeError("Connection lost"),
        ):
            result = await ctx._execute_remote_via_gate(host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Connection lost" in result.error

    @pytest.mark.asyncio
    async def test_protocol_error_returns_result(self):
        """ProtocolError during read returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = False
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate.gate_process.stdout = MagicMock()

        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch.object(
            ctx._remote_runner.protocol, 'send_message',
            new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message',
            side_effect=ProtocolError("Invalid hex length"),
        ):
            result = await ctx._execute_remote_via_gate(host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Invalid hex length" in result.error

    @pytest.mark.asyncio
    async def test_ftl_module_error_response_returns_result(self):
        """Gate Error response for FTL module returns ExecuteResult, not exception."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = False
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate.gate_process.stdout = MagicMock()

        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch.object(
            ctx._remote_runner.protocol, 'send_message',
            new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message',
            new_callable=AsyncMock,
            return_value=("Error", {"message": "Module crashed"}),
        ), patch(
            'ftl2.ftl_modules.executor.is_ftl_module',
            return_value=True,
        ):
            result = await ctx._execute_remote_via_gate(
                host, "system_info", {},
            )

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Module crashed" in result.error

    @pytest.mark.asyncio
    async def test_unexpected_response_returns_result(self):
        """Unexpected gate response for FTL module returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = False
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate.gate_process.stdout = MagicMock()

        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch.object(
            ctx._remote_runner.protocol, 'send_message',
            new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message',
            new_callable=AsyncMock,
            return_value=("Bogus", {}),
        ), patch(
            'ftl2.ftl_modules.executor.is_ftl_module',
            return_value=True,
        ):
            result = await ctx._execute_remote_via_gate(
                host, "system_info", {},
            )

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Unexpected response" in result.error

    @pytest.mark.asyncio
    async def test_runtime_error_still_raised(self):
        """RuntimeError for uninitialized runner still raises (programming error)."""
        with patch.object(AutomationContext, '_check_name_collisions'):
            ctx = AutomationContext()
        ctx._remote_runner = None

        with pytest.raises(RuntimeError, match="not initialized"):
            await ctx._execute_remote_via_gate(_make_host(), "ping", {})

    @pytest.mark.asyncio
    async def test_output_dict_has_failed_key(self):
        """ExecuteResult.output includes failed=True for structured error inspection."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        with patch.object(
            ctx, '_get_or_create_gate',
            side_effect=ConnectionError("timeout"),
        ):
            result = await ctx._execute_remote_via_gate(host, "ping", {})

        assert result.output.get("failed") is True
        assert "msg" in result.output


class TestMultiplexedPathErrorHandling:
    """_execute_multiplexed returns ExecuteResult on failure."""

    @pytest.mark.asyncio
    async def test_protocol_error_returns_result(self):
        """Protocol error in multiplexed path returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = True
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate._write_lock = asyncio.Lock()
        gate.next_msg_id.return_value = 1

        # Make create_future return a future that raises
        future = asyncio.get_running_loop().create_future()
        future.set_exception(ProtocolError("Connection dropped"))
        gate.create_future.return_value = future

        with patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id',
            new_callable=AsyncMock,
        ):
            result = await ctx._execute_multiplexed(gate, host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Connection dropped" in result.error

    @pytest.mark.asyncio
    async def test_ftl_error_response_returns_result(self):
        """Error response in multiplexed FTL path returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = True
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate._write_lock = asyncio.Lock()
        gate.next_msg_id.return_value = 1

        future = asyncio.get_running_loop().create_future()
        future.set_result(("Error", {"message": "Import failed"}))
        gate.create_future.return_value = future

        with patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id',
            new_callable=AsyncMock,
        ), patch(
            'ftl2.ftl_modules.executor.is_ftl_module',
            return_value=True,
        ):
            result = await ctx._execute_multiplexed(
                gate, host, "system_info", {},
            )

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Import failed" in result.error

    @pytest.mark.asyncio
    async def test_broken_pipe_returns_result(self):
        """BrokenPipeError in multiplexed path returns ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = MagicMock()
        gate.multiplexed = True
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate._write_lock = asyncio.Lock()
        gate.next_msg_id.return_value = 1
        gate.create_future.return_value = asyncio.get_running_loop().create_future()

        with patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id',
            side_effect=BrokenPipeError("Pipe broken"),
        ):
            result = await ctx._execute_multiplexed(gate, host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert "Pipe broken" in result.error


class TestGateExitCodePropagation:
    """Gate subprocess exit codes are carried into ExecuteResult."""

    def _serial_gate(self):
        gate = MagicMock()
        gate.multiplexed = False
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate.gate_process.stdout = MagicMock()
        return gate

    def _mux_gate(self, resp):
        gate = MagicMock()
        gate.multiplexed = True
        gate.gate_process = MagicMock()
        gate.gate_process.stdin = MagicMock()
        gate._write_lock = asyncio.Lock()
        gate.next_msg_id.return_value = 1

        f = asyncio.get_running_loop().create_future()
        f.set_result(resp)
        gate.create_future.return_value = f
        return gate

    @pytest.mark.asyncio
    async def test_serial_nonzero_rc_reports_failure(self):
        """Non-zero gate rc surfaces as failed ExecuteResult."""
        ctx = _make_context_with_mocks()
        host = _make_host()
        gate = self._serial_gate()

        module_json = '{"changed": false}'
        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message', new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message', new_callable=AsyncMock,
            return_value=("ModuleResult", {"stdout": module_json, "stderr": "", "rc": 1}),
        ):
            result = await ctx._execute_remote_via_gate(host, "command", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert result.output["rc"] == 1

    @pytest.mark.asyncio
    async def test_serial_zero_rc_reports_success(self):
        """Zero gate rc with valid JSON reports success."""
        ctx = _make_context_with_mocks()
        host = _make_host()
        gate = self._serial_gate()

        module_json = '{"changed": true}'
        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message', new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message', new_callable=AsyncMock,
            return_value=("ModuleResult", {"stdout": module_json, "stderr": "", "rc": 0}),
        ):
            result = await ctx._execute_remote_via_gate(host, "file", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is True
        assert result.output["rc"] == 0

    @pytest.mark.asyncio
    async def test_serial_module_rc_preserved_over_gate_rc(self):
        """Module JSON with its own rc takes precedence over gate rc."""
        ctx = _make_context_with_mocks()
        host = _make_host()
        gate = self._serial_gate()

        module_json = '{"changed": false, "rc": 2}'
        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message', new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message', new_callable=AsyncMock,
            return_value=("ModuleResult", {"stdout": module_json, "stderr": "", "rc": 0}),
        ):
            result = await ctx._execute_remote_via_gate(host, "command", {})

        assert result.success is False
        assert result.output["rc"] == 2

    @pytest.mark.asyncio
    async def test_serial_empty_stdout_still_fails(self):
        """Empty stdout with non-zero rc still reports failure."""
        ctx = _make_context_with_mocks()
        host = _make_host()
        gate = self._serial_gate()

        with patch.object(ctx, '_get_or_create_gate', return_value=gate), patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message', new_callable=AsyncMock,
        ), patch.object(
            ctx._remote_runner.protocol, 'read_message', new_callable=AsyncMock,
            return_value=("ModuleResult", {"stdout": "", "stderr": "permission denied", "rc": 1}),
        ):
            result = await ctx._execute_remote_via_gate(host, "command", {})

        assert result.success is False
        assert result.output["rc"] == 1

    @pytest.mark.asyncio
    async def test_multiplexed_nonzero_rc_reports_failure(self):
        """Non-zero gate rc in multiplexed path surfaces as failure."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        module_json = '{"changed": false}'
        gate = self._mux_gate(("ModuleResult", {"stdout": module_json, "stderr": "", "rc": 1}))

        with patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id', new_callable=AsyncMock,
        ):
            result = await ctx._execute_multiplexed(gate, host, "command", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert result.output["rc"] == 1

    @pytest.mark.asyncio
    async def test_multiplexed_zero_rc_reports_success(self):
        """Zero gate rc in multiplexed path reports success."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        module_json = '{"changed": true}'
        gate = self._mux_gate(("ModuleResult", {"stdout": module_json, "stderr": "", "rc": 0}))

        with patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id', new_callable=AsyncMock,
        ):
            result = await ctx._execute_multiplexed(gate, host, "file", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is True
        assert result.output["rc"] == 0

    @pytest.mark.asyncio
    async def test_multiplexed_empty_stdout_still_fails(self):
        """Empty stdout in multiplexed path with non-zero rc still reports failure."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        gate = self._mux_gate(("ModuleResult", {"stdout": "", "stderr": "error", "rc": 1}))

        with patch(
            'ftl2.ftl_modules.executor.is_ftl_module', return_value=False,
        ), patch.object(
            ctx._remote_runner.protocol, 'send_message_with_id', new_callable=AsyncMock,
        ):
            result = await ctx._execute_multiplexed(gate, host, "command", {})

        assert result.success is False
        assert result.output["rc"] == 1


class TestErrorDataContract:
    """Both paths honour the errors-as-data contract for all exception types."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc_class,exc_msg", [
        (ConnectionError, "Connection refused"),
        (OSError, "Network unreachable"),
        (TimeoutError, "Operation timed out"),
        (ProtocolError, "Invalid message format"),
        (BrokenPipeError, "Broken pipe"),
        (RuntimeError, "Gate process exited unexpectedly"),
    ])
    async def test_serial_path_catches_all(self, exc_class, exc_msg):
        """Various exception types all produce ExecuteResult, never propagate."""
        ctx = _make_context_with_mocks()
        host = _make_host()

        with patch.object(
            ctx, '_get_or_create_gate',
            side_effect=exc_class(exc_msg),
        ):
            result = await ctx._execute_remote_via_gate(host, "ping", {})

        assert isinstance(result, ExecuteResult)
        assert result.success is False
        assert exc_msg in result.error
        assert result.host == "web01"
        assert result.module == "ping"
