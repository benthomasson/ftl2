"""Message protocol for gate communication.

Implements length-prefixed JSON protocol for communication between
the main process and remote gate processes via SSH.

Protocol format: [8-byte hex length][JSON message body]

Messages are either 2-tuples or 3-tuples:
  2-tuple: ["MsgType", {data}]           — legacy / event messages
  3-tuple: ["MsgType", {data}, msg_id]   — multiplexed request/response
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio

logger = logging.getLogger(__name__)


class ProtocolError(Exception):
    """Raised when protocol parsing fails."""

    pass


# Maximum allowed message size (64 MB).  Large enough for any legitimate
# payload (base64-encoded module bundles, SystemMetrics, etc.) but small
# enough to prevent memory exhaustion from a malformed length prefix.
MAX_MESSAGE_SIZE = 64 * 1024 * 1024


class GateProtocol:
    """Protocol handler for gate communication.

    Implements send and receive for length-prefixed JSON messages
    used in gate communication over SSH stdin/stdout.
    """

    MESSAGE_TYPES = {
        "Hello",  # Handshake/keepalive
        "Module",  # Execute standard module
        "FTLModule",  # Execute FTL async module
        "Shutdown",  # Clean shutdown
        "ListModules",  # List bundled modules
        "ListModulesResult",  # Response with module list
        "Info",  # Request gate info
        "InfoResult",  # Response with gate info
        "ModuleResult",  # Standard module result
        "FTLModuleResult",  # FTL module result
        "ModuleNotFound",  # Module not in bundle
        "Error",  # Generic error
        "GateSystemError",  # Unhandled exception
        "Watch",  # Subscribe to file change events
        "WatchResult",  # Response to Watch request
        "Unwatch",  # Unsubscribe from file change events
        "UnwatchResult",  # Response to Unwatch request
        "FileChanged",  # Unsolicited file change event
        "StartMonitor",  # Start system metrics streaming
        "StopMonitor",  # Stop system metrics streaming
        "MonitorResult",  # Response to Start/StopMonitor
        "SystemMetrics",  # Unsolicited system metrics event
        "PolicyDenied",  # Gate denied module execution due to policy
        "SetPolicy",  # Update policy on a running gate
        "SetPolicyResult",  # Response to SetPolicy request
        "StartGateStatus",  # Start gate health status streaming
        "StopGateStatus",  # Stop gate health status streaming
        "GateStatusResult",  # Response to Start/StopGateStatus
        "GateStatus",  # Unsolicited gate health status event
        "GateDrain",  # Request gate to stop accepting new work
        "GateDrainResult",  # Response to GateDrain with completion counts
        "Goodbye",  # Final acknowledgement before gate exit
        "GetCoverage",  # Request coverage data from gate
        "GetCoverageResult",  # Response with coverage file path
    }

    EVENT_TYPES = {
        "FileChanged",
        "SystemMetrics",
        "GateStatus",
    }

    async def send_message(
        self,
        writer: asyncio.StreamWriter,
        msg_type: str,
        data: Any,
    ) -> None:
        """Send a message using length-prefixed JSON protocol.

        Args:
            writer: Async stream writer (binary mode)
            msg_type: Message type string
            data: Message data (must be JSON-serializable)

        Raises:
            BrokenPipeError: If connection is broken
            ProtocolError: If message cannot be serialized
        """
        try:
            # Create message tuple
            message = [msg_type, data]

            # Serialize to JSON
            json_str = json.dumps(message)
            json_bytes = json_str.encode("utf-8")

            # Create length prefix (8-byte hex)
            length = len(json_bytes)
            length_prefix = f"{length:08x}".encode("ascii")

            # Write length prefix + message
            writer.write(length_prefix)
            writer.write(json_bytes)
            await writer.drain()

            logger.debug(f"Sent message: {msg_type}, length={length}")

        except BrokenPipeError:
            logger.error("Broken pipe while sending message")
            raise
        except Exception as e:
            logger.exception(f"Failed to send message: {e}")
            raise ProtocolError(f"Failed to send message: {e}") from e

    async def send_message_with_id(
        self,
        writer: asyncio.StreamWriter,
        msg_type: str,
        data: Any,
        msg_id: int,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        """Send a multiplexed message with a msg_id.

        Serialized as 3-tuple: [msg_type, data, msg_id].
        If write_lock is provided, the write+drain is atomic.

        Args:
            writer: Async stream writer (binary mode)
            msg_type: Message type string
            data: Message data (must be JSON-serializable)
            msg_id: Message ID for request/response correlation
            write_lock: Optional lock for concurrent writer access
        """
        try:
            message = [msg_type, data, msg_id]
            json_str = json.dumps(message)
            json_bytes = json_str.encode("utf-8")
            length = len(json_bytes)
            length_prefix = f"{length:08x}".encode("ascii")

            if write_lock:
                async with write_lock:
                    writer.write(length_prefix)
                    writer.write(json_bytes)
                    await writer.drain()
            else:
                writer.write(length_prefix)
                writer.write(json_bytes)
                await writer.drain()

            logger.debug(f"Sent message: {msg_type}, msg_id={msg_id}, length={length}")

        except BrokenPipeError:
            logger.error("Broken pipe while sending message")
            raise
        except Exception as e:
            logger.exception(f"Failed to send message: {e}")
            raise ProtocolError(f"Failed to send message: {e}") from e

    async def send_message_str(
        self,
        writer: Any,
        msg_type: str,
        data: Any,
    ) -> None:
        """Send a message to a text stream writer.

        Used for SSH stdin which may be text mode instead of binary.

        Args:
            writer: Stream writer (text or binary mode)
            msg_type: Message type string
            data: Message data (must be JSON-serializable)

        Raises:
            BrokenPipeError: If connection is broken
            ProtocolError: If message cannot be serialized
        """
        try:
            # Create message tuple
            message = [msg_type, data]

            # Serialize to JSON
            json_str = json.dumps(message)

            # Create length prefix (8-byte hex)
            length = len(json_str.encode("utf-8"))
            length_prefix = f"{length:08x}"

            # Write length prefix + message
            full_message = length_prefix + json_str
            writer.write(full_message)
            await writer.drain()

            logger.debug(f"Sent message (text): {msg_type}, length={length}")

        except BrokenPipeError:
            logger.error("Broken pipe while sending message")
            raise
        except Exception as e:
            logger.exception(f"Failed to send message: {e}")
            raise ProtocolError(f"Failed to send message: {e}") from e

    async def read_message(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, Any] | tuple[str, Any, int] | None:
        """Read a message using length-prefixed JSON protocol.

        Args:
            reader: Async stream reader (binary mode)

        Returns:
            2-tuple (msg_type, data) for legacy/event messages,
            3-tuple (msg_type, data, msg_id) for multiplexed messages,
            or None on EOF.

        Raises:
            ProtocolError: If message format is invalid
        """
        try:
            # Read 8-byte hex length prefix, skipping leading
            # whitespace for manual debugging (allows entering
            # length and JSON on separate lines interactively):
            #   python __main__.py
            #   0000000d
            #   ["Hello", {}]
            #   00000010
            #   ["Shutdown", {}]
            length_bytes = b""
            while len(length_bytes) < 8:
                chunk = await reader.read(8 - len(length_bytes))
                if not chunk:
                    if length_bytes:
                        raise ProtocolError(
                            f"Invalid length prefix: got {len(length_bytes)} bytes, expected 8"
                        )
                    return None
                # Skip leading whitespace only before prefix starts
                if not length_bytes:
                    chunk = chunk.lstrip()
                    if not chunk:
                        continue
                length_bytes += chunk

            # Parse hex length
            try:
                length_hex = length_bytes.decode("ascii")
                length = int(length_hex, 16)
            except (ValueError, UnicodeDecodeError) as e:
                raise ProtocolError(f"Invalid hex length: {length_bytes!r}") from e

            if length > MAX_MESSAGE_SIZE:
                raise ProtocolError(
                    f"Message size {length} bytes exceeds maximum "
                    f"({MAX_MESSAGE_SIZE} bytes)"
                )

            # Read message body, skipping leading whitespace
            # (newline between length and body in interactive mode)
            # but not stripping body content to preserve byte count
            json_bytes = b""
            while len(json_bytes) < length:
                remaining = length - len(json_bytes)
                chunk = await reader.read(remaining)
                if not chunk:
                    raise ProtocolError(
                        f"Incomplete message body: got {len(json_bytes)} bytes, expected {length}"
                    )
                # Skip leading whitespace only before body starts
                if not json_bytes:
                    chunk = chunk.lstrip()
                    if not chunk:
                        continue
                json_bytes += chunk

            # Parse JSON
            try:
                json_str = json_bytes.decode("utf-8")
                message = json.loads(json_str)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ProtocolError(f"Invalid JSON: {json_bytes[:100]!r}") from e

            # Validate message format (2-tuple or 3-tuple)
            if not isinstance(message, list) or len(message) not in (2, 3):
                raise ProtocolError(f"Invalid message format: {message}")

            msg_type = message[0]
            data = message[1]

            if not isinstance(msg_type, str):
                raise ProtocolError(f"Invalid message type: {msg_type}")

            logger.debug(f"Received message: {msg_type}, length={length}")

            if len(message) == 3:
                return (msg_type, data, message[2])
            return (msg_type, data)

        except ProtocolError:
            raise
        except Exception as e:
            logger.exception(f"Failed to read message: {e}")
            raise ProtocolError(f"Failed to read message: {e}") from e
