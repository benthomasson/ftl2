"""Tests for gate timeout and keepalive mechanisms.

Tests _send_and_wait timeout, handshake timeout, keepalive loop,
and unhealthy gate eviction without requiring real SSH connections.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.exceptions import (
    FTL2ConnectionError,
    GateHandshakeTimeoutError,
    GateRequestTimeoutError,
    GateUnresponsiveError,
    ModuleExecutionError,
)
from ftl2.runners import Gate, RemoteModuleRunner


def _make_gate(multiplexed: bool = True) -> Gate:
    """Create a Gate with mocked connection and process."""
    conn = MagicMock()
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdout = MagicMock()
    process.stderr = MagicMock()
    return Gate(
        conn=conn,
        gate_process=process,
        temp_dir="/tmp/test",
        interpreter="python3",
        multiplexed=multiplexed,
    )


class TestSendAndWaitTimeout:
    """Tests for _send_and_wait request-level timeout."""

    @pytest.mark.asyncio
    async def test_timeout_raises_gate_request_timeout_error(self):
        """_send_and_wait raises GateRequestTimeoutError when future doesn't resolve."""
        runner = RemoteModuleRunner()
        gate = _make_gate()

        # Mock send to succeed but never resolve the future
        runner.protocol.send_message_with_id = AsyncMock()

        with pytest.raises(GateRequestTimeoutError, match="timed out after 0.05s"):
            await runner._send_and_wait(gate, "Module", {"test": 1}, timeout=0.05)

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_pending_future(self):
        """Future is removed from gate._pending on timeout."""
        runner = RemoteModuleRunner()
        gate = _make_gate()
        runner.protocol.send_message_with_id = AsyncMock()

        with pytest.raises(GateRequestTimeoutError):
            await runner._send_and_wait(gate, "Module", {}, timeout=0.05)

        # The pending dict should be empty — future was cleaned up
        assert len(gate._pending) == 0

    @pytest.mark.asyncio
    async def test_successful_response_within_timeout(self):
        """_send_and_wait returns normally when future resolves in time."""
        runner = RemoteModuleRunner()
        gate = _make_gate()

        async def fake_send(stdin, msg_type, data, msg_id, write_lock=None):
            # Simulate gate responding immediately
            future = gate._pending.get(msg_id)
            if future and not future.done():
                future.set_result(("ModuleResult", {"changed": False}))

        runner.protocol.send_message_with_id = fake_send

        result = await runner._send_and_wait(gate, "Module", {}, timeout=5.0)
        assert result == ("ModuleResult", {"changed": False})

    @pytest.mark.asyncio
    async def test_default_timeout_is_request_timeout(self):
        """Default timeout uses REQUEST_TIMEOUT class constant."""
        runner = RemoteModuleRunner()
        assert runner.REQUEST_TIMEOUT == 300.0

        gate = _make_gate()
        runner.protocol.send_message_with_id = AsyncMock()

        # Override to a short timeout to verify the default path works
        runner.REQUEST_TIMEOUT = 0.05
        with pytest.raises(GateRequestTimeoutError):
            await runner._send_and_wait(gate, "Module", {})

    @pytest.mark.asyncio
    async def test_timeout_error_includes_msg_type_and_id(self):
        """Error message includes diagnostic context (msg_type, msg_id)."""
        runner = RemoteModuleRunner()
        gate = _make_gate()
        runner.protocol.send_message_with_id = AsyncMock()

        with pytest.raises(GateRequestTimeoutError, match="msg_type=Module") as exc_info:
            await runner._send_and_wait(gate, "Module", {}, timeout=0.05)
        assert "msg_id=" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_multiple_concurrent_timeouts_clean_up(self):
        """Multiple concurrent requests that time out all clean up their futures."""
        runner = RemoteModuleRunner()
        gate = _make_gate()
        runner.protocol.send_message_with_id = AsyncMock()

        async def send_and_timeout():
            with pytest.raises(GateRequestTimeoutError):
                await runner._send_and_wait(gate, "Module", {}, timeout=0.05)

        # Fire 5 concurrent requests that all time out
        await asyncio.gather(*[send_and_timeout() for _ in range(5)])

        # All futures should have been cleaned up
        assert len(gate._pending) == 0


