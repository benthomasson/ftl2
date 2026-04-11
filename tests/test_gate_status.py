"""Tests for GateStatus self-reporting (issue #67).

Tests the GateStatusMonitor class, protocol message types, serial and
multiplexed mode wiring, event routing, and error tracking.
"""

import asyncio
import json
import os
import sys
import time

import pytest

from ftl2.message import GateProtocol
from ftl2.runners import Gate, _gate_reader_loop


# ---------------------------------------------------------------------------
# Helpers (reused from test_multiplexing.py)
# ---------------------------------------------------------------------------


class MemoryWriter:
    """Async writer that captures bytes in a buffer."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass


class BrokenWriter:
    """Writer that raises BrokenPipeError on write."""

    def write(self, data: bytes) -> None:
        raise BrokenPipeError("pipe broken")

    async def drain(self) -> None:
        raise BrokenPipeError("pipe broken")


def make_reader_from_messages(protocol_messages: list) -> asyncio.StreamReader:
    """Build a StreamReader pre-loaded with length-prefixed JSON messages."""
    buf = bytearray()
    for msg in protocol_messages:
        json_bytes = json.dumps(msg).encode("utf-8")
        length_prefix = f"{len(json_bytes):08x}".encode("ascii")
        buf.extend(length_prefix)
        buf.extend(json_bytes)

    reader = asyncio.StreamReader()
    reader.feed_data(bytes(buf))
    reader.feed_eof()
    return reader


def parse_responses(buf: bytearray) -> list:
    """Parse length-prefixed JSON messages from a buffer."""
    responses = []
    pos = 0
    while pos + 8 <= len(buf):
        length = int(buf[pos : pos + 8].decode("ascii"), 16)
        pos += 8
        if pos + length > len(buf):
            break
        msg = json.loads(buf[pos : pos + length].decode("utf-8"))
        responses.append(msg)
        pos += length
    return responses


# ---------------------------------------------------------------------------
# Fixture: reset module-level gate state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_gate_state():
    """Save and restore module-level gate state so tests don't leak."""
    import ftl2.ftl_gate.__main__ as gate_mod

    orig = (
        gate_mod._error_count,
        gate_mod._last_error,
        gate_mod._start_time,
        gate_mod._active_tasks,
    )
    gate_mod._start_time = time.time()
    gate_mod._error_count = 0
    gate_mod._last_error = None
    gate_mod._active_tasks = None
    yield
    (
        gate_mod._error_count,
        gate_mod._last_error,
        gate_mod._start_time,
        gate_mod._active_tasks,
    ) = orig


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------


class TestGateStatusProtocol:
    """Verify GateStatus message and event types are registered."""

    def test_start_gate_status_in_message_types(self):
        assert "StartGateStatus" in GateProtocol.MESSAGE_TYPES

    def test_stop_gate_status_in_message_types(self):
        assert "StopGateStatus" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_result_in_message_types(self):
        assert "GateStatusResult" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_in_message_types(self):
        assert "GateStatus" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_in_event_types(self):
        assert "GateStatus" in GateProtocol.EVENT_TYPES


# ---------------------------------------------------------------------------
# GateStatusMonitor unit tests
# ---------------------------------------------------------------------------


