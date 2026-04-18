"""Tests for gate multiplexing paths.

Tests the Gate dataclass, _gate_reader_loop routing, cache key format,
and multiplexed message handling.
"""

import asyncio
import json

import pytest

from ftl2.message import GateProtocol
from ftl2.runners import Gate, _gate_reader_loop
from ftl2.types import BecomeConfig, gate_cache_key

# ---------------------------------------------------------------------------
# Helpers: in-memory async reader/writer for protocol testing
# ---------------------------------------------------------------------------

class MemoryWriter:
    """Async writer that captures bytes in a buffer."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass


def make_reader_from_messages(protocol_messages: list) -> asyncio.StreamReader:
    """Build a StreamReader pre-loaded with length-prefixed JSON messages.

    Each item in protocol_messages should be a list like:
      ["ModuleResult", {"stdout": "ok"}, 1]   (3-tuple)
      ["FileChanged", {"path": "/etc/hosts"}]  (2-tuple)
    """
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


# ---------------------------------------------------------------------------
# Gate dataclass
# ---------------------------------------------------------------------------

class TestGateDataclass:
    """Tests for the Gate dataclass helper methods."""

    def test_next_msg_id_increments(self):
        gate = Gate.__new__(Gate)
        gate._msg_counter = 0
        assert gate.next_msg_id() == 1
        assert gate.next_msg_id() == 2
        assert gate.next_msg_id() == 3

    def test_create_future(self):
        gate = Gate.__new__(Gate)
        gate._pending = {}

        loop = asyncio.new_event_loop()
        try:
            future = loop.run_until_complete(self._create_future_async(gate))
            assert 42 in gate._pending
            assert gate._pending[42] is future
            assert not future.done()
        finally:
            loop.close()

    @staticmethod
    async def _create_future_async(gate):
        return gate.create_future(42)


# ---------------------------------------------------------------------------
# _gate_reader_loop
# ---------------------------------------------------------------------------

class TestGateReaderLoop:
    """Tests for the background reader loop that routes multiplexed messages."""

    @pytest.mark.asyncio
    async def test_routes_3tuple_to_future(self):
        """3-tuple responses are routed to the correct pending Future."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["ModuleResult", {"stdout": "hello"}, 1],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        future = gate.create_future(1)

        await _gate_reader_loop(gate, protocol)

        assert future.done()
        msg_type, data = future.result()
        assert msg_type == "ModuleResult"
        assert data["stdout"] == "hello"

    @pytest.mark.asyncio
    async def test_routes_2tuple_to_event_callback(self):
        """2-tuple event messages are dispatched to the event callback."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["FileChanged", {"path": "/etc/hosts", "event": "modified"}],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        events = []

        async def callback(event_type, data):
            events.append((event_type, data))

        await _gate_reader_loop(gate, protocol, event_callback=callback)

        assert len(events) == 1
        assert events[0][0] == "FileChanged"
        assert events[0][1]["path"] == "/etc/hosts"

    @pytest.mark.asyncio
    async def test_2tuple_dropped_without_callback(self):
        """2-tuple events are silently dropped when event_callback is None."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["SystemMetrics", {"cpu": 50}],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        # Should complete without error even with no callback
        await _gate_reader_loop(gate, protocol, event_callback=None)

    @pytest.mark.asyncio
    async def test_eof_fails_pending_futures(self):
        """EOF from gate fails all pending futures with ConnectionError."""
        protocol = GateProtocol()
        # Empty reader → immediate EOF
        reader = asyncio.StreamReader()
        reader.feed_eof()

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        f1 = gate.create_future(1)
        f2 = gate.create_future(2)

        await _gate_reader_loop(gate, protocol)

        assert f1.done()
        assert f2.done()
        with pytest.raises(Exception, match="Gate connection closed"):
            f1.result()
        with pytest.raises(Exception, match="Gate connection closed"):
            f2.result()
        assert len(gate._pending) == 0

    @pytest.mark.asyncio
    async def test_multiple_interleaved_messages(self):
        """Multiple 3-tuple and 2-tuple messages are routed correctly."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["ModuleResult", {"name": "ping"}, 1],
            ["FileChanged", {"path": "/tmp/x"}],
            ["ModuleResult", {"name": "file"}, 2],
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

        assert f1.result() == ("ModuleResult", {"name": "ping"})
        assert f2.result() == ("ModuleResult", {"name": "file"})
        assert len(events) == 1
        assert events[0][0] == "FileChanged"

    @pytest.mark.asyncio
    async def test_orphan_msg_id_logged(self):
        """Response for unknown msg_id is logged but doesn't crash."""
        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["ModuleResult", {"stdout": "orphan"}, 999],
        ])

        gate = Gate.__new__(Gate)
        gate._pending = {}
        gate.gate_process = type("P", (), {"stdout": reader})()

        # No future created for msg_id=999
        await _gate_reader_loop(gate, protocol)
        # Should complete without error