class TestHandshakeTimeout:
    """Tests for _open_gate handshake timeout."""

    @pytest.mark.asyncio
    async def test_handshake_timeout_raises_error(self):
        """_open_gate raises GateHandshakeTimeoutError when gate doesn't respond."""
        runner = RemoteModuleRunner()
        runner.HANDSHAKE_TIMEOUT = 0.05

        conn = MagicMock()
        process = MagicMock()
        process.stdin = MagicMock()
        process.stderr = MagicMock()
        process.stderr.read = AsyncMock(return_value=b"")

        # send_message succeeds
        runner.protocol.send_message = AsyncMock()
        # read_message hangs forever (must be async to actually block)
        async def hang_forever(_):
            await asyncio.sleep(999)

        runner.protocol.read_message = AsyncMock(side_effect=hang_forever)

        # Mock create_process — must raise on subsystem so exec path is taken
        import asyncssh
        conn.create_process = AsyncMock(
            side_effect=[asyncssh.ChannelOpenError(1, "no subsystem"), process]
        )

        with pytest.raises(GateHandshakeTimeoutError, match="timed out after 0.05s"):
            await runner._open_gate(conn, "/tmp/gate.pyz", "python3")

    @pytest.mark.asyncio
    async def test_handshake_timeout_includes_stderr(self):
        """GateHandshakeTimeoutError includes stderr output if available."""
        runner = RemoteModuleRunner()
        runner.HANDSHAKE_TIMEOUT = 0.05

        conn = MagicMock()
        process = MagicMock()
        process.stdin = MagicMock()
        process.stderr = MagicMock()
        process.stderr.read = AsyncMock(return_value=b"ImportError: no module named foo")

        runner.protocol.send_message = AsyncMock()

        async def hang_forever(_):
            await asyncio.sleep(999)

        runner.protocol.read_message = AsyncMock(side_effect=hang_forever)

        import asyncssh
        conn.create_process = AsyncMock(
            side_effect=[asyncssh.ChannelOpenError(1, "no subsystem"), process]
        )

        with pytest.raises(
            GateHandshakeTimeoutError, match="ImportError: no module named foo"
        ):
            await runner._open_gate(conn, "/tmp/gate.pyz", "python3")

    @pytest.mark.asyncio
    async def test_handshake_timeout_stderr_collection_cannot_hang(self):
        """If stderr.read also hangs, the error is still raised (no double-hang)."""
        runner = RemoteModuleRunner()
        runner.HANDSHAKE_TIMEOUT = 0.05

        conn = MagicMock()
        process = MagicMock()
        process.stdin = MagicMock()
        process.stderr = MagicMock()

        async def stderr_hangs():
            await asyncio.sleep(999)

        process.stderr.read = AsyncMock(side_effect=stderr_hangs)

        runner.protocol.send_message = AsyncMock()

        async def hang_forever(_):
            await asyncio.sleep(999)

        runner.protocol.read_message = AsyncMock(side_effect=hang_forever)

        import asyncssh
        conn.create_process = AsyncMock(
            side_effect=[asyncssh.ChannelOpenError(1, "no subsystem"), process]
        )

        # Should still raise the timeout error, not hang on stderr collection
        with pytest.raises(GateHandshakeTimeoutError, match="timed out after 0.05s"):
            await runner._open_gate(conn, "/tmp/gate.pyz", "python3")