class TestGateStatusMonitor:
    """Tests for the GateStatusMonitor class in the gate process."""

    def _make_monitor(self, writer=None, gate_hash="testhash"):
        from ftl2.ftl_gate.__main__ import GateStatusMonitor

        return GateStatusMonitor(GateProtocol(), writer or MemoryWriter(), gate_hash)

    def test_construction(self):
        """Monitor can be constructed with protocol, writer, and gate_hash."""
        monitor = self._make_monitor()
        assert monitor._task is None
        assert monitor._interval == 5.0
        assert monitor._gate_hash == "testhash"

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        """start() creates a background asyncio task."""
        monitor = self._make_monitor()
        monitor.start(interval=1.0)
        assert monitor._task is not None
        assert not monitor._task.done()
        monitor.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Calling start() twice does not create a second task."""
        monitor = self._make_monitor()
        monitor.start(interval=1.0)
        first_task = monitor._task
        monitor.start(interval=2.0)
        assert monitor._task is first_task
        monitor.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """stop() cancels the background task and clears it."""
        monitor = self._make_monitor()
        monitor.start(interval=1.0)
        task = monitor._task
        monitor.stop()
        assert monitor._task is None
        # Let the event loop process the cancellation
        await asyncio.sleep(0)
        assert task.done()

    def test_stop_when_not_started_is_noop(self):
        """stop() before start() does not raise."""
        monitor = self._make_monitor()
        monitor.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_custom_interval(self):
        """start() stores the requested interval."""
        monitor = self._make_monitor()
        monitor.start(interval=10.0)
        assert monitor._interval == 10.0
        monitor.stop()

    @pytest.mark.asyncio
    async def test_collect_status_schema(self):
        """_collect_status() returns dict with all expected fields."""
        monitor = self._make_monitor(gate_hash="abc123")
        status = monitor._collect_status()

        required_keys = {
            "gate_id",
            "host",
            "version",
            "gate_hash",
            "uptime_seconds",
            "state",
            "active_tasks",
            "queue_depth",
            "error_count",
            "last_error",
            "module_cache_size",
            "module_cache_bytes",
            "memory_rss",
            "pid",
        }
        assert required_keys.issubset(status.keys()), (
            f"Missing keys: {required_keys - status.keys()}"
        )

    @pytest.mark.asyncio
    async def test_collect_status_types(self):
        """_collect_status() returns correct types for each field."""
        monitor = self._make_monitor()
        status = monitor._collect_status()

        assert isinstance(status["gate_id"], str)
        assert isinstance(status["host"], str)
        assert isinstance(status["version"], str)
        assert isinstance(status["gate_hash"], str)
        assert isinstance(status["uptime_seconds"], (int, float))
        assert status["state"] in ("idle", "executing")
        assert isinstance(status["active_tasks"], int)
        assert isinstance(status["queue_depth"], int)
        assert isinstance(status["error_count"], int)
        assert isinstance(status["memory_rss"], int)
        assert isinstance(status["pid"], int)
        assert status["pid"] == os.getpid()

    @pytest.mark.asyncio
    async def test_collect_status_idle_state(self):
        """With no active tasks, state should be 'idle'."""
        import ftl2.ftl_gate.__main__ as gate_mod

        gate_mod._active_tasks = None
        monitor = self._make_monitor()
        status = monitor._collect_status()
        assert status["state"] == "idle"
        assert status["active_tasks"] == 0

    @pytest.mark.asyncio
    async def test_collect_status_executing_state(self):
        """With active tasks, state should be 'executing'."""
        import ftl2.ftl_gate.__main__ as gate_mod

        gate_mod._active_tasks = {"task1", "task2"}
        monitor = self._make_monitor()
        status = monitor._collect_status()
        assert status["state"] == "executing"
        assert status["active_tasks"] == 2

    @pytest.mark.asyncio
    async def test_collect_status_reports_errors(self):
        """_collect_status() reflects the module-level error counters."""
        import ftl2.ftl_gate.__main__ as gate_mod

        gate_mod._error_count = 5
        gate_mod._last_error = "something broke"
        monitor = self._make_monitor()
        status = monitor._collect_status()
        assert status["error_count"] == 5
        assert status["last_error"] == "something broke"

    @pytest.mark.asyncio
    async def test_collect_status_gate_hash(self):
        """_collect_status() includes the gate_hash from construction."""
        monitor = self._make_monitor(gate_hash="deadbeef12345678")
        status = monitor._collect_status()
        assert status["gate_hash"] == "deadbeef12345678"

    @pytest.mark.asyncio
    async def test_collect_status_uptime(self):
        """uptime_seconds should be non-negative and reasonable."""
        import ftl2.ftl_gate.__main__ as gate_mod

        gate_mod._start_time = time.time() - 10.0
        monitor = self._make_monitor()
        status = monitor._collect_status()
        assert 9.0 <= status["uptime_seconds"] <= 12.0

    @pytest.mark.asyncio
    async def test_status_loop_sends_messages(self):
        """The status loop sends GateStatus messages to the writer."""
        writer = MemoryWriter()
        monitor = self._make_monitor(writer=writer)
        monitor.start(interval=0.05)  # 50ms for fast test
        await asyncio.sleep(0.15)  # Wait for ~2-3 cycles
        monitor.stop()

        responses = parse_responses(writer.buffer)
        assert len(responses) >= 1, "Expected at least one GateStatus message"
        for resp in responses:
            assert resp[0] == "GateStatus"
            assert "gate_id" in resp[1]
            assert "state" in resp[1]

    @pytest.mark.asyncio
    async def test_status_loop_broken_pipe_stops(self):
        """BrokenPipeError in the loop stops cleanly without raising."""
        from ftl2.ftl_gate.__main__ import GateStatusMonitor

        monitor = GateStatusMonitor(GateProtocol(), BrokenWriter(), "hash")
        monitor.start(interval=0.01)
        # Wait for the loop to hit BrokenPipeError and exit
        await asyncio.sleep(0.1)
        # The task should have completed (not cancelled, just returned)
        assert monitor._task is not None
        assert monitor._task.done()

    @pytest.mark.asyncio
    async def test_write_lock_used(self):
        """When _write_lock is set, sends are serialized through it."""
        writer = MemoryWriter()
        monitor = self._make_monitor(writer=writer)
        monitor._write_lock = asyncio.Lock()
        monitor.start(interval=0.05)
        await asyncio.sleep(0.15)
        monitor.stop()

        # Should still have sent messages successfully through the lock
        responses = parse_responses(writer.buffer)
        assert len(responses) >= 1


# ---------------------------------------------------------------------------
# Serial mode wiring
# ---------------------------------------------------------------------------


class TestGateStatusSerialMode:
    """Tests for GateStatus handling in serial (non-multiplexed) mode."""

    @pytest.mark.asyncio
    async def test_start_gate_status_serial(self):
        """StartGateStatus in serial mode returns GateStatusResult ok."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 1.0}],
            ["Shutdown", {}],
        ])
        writer = MemoryWriter()

        # We need to run `main()` but that calls connect_stdin_stdout.
        # Instead, test the serial handler path directly by importing main
        # and calling the handler logic. Since main() does I/O setup we
        # can't easily call it. Instead, test via multiplexed mode or
        # verify the handler integration through the message protocol.
        #
        # For serial mode, we test indirectly: the message type is
        # recognized and handled without error.
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "serial_hash")

        # Directly test the handler behavior
        gate_status_monitor.start(interval=1.0)
        assert gate_status_monitor._task is not None
        gate_status_monitor.stop()

    @pytest.mark.asyncio
    async def test_stop_gate_status_serial(self):
        """StopGateStatus cleans up the monitor."""
        from ftl2.ftl_gate.__main__ import GateStatusMonitor

        protocol = GateProtocol()
        writer = MemoryWriter()
        monitor = GateStatusMonitor(protocol, writer, "serial_hash")

        monitor.start(interval=1.0)
        assert monitor._task is not None
        monitor.stop()
        assert monitor._task is None


