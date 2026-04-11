"""Tests for GateStatus self-reporting (Issue #67).

Tests the GateStatusReporter class, message types, _collect_status schema,
gate state tracking, and multiplexed/serial handler integration.
"""

import asyncio
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

from ftl2.message import GateProtocol


# ---------------------------------------------------------------------------
# Helpers (reused from test_multiplexing.py pattern)
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
        raise BrokenPipeError("pipe closed")

    async def drain(self) -> None:
        pass


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
        length = int(buf[pos:pos + 8].decode("ascii"), 16)
        pos += 8
        if pos + length > len(buf):
            break
        msg = json.loads(buf[pos:pos + length].decode("utf-8"))
        responses.append(msg)
        pos += length
    return responses


# ---------------------------------------------------------------------------
# Message type registration
# ---------------------------------------------------------------------------

class TestGateStatusMessageTypes:
    """Verify GateStatus message types are registered in the protocol."""

    def test_start_gate_status_in_message_types(self):
        assert "StartGateStatus" in GateProtocol.MESSAGE_TYPES

    def test_stop_gate_status_in_message_types(self):
        assert "StopGateStatus" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_result_in_message_types(self):
        assert "GateStatusResult" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_in_message_types(self):
        assert "GateStatus" in GateProtocol.MESSAGE_TYPES

    def test_gate_status_in_event_types(self):
        """GateStatus is an unsolicited push event, like SystemMetrics."""
        assert "GateStatus" in GateProtocol.EVENT_TYPES

    def test_gate_status_event_alongside_system_metrics(self):
        """Both monitoring event types are registered."""
        assert "SystemMetrics" in GateProtocol.EVENT_TYPES
        assert "GateStatus" in GateProtocol.EVENT_TYPES


# ---------------------------------------------------------------------------
# GateStatusReporter unit tests
# ---------------------------------------------------------------------------

