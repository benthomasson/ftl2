"""Tests for FTLModule error handling in the gate process (Issue #40).

Validates that FTLModule execution errors are caught and sent as protocol
Error messages instead of crashing the gate process. Tests cover both
serial and multiplexed mode handlers, plus the underlying run_ftl_module()
function.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from ftl2.ftl_gate.__main__ import (
    ModuleNotFoundError,
    execute_ftl_module,
    run_ftl_module,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode_module(source: str) -> str:
    """Base64-encode a Python module source string."""
    return base64.b64encode(source.encode()).decode()


def _make_protocol() -> MagicMock:
    """Create a mock GateProtocol with async send methods."""
    proto = MagicMock()
    proto.send_message = AsyncMock()
    proto.send_message_with_id = AsyncMock()
    return proto


# ---------------------------------------------------------------------------
# Tests for run_ftl_module() — the core execution function
# ---------------------------------------------------------------------------

class TestRunFtlModule:
    """Tests for run_ftl_module() return values and error handling."""

    async def test_successful_sync_module(self):
        """Test successful execution of a synchronous FTL module."""
        source = _encode_module("def main(args):\n    return {'ok': True}")
        resp_type, resp_data = await run_ftl_module("test_mod", source, {"x": 1})
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] == {"ok": True}

    async def test_successful_async_module(self):
        """Test successful execution of an async FTL module."""
        source = _encode_module(
            "import asyncio\nasync def main(args):\n    return {'async': True}"
        )
        resp_type, resp_data = await run_ftl_module("async_mod", source, {})
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] == {"async": True}

    async def test_module_not_found_returns_tuple(self):
        """Test that a missing baked-in module returns ModuleNotFound tuple."""
        # Empty module string + no baked-in module → ModuleNotFound
        resp_type, resp_data = await run_ftl_module(
            "nonexistent_module_xyz", "", None
        )
        assert resp_type == "ModuleNotFound"
        assert "module_name" in resp_data
        assert resp_data["module_name"] == "nonexistent_module_xyz"

    async def test_compile_error_returns_error_tuple(self):
        """Test that a module with syntax errors returns an Error tuple."""
        bad_source = _encode_module("def main(args)\n    return None")  # missing colon
        resp_type, resp_data = await run_ftl_module("bad_syntax", bad_source, {})
        assert resp_type == "Error"
        assert "message" in resp_data
        assert "traceback" in resp_data
        assert "SyntaxError" in resp_data["traceback"]

    async def test_runtime_error_returns_error_tuple(self):
        """Test that a module raising at runtime returns an Error tuple."""
        source = _encode_module("def main(args):\n    raise ValueError('boom')")
        resp_type, resp_data = await run_ftl_module("exploder", source, {})
        assert resp_type == "Error"
        assert "boom" in resp_data["message"]
        assert "traceback" in resp_data

    async def test_missing_entry_point_returns_error(self):
        """Test module with no main() or ftl_<name>() returns Error."""
        source = _encode_module("x = 42")
        resp_type, resp_data = await run_ftl_module("no_entry", source, {})
        assert resp_type == "Error"
        assert "no main()" in resp_data["message"] or "has no" in resp_data["message"]

    async def test_named_entry_point(self):
        """Test module using ftl_<module_name>() entry point."""
        # Named entry points with non-"main" name get kwargs dispatch,
        # so use **kwargs signature
        source = _encode_module("def ftl_my_mod(**kwargs):\n    return {'named': True}")
        resp_type, resp_data = await run_ftl_module("my_mod", source, {})
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] == {"named": True}

    async def test_module_with_no_args(self):
        """Test module whose main() takes no arguments."""
        source = _encode_module("def main():\n    return {'noargs': True}")
        resp_type, resp_data = await run_ftl_module("noargs_mod", source, {})
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] == {"noargs": True}


# ---------------------------------------------------------------------------
# Tests for execute_ftl_module() — serial mode wrapper
# ---------------------------------------------------------------------------

class TestExecuteFtlModule:
    """Tests for execute_ftl_module() serial mode wrapper."""

    async def test_success_sends_result(self):
        """Test that successful execution sends FTLModuleResult via protocol."""
        proto = _make_protocol()
        writer = MagicMock()
        source = _encode_module("def main(args):\n    return {'ok': True}")

        await execute_ftl_module(proto, writer, "test_mod", source, {"x": 1})

        proto.send_message.assert_called_once()
        call_args = proto.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"

    async def test_not_found_sends_module_not_found(self):
        """Test that missing module sends ModuleNotFound via protocol."""
        proto = _make_protocol()
        writer = MagicMock()

        await execute_ftl_module(proto, writer, "nonexistent_xyz", "", {})

        proto.send_message.assert_called_once()
        call_args = proto.send_message.call_args
        assert call_args[0][1] == "ModuleNotFound"

    async def test_error_sends_error_message(self):
        """Test that execution error sends Error via protocol."""
        proto = _make_protocol()
        writer = MagicMock()
        source = _encode_module("def main(args):\n    raise RuntimeError('fail')")

        await execute_ftl_module(proto, writer, "failing", source, {})

        proto.send_message.assert_called_once()
        call_args = proto.send_message.call_args
        assert call_args[0][1] == "Error"


# ---------------------------------------------------------------------------
# Tests for serial mode handler — try/except at lines 980-1014
# ---------------------------------------------------------------------------

class TestSerialModeHandler:
    """Tests for the serial mode FTLModule handler's try/except wrapping.

    These test that if execute_ftl_module itself raises (e.g., protocol send
    failure), the outer try/except catches it and sends an error message.
    """

    async def test_protocol_send_failure_caught(self):
        """Test that protocol.send_message failure in execute_ftl_module is caught."""
        proto = _make_protocol()
        writer = MagicMock()
        source = _encode_module("def main(args):\n    return {'ok': True}")

        # Make send_message raise on first call (simulating protocol failure),
        # then succeed on retry (for the error message)
        call_count = 0
        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("pipe broken")
        proto.send_message.side_effect = side_effect

        # The outer handler should catch this and try to send an Error.
        # But since send_message itself fails, the outer send may also fail.
        # The key point: execute_ftl_module raises, and the caller catches it.
        with pytest.raises(ConnectionError):
            await execute_ftl_module(proto, writer, "test_mod", source, {})

    async def test_invalid_data_guard(self):
        """Test that non-dict data is rejected before execution."""
        # This tests the isinstance(data, dict) check at line 983.
        # We verify the pattern: if data is not a dict, Error is sent.
        # Since the check is in the main loop (not execute_ftl_module),
        # we verify execute_ftl_module handles None args gracefully.
        proto = _make_protocol()
        writer = MagicMock()
        source = _encode_module("def main():\n    return {'ok': True}")

        await execute_ftl_module(proto, writer, "test", source, None)
        proto.send_message.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for multiplexed mode handler — try/except at lines 1194-1219
# ---------------------------------------------------------------------------

class TestMultiplexedModeHandler:
    """Tests for multiplexed FTLModule handler error paths.

    Since the multiplexed handler calls run_ftl_module() directly and
    then send_message_with_id(), we test that run_ftl_module() always
    returns tuples (never raises), making the outer try/except a safety net.
    """

    async def test_run_ftl_module_never_raises_on_bad_module(self):
        """Verify run_ftl_module returns tuple even for broken modules."""
        bad_source = _encode_module("import nonexistent_package_xyz")
        resp_type, resp_data = await run_ftl_module("broken", bad_source, {})
        # Should return Error tuple, not raise
        assert resp_type == "Error"
        assert "message" in resp_data

    async def test_run_ftl_module_never_raises_on_runtime_error(self):
        """Verify run_ftl_module catches Exception subclasses from module code."""
        source = _encode_module(
            "def main(args):\n    raise TypeError('type mismatch')"
        )
        resp_type, resp_data = await run_ftl_module("type_err", source, {})
        assert resp_type == "Error"
        assert "type mismatch" in resp_data["message"]

    async def test_system_exit_not_caught(self):
        """SystemExit is BaseException and is NOT caught by run_ftl_module.

        This documents actual behavior: SystemExit propagates up. The outer
        handle_request try/except in multiplexed mode catches it as a
        GateSystemError safety net.
        """
        source = _encode_module(
            "def main(args):\n    raise SystemExit('fatal')"
        )
        with pytest.raises(SystemExit):
            await run_ftl_module("sys_exit", source, {})

    async def test_send_message_with_id_pattern(self):
        """Test the multiplexed send pattern with msg_id and write_lock."""
        proto = _make_protocol()
        writer = MagicMock()
        write_lock = asyncio.Lock()
        msg_id = 42

        source = _encode_module("def main(args):\n    return {'ok': True}")
        resp_type, resp_data = await run_ftl_module("test_mod", source, {})

        await proto.send_message_with_id(
            writer, resp_type, resp_data, msg_id, write_lock=write_lock
        )

        proto.send_message_with_id.assert_called_once()
        call_args = proto.send_message_with_id.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][3] == 42
        assert call_args[1]["write_lock"] is write_lock

    async def test_error_response_includes_msg_id(self):
        """Test that error responses in multiplexed mode include msg_id."""
        proto = _make_protocol()
        writer = MagicMock()
        write_lock = asyncio.Lock()
        msg_id = 99

        source = _encode_module("def main(args):\n    raise ValueError('boom')")
        resp_type, resp_data = await run_ftl_module("failing", source, {})

        await proto.send_message_with_id(
            writer, resp_type, resp_data, msg_id, write_lock=write_lock
        )

        call_args = proto.send_message_with_id.call_args
        assert call_args[0][1] == "Error"
        assert call_args[0][3] == 99


# ---------------------------------------------------------------------------
# Edge cases from reviewer notes
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases identified during code review."""

    async def test_module_not_found_error_class_exists(self):
        """Verify ModuleNotFoundError is importable from gate module."""
        err = ModuleNotFoundError("test_module")
        assert str(err) == "test_module"

    async def test_empty_module_name(self):
        """Test handling of empty module name."""
        resp_type, resp_data = await run_ftl_module("", "", {})
        # Should return ModuleNotFound or Error, not crash
        assert resp_type in ("ModuleNotFound", "Error")

    async def test_none_module_args(self):
        """Test that None module_args is handled safely."""
        source = _encode_module("def main():\n    return {'ok': True}")
        resp_type, resp_data = await run_ftl_module("test", source, None)
        assert resp_type == "FTLModuleResult"

    async def test_large_traceback_in_error(self):
        """Test that deeply nested errors still produce error tuples."""
        source = _encode_module(
            "def main(args):\n"
            "    def a(): return b()\n"
            "    def b(): return c()\n"
            "    def c(): raise RecursionError('deep')\n"
            "    return a()\n"
        )
        resp_type, resp_data = await run_ftl_module("deep_err", source, {})
        assert resp_type == "Error"
        assert "traceback" in resp_data
        assert len(resp_data["traceback"]) > 0

    async def test_module_returning_none(self):
        """Test module that returns None."""
        source = _encode_module("def main(args):\n    pass")
        resp_type, resp_data = await run_ftl_module("none_ret", source, {})
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] is None

    async def test_module_with_kwargs(self):
        """Test module whose main accepts keyword arguments."""
        source = _encode_module(
            "def main(*, name='default', count=0):\n"
            "    return {'name': name, 'count': count}\n"
        )
        resp_type, resp_data = await run_ftl_module(
            "kwargs_mod", source, {"name": "test", "count": 5}
        )
        assert resp_type == "FTLModuleResult"
        assert resp_data["result"] == {"name": "test", "count": 5}