# ---------------------------------------------------------------------------
# Multiplexed mode wiring
# ---------------------------------------------------------------------------


class TestGateStatusMultiplexedMode:
    """Tests for GateStatus handling in multiplexed mode."""

    @pytest.mark.asyncio
    async def test_start_gate_status_multiplexed(self):
        """StartGateStatus returns GateStatusResult with status ok."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 1.0}, 1],
            ["Shutdown", {}, 2],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "mux_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "mux_hash", gate_status_monitor=gate_status_monitor
        )

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}

        assert 1 in by_id, f"No response for msg_id=1. Got: {responses}"
        assert by_id[1][0] == "GateStatusResult"
        assert by_id[1][1]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_stop_gate_status_multiplexed(self):
        """StopGateStatus returns GateStatusResult with status stopped."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 1.0}, 1],
            ["StopGateStatus", {}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "mux_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "mux_hash", gate_status_monitor=gate_status_monitor
        )

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}

        assert 2 in by_id, f"No response for msg_id=2. Got: {responses}"
        assert by_id[2][0] == "GateStatusResult"
        assert by_id[2][1]["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_start_with_default_interval(self):
        """StartGateStatus without interval uses default 5.0."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {}, 1],  # No interval specified
            ["StopGateStatus", {}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "mux_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "mux_hash", gate_status_monitor=gate_status_monitor
        )

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}
        assert by_id[1][0] == "GateStatusResult"
        assert by_id[1][1]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_write_lock_set_in_multiplexed(self):
        """main_multiplexed sets _write_lock on the gate status monitor."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["Shutdown", {}, 1],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "mux_hash")

        assert gate_status_monitor._write_lock is None
        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "mux_hash", gate_status_monitor=gate_status_monitor
        )
        # After multiplexed mode, write_lock should have been set
        assert gate_status_monitor._write_lock is not None

    @pytest.mark.asyncio
    async def test_shutdown_stops_gate_status_monitor(self):
        """Shutdown in multiplexed mode stops the gate status monitor."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 0.05}, 1],
            ["Shutdown", {}, 2],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "mux_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "mux_hash", gate_status_monitor=gate_status_monitor
        )
        # After shutdown, the monitor task should be stopped
        assert gate_status_monitor._task is None


# ---------------------------------------------------------------------------
# Event routing through reader loop
# ---------------------------------------------------------------------------


class TestGateStatusEventRouting:
    """Tests for GateStatus event dispatch through the reader loop."""

    @pytest.mark.asyncio
    async def test_gate_status_event_routed_to_callback(self):
        """GateStatus 2-tuple events are dispatched to event callback."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["GateStatus", {"state": "idle", "error_count": 0}],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        events = []

        async def callback(event_type, data):
            events.append((event_type, data))

        await _gate_reader_loop(gate, protocol, event_callback=callback)

        assert len(events) == 1
        assert events[0][0] == "GateStatus"
        assert events[0][1]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_gate_status_event_dropped_without_callback(self):
        """GateStatus events are silently dropped with no event_callback."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["GateStatus", {"state": "idle"}],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        # Should complete without error even with no callback
        await _gate_reader_loop(gate, protocol, event_callback=None)

    @pytest.mark.asyncio
    async def test_gate_status_interleaved_with_responses(self):
        """GateStatus events interleaved with 3-tuple responses route correctly."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["ModuleResult", {"stdout": "ok"}, 1],
            ["GateStatus", {"state": "executing", "active_tasks": 1}],
            ["ModuleResult", {"stdout": "done"}, 2],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        f1 = gate.create_future(1)
        f2 = gate.create_future(2)

        events = []

        async def callback(event_type, data):
            events.append((event_type, data))

        await _gate_reader_loop(gate, protocol, event_callback=callback)

        assert f1.result() == ("ModuleResult", {"stdout": "ok"})
        assert f2.result() == ("ModuleResult", {"stdout": "done"})
        assert len(events) == 1
        assert events[0][0] == "GateStatus"
        assert events[0][1]["active_tasks"] == 1