class TestGateStatusReporter:
    """Tests for the GateStatusReporter class itself."""

    def _make_reporter(self, writer=None, gate_hash="test-hash-123"):
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        if writer is None:
            writer = MemoryWriter()
        return GateStatusReporter(protocol, writer, gate_hash), writer

    def test_init_defaults(self):
        """Reporter initializes with correct defaults."""
        reporter, _ = self._make_reporter()
        assert reporter._task is None
        assert reporter._interval == 5.0
        assert reporter._write_lock is None
        assert reporter._active_tasks_ref is None
        assert reporter._gate_hash == "test-hash-123"

    def test_collect_status_schema(self):
        """_collect_status returns all required fields with correct types."""
        reporter, _ = self._make_reporter(gate_hash="abc-def-789")
        status = reporter._collect_status()

        # All fields present
        expected_keys = {
            "gate_id", "host", "version", "uptime_seconds", "state",
            "current_task", "cpu_percent", "memory_rss_peak",
            "active_channels", "queue_depth", "error_count", "last_error",
            "module_cache_size", "module_cache_bytes",
        }
        assert set(status.keys()) == expected_keys

        # Type checks
        assert isinstance(status["gate_id"], str)
        assert isinstance(status["host"], str)
        assert isinstance(status["version"], str)
        assert isinstance(status["uptime_seconds"], int)
        assert status["state"] in ("idle", "executing")
        assert isinstance(status["cpu_percent"], float)
        assert isinstance(status["memory_rss_peak"], int)
        assert isinstance(status["active_channels"], int)
        assert isinstance(status["queue_depth"], int)
        assert isinstance(status["error_count"], int)
        assert isinstance(status["module_cache_size"], int)
        assert isinstance(status["module_cache_bytes"], int)

    def test_collect_status_gate_id_format(self):
        """gate_id is '{hostname}-{pid}'."""
        reporter, _ = self._make_reporter()
        status = reporter._collect_status()
        expected_id = f"{os.uname().nodename}-{os.getpid()}"
        assert status["gate_id"] == expected_id

    def test_collect_status_version_is_gate_hash(self):
        """version field reflects the gate_hash provided at construction."""
        reporter, _ = self._make_reporter(gate_hash="my-version-42")
        status = reporter._collect_status()
        assert status["version"] == "my-version-42"

    def test_collect_status_idle_state(self):
        """State is 'idle' when no tasks are active."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved = gate_mod._gate_active_tasks
        try:
            gate_mod._gate_active_tasks = 0
            reporter, _ = self._make_reporter()
            status = reporter._collect_status()
            assert status["state"] == "idle"
            assert status["current_task"] is None
        finally:
            gate_mod._gate_active_tasks = saved

    def test_collect_status_executing_state(self):
        """State is 'executing' when tasks are active, current_task lists them."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_tasks = gate_mod._gate_active_tasks
        saved_current = gate_mod._gate_current_tasks.copy()
        try:
            gate_mod._gate_active_tasks = 2
            gate_mod._gate_current_tasks = {"dnf", "file"}
            reporter, _ = self._make_reporter()
            status = reporter._collect_status()
            assert status["state"] == "executing"
            assert status["current_task"] == ["dnf", "file"]  # sorted
        finally:
            gate_mod._gate_active_tasks = saved_tasks
            gate_mod._gate_current_tasks = saved_current

    def test_collect_status_error_tracking(self):
        """Error count and last_error reflect gate state globals."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_count = gate_mod._gate_error_count
        saved_last = gate_mod._gate_last_error
        try:
            gate_mod._gate_error_count = 7
            gate_mod._gate_last_error = "module 'foo' not found"
            reporter, _ = self._make_reporter()
            status = reporter._collect_status()
            assert status["error_count"] == 7
            assert status["last_error"] == "module 'foo' not found"
        finally:
            gate_mod._gate_error_count = saved_count
            gate_mod._gate_last_error = saved_last

    def test_collect_status_module_cache(self):
        """module_cache_size and module_cache_bytes reflect _module_cache."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_cache = gate_mod._module_cache.copy()
        try:
            gate_mod._module_cache = {
                "ping": b"x" * 100,
                "file": b"y" * 200,
            }
            reporter, _ = self._make_reporter()
            status = reporter._collect_status()
            assert status["module_cache_size"] == 2
            assert status["module_cache_bytes"] == 300
        finally:
            gate_mod._module_cache = saved_cache

    def test_collect_status_active_channels_serial_mode(self):
        """Without active_tasks_ref, active_channels is 0 when idle, 1 when executing."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved = gate_mod._gate_active_tasks
        try:
            gate_mod._gate_active_tasks = 0
            reporter, _ = self._make_reporter()
            assert reporter._active_tasks_ref is None
            status = reporter._collect_status()
            assert status["active_channels"] == 0

            gate_mod._gate_active_tasks = 1
            status = reporter._collect_status()
            assert status["active_channels"] == 1
        finally:
            gate_mod._gate_active_tasks = saved

    def test_collect_status_active_channels_multiplexed_mode(self):
        """With active_tasks_ref set, active_channels = len(tasks_set)."""
        reporter, _ = self._make_reporter()
        tasks = {"task_a", "task_b", "task_c"}
        reporter._active_tasks_ref = tasks
        status = reporter._collect_status()
        assert status["active_channels"] == 3

    def test_collect_status_queue_depth_always_zero(self):
        """queue_depth is always 0 (no queuing in FTL2)."""
        reporter, _ = self._make_reporter()
        status = reporter._collect_status()
        assert status["queue_depth"] == 0

    def test_collect_status_uptime_positive(self):
        """uptime_seconds is non-negative."""
        reporter, _ = self._make_reporter()
        status = reporter._collect_status()
        assert status["uptime_seconds"] >= 0

    def test_collect_status_memory_rss_peak_positive(self):
        """memory_rss_peak is a positive integer (we're using memory)."""
        reporter, _ = self._make_reporter()
        status = reporter._collect_status()
        assert status["memory_rss_peak"] > 0

    def test_collect_status_cpu_percent_non_negative(self):
        """cpu_percent is >= 0."""
        reporter, _ = self._make_reporter()
        status = reporter._collect_status()
        assert status["cpu_percent"] >= 0.0

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        """Calling start() twice doesn't create a second background task."""
        reporter, _ = self._make_reporter()
        reporter.start(interval=100.0)  # Long interval so it doesn't fire
        first_task = reporter._task
        assert first_task is not None

        reporter.start(interval=50.0)  # Should be a no-op
        assert reporter._task is first_task  # Same task object

        reporter.stop()

    def test_stop_when_not_started(self):
        """Calling stop() when not started is a no-op (no crash)."""
        reporter, _ = self._make_reporter()
        assert reporter._task is None
        reporter.stop()  # Should not raise
        assert reporter._task is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        """stop() cancels the background task and sets it to None."""
        reporter, _ = self._make_reporter()
        reporter.start(interval=100.0)
        assert reporter._task is not None
        reporter.stop()
        assert reporter._task is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        """Calling stop() twice is safe."""
        reporter, _ = self._make_reporter()
        reporter.start(interval=100.0)
        reporter.stop()
        reporter.stop()  # Should not raise
        assert reporter._task is None


# ---------------------------------------------------------------------------
# Status loop integration
# ---------------------------------------------------------------------------

class TestGateStatusLoop:
    """Tests for the background _status_loop emitting GateStatus events."""

    @pytest.mark.asyncio
    async def test_status_loop_emits_event(self):
        """The status loop emits a GateStatus event after one interval."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "loop-test")

        reporter.start(interval=0.1)  # Short interval for testing
        await asyncio.sleep(0.25)  # Wait for at least one emission
        reporter.stop()

        responses = parse_responses(writer.buffer)
        gate_status_events = [r for r in responses if r[0] == "GateStatus"]
        assert len(gate_status_events) >= 1, f"Expected GateStatus events, got: {responses}"

        # Verify the emitted event has correct schema
        event = gate_status_events[0]
        status_data = event[1]
        assert "gate_id" in status_data
        assert "state" in status_data
        assert status_data["version"] == "loop-test"

    @pytest.mark.asyncio
    async def test_status_loop_uses_write_lock(self):
        """When _write_lock is set, the loop acquires it before writing."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "lock-test")

        lock = asyncio.Lock()
        reporter._write_lock = lock

        reporter.start(interval=0.1)
        await asyncio.sleep(0.25)
        reporter.stop()

        # If the lock was used correctly, we still get events
        responses = parse_responses(writer.buffer)
        gate_status_events = [r for r in responses if r[0] == "GateStatus"]
        assert len(gate_status_events) >= 1

    @pytest.mark.asyncio
    async def test_status_loop_handles_broken_pipe(self):
        """BrokenPipeError during write stops the loop gracefully."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = BrokenWriter()
        reporter = GateStatusReporter(protocol, writer, "broken-test")

        reporter.start(interval=0.05)
        await asyncio.sleep(0.2)
        # The loop should have exited due to BrokenPipeError, no crash
        reporter.stop()

    @pytest.mark.asyncio
    async def test_status_loop_cancelled_on_stop(self):
        """Stopping the reporter cancels the asyncio task cleanly."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "cancel-test")

        reporter.start(interval=0.1)
        task = reporter._task
        assert not task.done()

        reporter.stop()
        # Give the event loop a chance to process the cancellation
        await asyncio.sleep(0.05)
        assert task.cancelled() or task.done()


# ---------------------------------------------------------------------------
# GateStatus event routing via _gate_reader_loop
# ---------------------------------------------------------------------------

class TestGateStatusEventRouting:
    """Test that GateStatus events route correctly through the reader loop."""

    @pytest.mark.asyncio
    async def test_gate_status_routed_to_event_callback(self):
        """GateStatus 2-tuple events are dispatched to the event callback."""
        from ftl2.runners import Gate, _gate_reader_loop
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["GateStatus", {"gate_id": "host-1234", "state": "idle", "uptime_seconds": 60}],
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
        assert events[0][1]["gate_id"] == "host-1234"
        assert events[0][1]["state"] == "idle"

    @pytest.mark.asyncio
    async def test_gate_status_dropped_without_callback(self):
        """GateStatus events are silently dropped when no callback is set."""
        from ftl2.runners import Gate, _gate_reader_loop
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["GateStatus", {"state": "idle"}],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        # Should complete without error
        await _gate_reader_loop(gate, protocol, event_callback=None)


# ---------------------------------------------------------------------------
# Multiplexed mode integration
# ---------------------------------------------------------------------------

class TestGateStatusMultiplexed:
    """Integration tests for GateStatus in multiplexed mode."""

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        """StartGateStatus → GateStatusResult(ok), StopGateStatus → GateStatusResult(stopped)."""
        from ftl2.ftl_gate.__main__ import main_multiplexed, FileWatcher, SystemMonitor, GateStatusReporter

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 10.0}, 1],
            ["StopGateStatus", {}, 2],
            ["Shutdown", {}, 3],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        status_reporter = GateStatusReporter(protocol, writer, "mux-test")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, status_reporter, "mux-test")

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}

        assert by_id[1][0] == "GateStatusResult"
        assert by_id[1][1]["status"] == "ok"
        assert by_id[2][0] == "GateStatusResult"
        assert by_id[2][1]["status"] == "stopped"
        assert by_id[3][0] == "Goodbye"

    @pytest.mark.asyncio
    async def test_start_with_custom_interval(self):
        """StartGateStatus accepts a custom interval."""
        from ftl2.ftl_gate.__main__ import main_multiplexed, FileWatcher, SystemMonitor, GateStatusReporter

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 30.0}, 10],
            ["StopGateStatus", {}, 11],
            ["Shutdown", {}, 12],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        status_reporter = GateStatusReporter(protocol, writer, "interval-test")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, status_reporter, "interval-test")

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}
        assert by_id[10][1]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_start_without_interval_uses_default(self):
        """StartGateStatus with empty data uses default 5.0s interval."""
        from ftl2.ftl_gate.__main__ import main_multiplexed, FileWatcher, SystemMonitor, GateStatusReporter

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {}, 20],
            ["StopGateStatus", {}, 21],
            ["Shutdown", {}, 22],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        status_reporter = GateStatusReporter(protocol, writer, "default-test")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, status_reporter, "default-test")

        responses = parse_responses(writer.buffer)
        by_id = {r[2]: r for r in responses if len(r) == 3}
        assert by_id[20][0] == "GateStatusResult"
        assert by_id[20][1]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_shutdown_stops_reporter(self):
        """Shutdown cleans up the status reporter (via finally block)."""
        from ftl2.ftl_gate.__main__ import main_multiplexed, FileWatcher, SystemMonitor, GateStatusReporter

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 100.0}, 30],
            ["Shutdown", {}, 31],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        status_reporter = GateStatusReporter(protocol, writer, "shutdown-test")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, status_reporter, "shutdown-test")

        # After shutdown, the reporter's task should be cleaned up
        assert status_reporter._task is None

    @pytest.mark.asyncio
    async def test_gate_status_with_short_interval_emits_events(self):
        """With a short interval, GateStatus events appear in the output alongside responses."""
        from ftl2.ftl_gate.__main__ import main_multiplexed, FileWatcher, SystemMonitor, GateStatusReporter

        protocol = GateProtocol()

        # Build messages manually with a delay mechanism:
        # Start with a short interval, then wait via slow reader, then stop+shutdown
        reader = make_reader_from_messages([
            ["StartGateStatus", {"interval": 0.1}, 40],
            ["StopGateStatus", {}, 41],
            ["Shutdown", {}, 42],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        status_reporter = GateStatusReporter(protocol, writer, "emit-test")

        # We need to give the reporter time to emit. Use a wrapper reader
        # that delays before the StopGateStatus message.
        original_reader = reader

        # Create a reader with built-in delay
        delayed_buf = bytearray()
        msgs = [
            ["StartGateStatus", {"interval": 0.1}, 40],
        ]
        for msg in msgs:
            json_bytes = json.dumps(msg).encode("utf-8")
            length_prefix = f"{len(json_bytes):08x}".encode("ascii")
            delayed_buf.extend(length_prefix)
            delayed_buf.extend(json_bytes)

        delayed_reader = asyncio.StreamReader()
        delayed_reader.feed_data(bytes(delayed_buf))

        # Feed the rest after a delay
        async def feed_rest():
            await asyncio.sleep(0.35)  # Let 2-3 status events emit
            rest_msgs = [
                ["StopGateStatus", {}, 41],
                ["Shutdown", {}, 42],
            ]
            rest_buf = bytearray()
            for msg in rest_msgs:
                json_bytes = json.dumps(msg).encode("utf-8")
                length_prefix = f"{len(json_bytes):08x}".encode("ascii")
                rest_buf.extend(length_prefix)
                rest_buf.extend(json_bytes)
            delayed_reader.feed_data(bytes(rest_buf))
            delayed_reader.feed_eof()

        feeder_task = asyncio.create_task(feed_rest())

        await main_multiplexed(
            delayed_reader, writer, protocol, watcher, monitor, status_reporter, "emit-test"
        )
        await feeder_task

        responses = parse_responses(writer.buffer)
        gate_status_events = [r for r in responses if r[0] == "GateStatus"]
        assert len(gate_status_events) >= 1, (
            f"Expected at least one GateStatus event, got: {[r[0] for r in responses]}"
        )

        # Verify the event schema
        event_data = gate_status_events[0][1]
        assert event_data["version"] == "emit-test"
        assert "state" in event_data
        assert "gate_id" in event_data


# ---------------------------------------------------------------------------
# Edge cases from reviewer notes
# ---------------------------------------------------------------------------

class TestGateStatusEdgeCases:
    """Edge cases identified during code review."""

    def test_current_task_returns_sorted_list(self):
        """When multiple tasks execute, current_task is a sorted list."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_tasks = gate_mod._gate_active_tasks
        saved_current = gate_mod._gate_current_tasks.copy()
        try:
            gate_mod._gate_active_tasks = 3
            gate_mod._gate_current_tasks = {"zzz_module", "aaa_module", "mmm_module"}

            from ftl2.ftl_gate.__main__ import GateStatusReporter
            protocol = GateProtocol()
            writer = MemoryWriter()
            reporter = GateStatusReporter(protocol, writer, "sort-test")
            status = reporter._collect_status()

            assert status["current_task"] == ["aaa_module", "mmm_module", "zzz_module"]
        finally:
            gate_mod._gate_active_tasks = saved_tasks
            gate_mod._gate_current_tasks = saved_current

    def test_collect_status_with_empty_cache(self):
        """module_cache_size=0 and module_cache_bytes=0 when cache is empty."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_cache = gate_mod._module_cache.copy()
        try:
            gate_mod._module_cache = {}
            from ftl2.ftl_gate.__main__ import GateStatusReporter
            protocol = GateProtocol()
            writer = MemoryWriter()
            reporter = GateStatusReporter(protocol, writer, "cache-test")
            status = reporter._collect_status()
            assert status["module_cache_size"] == 0
            assert status["module_cache_bytes"] == 0
        finally:
            gate_mod._module_cache = saved_cache

    def test_collect_status_host_matches_uname(self):
        """host field matches os.uname().nodename."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "host-test")
        status = reporter._collect_status()
        assert status["host"] == os.uname().nodename

    def test_memory_rss_peak_platform_handling(self):
        """memory_rss_peak uses correct units per platform."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        import resource as _resource

        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "mem-test")
        status = reporter._collect_status()

        rusage = _resource.getrusage(_resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            # macOS: ru_maxrss is in bytes
            assert status["memory_rss_peak"] == rusage.ru_maxrss
        else:
            # Linux: ru_maxrss is in KB, multiplied by 1024
            assert status["memory_rss_peak"] == rusage.ru_maxrss * 1024

    def test_cpu_percent_handles_zero_elapsed(self):
        """CPU percent doesn't divide by zero on rapid successive calls."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "cpu-test")

        # Force _prev_sample_time to now so elapsed ≈ 0
        reporter._prev_sample_time = time.time()
        reporter._prev_times = os.times()
        status = reporter._collect_status()
        assert status["cpu_percent"] >= 0.0  # Should not raise

    def test_no_errors_initially(self):
        """Fresh gate state has 0 errors and None last_error."""
        import ftl2.ftl_gate.__main__ as gate_mod
        saved_count = gate_mod._gate_error_count
        saved_last = gate_mod._gate_last_error
        try:
            gate_mod._gate_error_count = 0
            gate_mod._gate_last_error = None

            from ftl2.ftl_gate.__main__ import GateStatusReporter
            protocol = GateProtocol()
            writer = MemoryWriter()
            reporter = GateStatusReporter(protocol, writer, "fresh-test")
            status = reporter._collect_status()
            assert status["error_count"] == 0
            assert status["last_error"] is None
        finally:
            gate_mod._gate_error_count = saved_count
            gate_mod._gate_last_error = saved_last

    def test_collect_status_is_json_serializable(self):
        """The status dict must be JSON-serializable for the wire protocol."""
        from ftl2.ftl_gate.__main__ import GateStatusReporter
        protocol = GateProtocol()
        writer = MemoryWriter()
        reporter = GateStatusReporter(protocol, writer, "json-test")
        status = reporter._collect_status()
        # Should not raise
        serialized = json.dumps(status)
        deserialized = json.loads(serialized)
        assert deserialized == status
