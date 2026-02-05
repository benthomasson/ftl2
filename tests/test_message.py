"""Tests for message protocol."""

import asyncio

import pytest

from ftl2.message import GateProtocol, ProtocolError


class TestGateProtocol:
    """Tests for GateProtocol class."""

    def test_message_types_defined(self):
        """Test that message types are defined."""
        protocol = GateProtocol()
        assert "Hello" in protocol.MESSAGE_TYPES
        assert "Module" in protocol.MESSAGE_TYPES
        assert "Shutdown" in protocol.MESSAGE_TYPES
        assert len(protocol.MESSAGE_TYPES) == 9

    @pytest.mark.asyncio
    async def test_send_message_hello(self):
        """Test sending a Hello message."""
        protocol = GateProtocol()

        # Create mock writer
        class MockWriter:
            def __init__(self):
                self.data = bytearray()

            def write(self, data: bytes):
                self.data.extend(data)

            async def drain(self):
                pass

        writer = MockWriter()
        await protocol.send_message(writer, "Hello", {})

        # Verify format: 8-byte hex length + JSON
        data = bytes(writer.data)
        assert len(data) > 8
        length_hex = data[:8].decode("ascii")
        assert len(length_hex) == 8
        length = int(length_hex, 16)
        assert len(data) == 8 + length

        # Verify JSON content
        json_data = data[8:].decode("utf-8")
        assert '"Hello"' in json_data

    @pytest.mark.asyncio
    async def test_send_message_with_data(self):
        """Test sending a message with data payload."""
        protocol = GateProtocol()

        class MockWriter:
            def __init__(self):
                self.data = bytearray()

            def write(self, data: bytes):
                self.data.extend(data)

            async def drain(self):
                pass

        writer = MockWriter()
        test_data = {"module": "ping", "args": {"data": "test"}}
        await protocol.send_message(writer, "Module", test_data)

        data = bytes(writer.data)
        json_data = data[8:].decode("utf-8")
        assert '"Module"' in json_data
        assert '"ping"' in json_data

    @pytest.mark.asyncio
    async def test_read_message_hello(self):
        """Test reading a Hello message."""
        protocol = GateProtocol()

        # Create mock message
        message_json = '["Hello", {}]'
        message_bytes = message_json.encode("utf-8")
        length = len(message_bytes)
        length_prefix = f"{length:08x}".encode("ascii")
        full_message = length_prefix + message_bytes

        # Create reader from bytes
        reader = asyncio.StreamReader()
        reader.feed_data(full_message)
        reader.feed_eof()

        # Read message
        result = await protocol.read_message(reader)

        assert result is not None
        msg_type, data = result
        assert msg_type == "Hello"
        assert data == {}

    @pytest.mark.asyncio
    async def test_read_message_with_data(self):
        """Test reading a message with data payload."""
        protocol = GateProtocol()

        # Create mock message
        message_json = '["ModuleResult", {"stdout": "pong", "rc": 0}]'
        message_bytes = message_json.encode("utf-8")
        length = len(message_bytes)
        length_prefix = f"{length:08x}".encode("ascii")
        full_message = length_prefix + message_bytes

        # Create reader
        reader = asyncio.StreamReader()
        reader.feed_data(full_message)
        reader.feed_eof()

        # Read message
        result = await protocol.read_message(reader)

        assert result is not None
        msg_type, data = result
        assert msg_type == "ModuleResult"
        assert data["stdout"] == "pong"
        assert data["rc"] == 0

    @pytest.mark.asyncio
    async def test_read_message_eof(self):
        """Test reading message returns None on EOF."""
        protocol = GateProtocol()

        reader = asyncio.StreamReader()
        reader.feed_eof()

        result = await protocol.read_message(reader)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_message_invalid_length_prefix(self):
        """Test reading message with invalid length prefix."""
        protocol = GateProtocol()

        # Invalid hex in length prefix
        reader = asyncio.StreamReader()
        reader.feed_data(b"INVALID!")
        reader.feed_eof()

        with pytest.raises(ProtocolError) as exc_info:
            await protocol.read_message(reader)

        assert "Invalid hex length" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_message_incomplete_length(self):
        """Test reading message with incomplete length prefix."""
        protocol = GateProtocol()

        # Only 4 bytes instead of 8
        reader = asyncio.StreamReader()
        reader.feed_data(b"0000")
        reader.feed_eof()

        with pytest.raises(ProtocolError) as exc_info:
            await protocol.read_message(reader)

        assert "Invalid length prefix" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_message_incomplete_body(self):
        """Test reading message with incomplete body."""
        protocol = GateProtocol()

        # Length says 100 bytes but only provide 10
        reader = asyncio.StreamReader()
        reader.feed_data(b"00000064")  # 100 in hex
        reader.feed_data(b"short")  # Only 5 bytes
        reader.feed_eof()

        with pytest.raises(ProtocolError) as exc_info:
            await protocol.read_message(reader)

        assert "Incomplete message" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_message_invalid_json(self):
        """Test reading message with invalid JSON."""
        protocol = GateProtocol()

        invalid_json = b"{invalid json"
        length = len(invalid_json)
        length_prefix = f"{length:08x}".encode("ascii")
        full_message = length_prefix + invalid_json

        reader = asyncio.StreamReader()
        reader.feed_data(full_message)
        reader.feed_eof()

        with pytest.raises(ProtocolError) as exc_info:
            await protocol.read_message(reader)

        assert "Invalid JSON" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_read_message_invalid_format(self):
        """Test reading message with invalid message format."""
        protocol = GateProtocol()

        # Not a list of 2 elements
        message_json = '{"type": "Hello"}'
        message_bytes = message_json.encode("utf-8")
        length = len(message_bytes)
        length_prefix = f"{length:08x}".encode("ascii")
        full_message = length_prefix + message_bytes

        reader = asyncio.StreamReader()
        reader.feed_data(full_message)
        reader.feed_eof()

        with pytest.raises(ProtocolError) as exc_info:
            await protocol.read_message(reader)

        assert "Invalid message format" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_str(self):
        """Test sending message to text stream."""
        protocol = GateProtocol()

        class MockTextWriter:
            def __init__(self):
                self.data = ""

            def write(self, text: str):
                self.data += text

            async def drain(self):
                pass

        writer = MockTextWriter()
        await protocol.send_message_str(writer, "Hello", {})

        # Verify format
        assert len(writer.data) > 8
        length_hex = writer.data[:8]
        assert len(length_hex) == 8
        int(length_hex, 16)  # Should parse as hex

        json_part = writer.data[8:]
        assert '"Hello"' in json_part

    @pytest.mark.asyncio
    async def test_roundtrip_multiple_messages(self):
        """Test sending and receiving multiple messages."""
        protocol = GateProtocol()

        # Mock writer that collects data
        class MockWriter:
            def __init__(self):
                self.data = bytearray()

            def write(self, data: bytes):
                self.data.extend(data)

            async def drain(self):
                pass

        writer = MockWriter()

        # Send multiple messages
        await protocol.send_message(writer, "Hello", {})
        await protocol.send_message(writer, "Module", {"name": "ping"})
        await protocol.send_message(writer, "Shutdown", {})

        # Create reader from collected data
        reader = asyncio.StreamReader()
        reader.feed_data(bytes(writer.data))
        reader.feed_eof()

        # Read back all messages
        msg1 = await protocol.read_message(reader)
        assert msg1 is not None
        assert msg1[0] == "Hello"

        msg2 = await protocol.read_message(reader)
        assert msg2 is not None
        assert msg2[0] == "Module"
        assert msg2[1]["name"] == "ping"

        msg3 = await protocol.read_message(reader)
        assert msg3 is not None
        assert msg3[0] == "Shutdown"

        # Should be EOF
        msg4 = await protocol.read_message(reader)
        assert msg4 is None