class TestKeepaliveLoop:
    """Tests for _keepalive_loop periodic health checking."""

    @pytest.mark.asyncio
    async def test_keepalive_failure_marks_gate_unhealthy(self):
        """Keepalive timeout sets gate.healthy=False and fails pending futures."""
        runner = RemoteModuleRunner()
        runner.KEEPALIVE_INTERVAL = 0.02
        runner.KEEPALIVE_TIMEOUT = 0.02

        gate = _make_gate()
        cache_key = "test:22:user"
        runner.gate_cache[cache_key] = gate

        # _send_and_wait will time out
        runner.protocol.send_message_with_id = AsyncMock()

        # Create a pending future to verify it gets failed
        pending_future = gate.create_future(gate.next_msg_id())

        # Run keepalive — it should detect timeout and break
        await runner._keepalive_loop(gate, cache_key)

        assert not gate.healthy
        assert cache_key not in runner.gate_cache
        assert pending_future.done()
        with pytest.raises(GateUnresponsiveError):
            pending_future.result()

    @pytest.mark.asyncio
    async def test_keepalive_cancellation(self):
        """Keepalive loop handles cancellation gracefully."""
        runner = RemoteModuleRunner()
        runner.KEEPALIVE_INTERVAL = 0.02
        runner.KEEPALIVE_TIMEOUT = 5.0

        gate = _make_gate()

        async def fake_send(stdin, msg_type, data, msg_id, write_lock=None):
            future = gate._pending.get(msg_id)
            if future and not future.done():
                future.set_result(("Hello", {"gate_hash": "abc"}))

        runner.protocol.send_message_with_id = fake_send

        task = asyncio.create_task(runner._keepalive_loop(gate, "test:22:user"))
        await asyncio.sleep(0.05)
        task.cancel()
        # Should not raise
        await task
        assert gate.healthy  # Gate is still healthy after clean cancel

    @pytest.mark.asyncio
    async def test_keepalive_stops_when_unhealthy(self):
        """Keepalive loop exits when gate is marked unhealthy externally."""
        runner = RemoteModuleRunner()
        runner.KEEPALIVE_INTERVAL = 0.02

        gate = _make_gate()
        gate.healthy = False

        # Should return immediately
        await asyncio.wait_for(
            runner._keepalive_loop(gate, "test:22:user"),
            timeout=1.0,
        )

    @pytest.mark.asyncio
    async def test_keepalive_clears_pending_on_failure(self):
        """When keepalive fails, _pending dict is cleared after failing futures."""
        runner = RemoteModuleRunner()
        runner.KEEPALIVE_INTERVAL = 0.02
        runner.KEEPALIVE_TIMEOUT = 0.02

        gate = _make_gate()
        cache_key = "test:22:user"
        runner.gate_cache[cache_key] = gate
        runner.protocol.send_message_with_id = AsyncMock()

        # Create multiple pending futures
        futures = [gate.create_future(gate.next_msg_id()) for _ in range(3)]

        await runner._keepalive_loop(gate, cache_key)

        # All futures failed with GateUnresponsiveError
        for f in futures:
            assert f.done()
            with pytest.raises(GateUnresponsiveError):
                f.result()
        # Pending dict cleared
        assert len(gate._pending) == 0

    @pytest.mark.asyncio
    async def test_keepalive_connection_error_exits_cleanly(self):
        """Keepalive loop exits on BrokenPipeError without marking unhealthy."""
        runner = RemoteModuleRunner()
        runner.KEEPALIVE_INTERVAL = 0.02
        runner.KEEPALIVE_TIMEOUT = 5.0

        gate = _make_gate()

        # _send_and_wait raises a transport error
        async def broken_send(gate, msg_type, data, timeout=None):
            raise BrokenPipeError("Connection lost")

        with patch.object(runner, "_send_and_wait", side_effect=broken_send):
            await asyncio.wait_for(
                runner._keepalive_loop(gate, "test:22:user"),
                timeout=1.0,
            )
        # The loop breaks on connection errors — reader loop handles cleanup


class TestUnhealthyGateEviction:
    """Tests for unhealthy gate eviction from cache."""

    @pytest.mark.asyncio
    async def test_unhealthy_gate_evicted_on_cache_lookup(self):
        """_get_or_create_gate evicts unhealthy gates and creates new ones."""
        runner = RemoteModuleRunner()

        # Put an unhealthy gate in cache
        old_gate = _make_gate()
        old_gate.healthy = False
        cache_key = "192.168.1.10:22:root"
        runner.gate_cache[cache_key] = old_gate

        # _connect_gate should be called since the cached gate is unhealthy
        new_gate = _make_gate()
        with patch.object(runner, "_connect_gate", new_callable=AsyncMock, return_value=new_gate) as mock_connect:
            with patch.object(runner, "_close_gate", new_callable=AsyncMock) as mock_close:
                result = await runner._get_or_create_gate(
                    cache_key, "192.168.1.10", 22, "root",
                    None, None, "python3",
                    MagicMock(),
                )

        # Old gate should have been closed
        mock_close.assert_called_once_with(old_gate)
        # New gate should have been created
        mock_connect.assert_called_once()
        assert result is new_gate