# ---------------------------------------------------------------------------
# Cache key consistency
# ---------------------------------------------------------------------------

class TestCacheKeyFormat:
    """Verify gate_cache_key produces correct keys."""

    def test_no_become(self):
        assert gate_cache_key("web01") == "web01"

    def test_become_none(self):
        assert gate_cache_key("web01", None) == "web01"

    def test_become_disabled(self):
        bc = BecomeConfig(become=False)
        assert gate_cache_key("web01", bc) == "web01"

    def test_become_root_sudo(self):
        bc = BecomeConfig(become=True, become_user="root", become_method="sudo")
        assert gate_cache_key("web01", bc) == "web01:become=root:method=sudo"

    def test_become_deploy_doas(self):
        bc = BecomeConfig(become=True, become_user="deploy", become_method="doas")
        assert gate_cache_key("web01", bc) == "web01:become=deploy:method=doas"

    def test_different_users_no_collision(self):
        bc1 = BecomeConfig(become=True, become_user="root")
        bc2 = BecomeConfig(become=True, become_user="deploy")
        assert gate_cache_key("web01", bc1) != gate_cache_key("web01", bc2)

    def test_different_methods_no_collision(self):
        bc_sudo = BecomeConfig(become=True, become_method="sudo")
        bc_doas = BecomeConfig(become=True, become_method="doas")
        assert gate_cache_key("web01", bc_sudo) != gate_cache_key("web01", bc_doas)


# ---------------------------------------------------------------------------
# main_multiplexed message handling
# ---------------------------------------------------------------------------

class TestMainMultiplexed:
    """Tests for the multiplexed gate entry point."""

    @pytest.mark.asyncio
    async def test_shutdown_sends_goodbye(self):
        """Shutdown message gets a Goodbye response."""
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
        gate_status_monitor = GateStatusMonitor(protocol, writer, "abc123")

        result = await main_multiplexed(reader, writer, protocol, watcher, monitor, "abc123", gate_status_monitor=gate_status_monitor)

        assert result is None
        # Parse the response from writer buffer
        response = json.loads(writer.buffer[8:].decode())
        assert response[0] == "Goodbye"
        assert response[2] == 1  # same msg_id

    @pytest.mark.asyncio
    async def test_module_execution(self):
        """Module request gets a response with the correct msg_id."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        # Send a Module request for a module that won't be found (no gate bundle)
        # followed by Shutdown
        reader = make_reader_from_messages([
            ["Module", {"module_name": "nonexistent_test_module", "module_args": {}}, 5],
            ["Shutdown", {}, 6],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "abc123")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, "abc123", gate_status_monitor=gate_status_monitor)

        # Parse all responses from writer buffer
        responses = self._parse_responses(writer.buffer)
        assert len(responses) >= 2

        # Find responses by msg_id (order may vary due to concurrency)
        by_id = {r[2]: r for r in responses if len(r) == 3}
        assert 5 in by_id, f"No response for msg_id=5 in {responses}"
        assert by_id[5][0] == "ModuleNotFound"
        assert 6 in by_id
        assert by_id[6][0] == "Goodbye"

    @pytest.mark.asyncio
    async def test_info_request(self):
        """Info request returns gate info with correct msg_id."""
        from ftl2.ftl_gate.__main__ import (
            FileWatcher,
            GateStatusMonitor,
            SystemMonitor,
            main_multiplexed,
        )

        protocol = GateProtocol()
        reader = make_reader_from_messages([
            ["Info", {}, 10],
            ["Shutdown", {}, 11],
        ])
        writer = MemoryWriter()
        watcher = FileWatcher(protocol, writer)
        monitor = SystemMonitor(protocol, writer)
        gate_status_monitor = GateStatusMonitor(protocol, writer, "abc123")

        await main_multiplexed(reader, writer, protocol, watcher, monitor, "abc123", gate_status_monitor=gate_status_monitor)

        responses = self._parse_responses(writer.buffer)
        info_resp = [r for r in responses if r[0] == "InfoResult"]
        assert len(info_resp) == 1
        assert info_resp[0][2] == 10
        assert "python_version" in info_resp[0][1]

    @staticmethod
    def _parse_responses(buf: bytearray) -> list:
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
