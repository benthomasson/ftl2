"""Tests for FTL gate runtime.

The gate runtime is designed to run as a subprocess communicating via
stdin/stdout, making traditional unit testing difficult. These tests
verify basic functionality without requiring actual subprocess execution.
"""


import pytest

# Import gate runtime components
from ftl2.ftl_gate.__main__ import (
    ModuleNotFoundError,
    StdinReader,
    StdoutWriter,
    execute_module_stub,
)


class TestGateRuntimeClasses:
    """Tests for gate runtime utility classes."""

    def test_module_not_found_error(self):
        """Test ModuleNotFoundError exception."""
        error = ModuleNotFoundError("test_module")
        assert str(error) == "test_module"
        assert isinstance(error, Exception)

    def test_stdin_reader_creation(self):
        """Test StdinReader can be created."""
        reader = StdinReader()
        assert reader is not None
        assert hasattr(reader, "read")

    def test_stdout_writer_creation(self):
        """Test StdoutWriter can be created."""
        writer = StdoutWriter()
        assert writer is not None
        assert hasattr(writer, "write")
        assert hasattr(writer, "drain")

    @pytest.mark.asyncio
    async def test_stdout_writer_drain(self):
        """Test StdoutWriter drain is async."""
        writer = StdoutWriter()
        # drain() should not raise
        await writer.drain()


class TestModuleExecution:
    """Tests for module execution stub."""

    @pytest.mark.asyncio
    async def test_execute_module_stub(self):
        """Test module execution stub returns expected format."""
        result = await execute_module_stub(
            module_name="test_module",
            module=None,
            module_args={"key": "value"},
        )

        assert isinstance(result, dict)
        assert "stdout" in result
        assert "stderr" in result
        assert "rc" in result
        assert "changed" in result

    @pytest.mark.asyncio
    async def test_execute_module_stub_result_values(self):
        """Test module execution stub returns expected values."""
        result = await execute_module_stub(
            module_name="ping",
            module=None,
            module_args={},
        )

        assert result["rc"] == 0
        assert result["changed"] is False
        assert isinstance(result["stdout"], str)
        assert isinstance(result["stderr"], str)

    @pytest.mark.asyncio
    async def test_execute_module_stub_with_args(self):
        """Test module execution stub accepts various arguments."""
        result = await execute_module_stub(
            module_name="setup",
            module=None,
            module_args={"host": "localhost", "port": 8080},
        )

        assert result["rc"] == 0
        assert "setup" in result["stdout"]


# Note: Full gate runtime testing would require:
# - Subprocess execution
# - Mock stdin/stdout pipes
# - Message protocol integration testing
# - End-to-end communication tests
#
# These are better suited for integration tests with actual
# subprocess execution rather than unit tests.