class TestCloseGate:
    """Tests for _close_gate lifecycle cleanup."""

    @pytest.mark.asyncio
    async def test_close_gate_marks_unhealthy(self):
        """_close_gate sets healthy=False."""
        runner = RemoteModuleRunner()
        gate = _make_gate()
        assert gate.healthy

        # Mock the protocol to avoid real I/O
        runner.protocol.send_message_with_id = AsyncMock()

        await runner._close_gate(gate)
        assert not gate.healthy

    @pytest.mark.asyncio
    async def test_close_gate_cancels_keepalive_task(self):
        """_close_gate cancels the keepalive task."""
        runner = RemoteModuleRunner()
        gate = _make_gate()

        # Create a fake keepalive task
        async def fake_loop():
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                return

        gate._keepalive_task = asyncio.create_task(fake_loop())

        runner.protocol.send_message_with_id = AsyncMock()
        await runner._close_gate(gate)

        assert gate._keepalive_task.cancelled() or gate._keepalive_task.done()

    @pytest.mark.asyncio
    async def test_close_gate_fails_pending_futures(self):
        """_close_gate fails all pending futures with FTL2ConnectionError."""
        runner = RemoteModuleRunner()
        gate = _make_gate()

        futures = [gate.create_future(gate.next_msg_id()) for _ in range(3)]

        runner.protocol.send_message_with_id = AsyncMock()
        await runner._close_gate(gate)

        for f in futures:
            assert f.done()
            with pytest.raises(FTL2ConnectionError, match="shutting down"):
                f.result()
        assert len(gate._pending) == 0


class TestGateDataclass:
    """Tests for Gate dataclass keepalive fields."""

    def test_gate_has_healthy_field(self):
        """Gate defaults to healthy=True."""
        gate = _make_gate()
        assert gate.healthy is True

    def test_gate_has_keepalive_task_field(self):
        """Gate defaults to _keepalive_task=None."""
        gate = _make_gate()
        assert gate._keepalive_task is None


class TestTimeoutConstants:
    """Tests for RemoteModuleRunner timeout constants."""

    def test_default_timeout_values(self):
        """Verify default timeout constants are sensible."""
        runner = RemoteModuleRunner()
        assert runner.REQUEST_TIMEOUT == 300.0
        assert runner.HANDSHAKE_TIMEOUT == 30.0
        assert runner.KEEPALIVE_INTERVAL == 30.0
        assert runner.KEEPALIVE_TIMEOUT == 15.0

    def test_keepalive_timeout_less_than_interval(self):
        """Keepalive timeout must be less than interval to avoid overlap."""
        runner = RemoteModuleRunner()
        assert runner.KEEPALIVE_TIMEOUT < runner.KEEPALIVE_INTERVAL


class TestExceptionHierarchy:
    """Tests that exception classes inherit correctly for catch-all handling."""

    def test_gate_request_timeout_is_module_execution_error(self):
        """GateRequestTimeoutError can be caught as ModuleExecutionError."""
        err = GateRequestTimeoutError("test timeout")
        assert isinstance(err, ModuleExecutionError)

    def test_gate_handshake_timeout_is_connection_error(self):
        """GateHandshakeTimeoutError can be caught as FTL2ConnectionError."""
        err = GateHandshakeTimeoutError("test timeout")
        assert isinstance(err, FTL2ConnectionError)

    def test_gate_unresponsive_is_connection_error(self):
        """GateUnresponsiveError can be caught as FTL2ConnectionError."""
        err = GateUnresponsiveError("test unresponsive")
        assert isinstance(err, FTL2ConnectionError)

    def test_gate_request_timeout_has_error_context(self):
        """GateRequestTimeoutError carries error context with correct type."""
        err = GateRequestTimeoutError("test timeout", host="web01", module="dnf")
        assert err.context.error_type == "GateTimeout"
        assert err.context.host == "web01"
        assert err.context.module == "dnf"

    def test_gate_unresponsive_has_error_context(self):
        """GateUnresponsiveError carries error context with correct type."""
        err = GateUnresponsiveError("gate hung", host="db01")
        assert err.context.error_type == "GateUnresponsive"
        assert err.context.host == "db01"