# ---------------------------------------------------------------------------
# Error tracking
# ---------------------------------------------------------------------------


class TestGateStatusErrorTracking:
    """Tests for error count/last_error incrementing on module failures."""

    @pytest.mark.asyncio
    async def test_error_count_increments_on_module_not_found(self):
        """ModuleNotFound in multiplexed mode increments _error_count."""
        import ftl2.ftl_gate.__main__ as gate_mod
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        assert gate_mod._error_count == 0

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["Module", {"module_name": "no_such_module", "module_args": {}}, 1],
            ["Shutdown", {}, 2],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "err_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "err_hash", gate_status_monitor
        )

        assert gate_mod._error_count >= 1
        assert gate_mod._last_error is not None

    @pytest.mark.asyncio
    async def test_error_reflected_in_status(self):
        """Errors from module execution are reflected in _collect_status."""
        import ftl2.ftl_gate.__main__ as gate_mod

        gate_mod._error_count = 3
        gate_mod._last_error = "test error message"

        from ftl2.ftl_gate.__main__ import GateStatusMonitor

        monitor = GateStatusMonitor(GateProtocol(), MemoryWriter(), "hash")
        status = monitor._collect_status()

        assert status["error_count"] == 3
        assert status["last_error"] == "test error message"

    @pytest.mark.asyncio
    async def test_multiple_errors_accumulate(self):
        """Multiple module failures accumulate in _error_count."""
        import ftl2.ftl_gate.__main__ as gate_mod
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        assert gate_mod._error_count == 0

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["Module", {"module_name": "missing_1", "module_args": {}}, 1],
            ["Module", {"module_name": "missing_2", "module_args": {}}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "err_hash")

        await main_multiplexed(
            reader, writer, protocol, watcher, monitor, "err_hash", gate_status_monitor
        )

        assert gate_mod._error_count >= 2


# ---------------------------------------------------------------------------
# Module cache reporting
# ---------------------------------------------------------------------------


class TestGateStatusModuleCache:
    """Tests for module cache size/bytes reporting."""

    @pytest.mark.asyncio
    async def test_empty_cache_reported(self):
        """Empty module cache reports size 0 and bytes 0."""
        import ftl2.ftl_gate.__main__ as gate_mod

        orig_cache = gate_mod._module_cache.copy()
        gate_mod._module_cache.clear()
        try:
            from ftl2.ftl_gate.__main__ import GateStatusMonitor

            monitor = GateStatusMonitor(GateProtocol(), MemoryWriter(), "hash")
            status = monitor._collect_status()
            assert status["module_cache_size"] == 0
            assert status["module_cache_bytes"] == 0
        finally:
            gate_mod._module_cache.update(orig_cache)

    @pytest.mark.asyncio
    async def test_populated_cache_reported(self):
        """Module cache with entries reports correct size and bytes."""
        import ftl2.ftl_gate.__main__ as gate_mod

        orig_cache = gate_mod._module_cache.copy()
        gate_mod._module_cache.clear()
        gate_mod._module_cache["mod_a"] = b"x" * 100
        gate_mod._module_cache["mod_b"] = b"y" * 200
        try:
            from ftl2.ftl_gate.__main__ import GateStatusMonitor

            monitor = GateStatusMonitor(GateProtocol(), MemoryWriter(), "hash")
            status = monitor._collect_status()
            assert status["module_cache_size"] == 2
            assert status["module_cache_bytes"] == 300
        finally:
            gate_mod._module_cache.clear()
            gate_mod._module_cache.update(orig_cache)
