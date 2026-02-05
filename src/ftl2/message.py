"""Message protocol for gate communication.

Implements length-prefixed JSON protocol for communication between
the main process and remote gate processes via SSH.

Protocol format: [8-byte hex length][JSON message body]
Example: "0000001a" + ["Hello", {}]
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ProtocolError(Exception):
    """Raised when protocol parsing fails."""

    pass


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
        "ModuleResult",  # Standard module result
        "FTLModuleResult",  # FTL module result
        "ModuleNotFound",  # Module not in bundle
        "Error",  # Generic error
        "GateSystemError",  # Unhandled exception
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
    ) -> tuple[str, Any] | None:
        """Read a message using length-prefixed JSON protocol.

        Args:
            reader: Async stream reader (binary mode)

        Returns:
            Tuple of (message_type, data) or None on EOF

        Raises:
            ProtocolError: If message format is invalid
        """
        try:
            # Read 8-byte hex length prefix
            length_bytes = await reader.read(8)
            if not length_bytes:
                # EOF - normal termination
                return None

            if len(length_bytes) != 8:
                raise ProtocolError(
                    f"Invalid length prefix: expected 8 bytes, got {len(length_bytes)}"
                )

            # Parse hex length
            try:
                length_hex = length_bytes.decode("ascii")
                length = int(length_hex, 16)
            except (ValueError, UnicodeDecodeError) as e:
                raise ProtocolError(f"Invalid hex length: {length_bytes!r}") from e

            # Read message body
            json_bytes = await reader.read(length)
            if len(json_bytes) != length:
                raise ProtocolError(
                    f"Incomplete message: expected {length} bytes, got {len(json_bytes)}"
                )

            # Parse JSON
            try:
                json_str = json_bytes.decode("utf-8")
                message = json.loads(json_str)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ProtocolError(f"Invalid JSON: {json_bytes[:100]!r}") from e

            # Validate message format
            if not isinstance(message, list) or len(message) != 2:
                raise ProtocolError(f"Invalid message format: {message}")

            msg_type, data = message

            if not isinstance(msg_type, str):
                raise ProtocolError(f"Invalid message type: {msg_type}")

            logger.debug(f"Received message: {msg_type}, length={length}")

            return (msg_type, data)

        except ProtocolError:
            raise
        except Exception as e:
            logger.exception(f"Failed to read message: {e}")
            raise ProtocolError(f"Failed to read message: {e}") from e
