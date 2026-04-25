"""Tests for gate bundling of events.py (issue #120).

Validates that the gate builder includes events.py so FTL modules
like http.py can import ftl2.events on remote hosts.
"""

import base64
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ftl2.gate import GateBuildConfig, GateBuilder


class TestEventsInGateHash:
    """events.py changes must invalidate the gate cache."""

    def test_events_in_hash_source_files(self):
        """compute_hash() includes events.py in its source file list."""
        import ftl2

        ftl2_dir = Path(ftl2.__file__).parent
        events_path = ftl2_dir / "events.py"
        assert events_path.exists(), "events.py must exist in ftl2 package"

        config = GateBuildConfig()
        hash1 = config.compute_hash()
        assert isinstance(hash1, str)
        assert len(hash1) == 64


class TestEventsInGateBuild:
    """Gate must include events.py so remote FTL modules can import it."""

    @pytest.fixture
    def temp_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_gate_contains_events_py(self, temp_cache_dir):
        """Built gate .pyz contains ftl2/events.py."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()

        gate_path, _ = builder.build(config)

        with zipfile.ZipFile(gate_path, "r") as zf:
            namelist = zf.namelist()
            assert "ftl2/events.py" in namelist, (
                f"events.py not found in gate. Contents: {namelist}"
            )

    def test_gate_contains_ftl_modules_exceptions(self, temp_cache_dir):
        """Built gate .pyz also contains ftl2/ftl_modules/exceptions.py (regression guard)."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()

        gate_path, _ = builder.build(config)

        with zipfile.ZipFile(gate_path, "r") as zf:
            namelist = zf.namelist()
            assert "ftl2/ftl_modules/exceptions.py" in namelist

    def test_gate_events_content_matches_source(self, temp_cache_dir):
        """The events.py bundled in the gate matches the source file."""
        import ftl2

        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()
        gate_path, _ = builder.build(config)

        source_events = (Path(ftl2.__file__).parent / "events.py").read_bytes()

        with zipfile.ZipFile(gate_path, "r") as zf:
            gate_events = zf.read("ftl2/events.py")

        assert gate_events == source_events

    def test_gate_events_importable_content(self, temp_cache_dir):
        """The events.py in the gate contains emit_progress (the symbol http.py imports)."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()
        gate_path, _ = builder.build(config)

        with zipfile.ZipFile(gate_path, "r") as zf:
            events_content = zf.read("ftl2/events.py").decode()

        assert "def emit_progress" in events_content
        assert "class ProgressEvent" in events_content


class TestFTLModuleWithEventsImport:
    """FTL modules that import from ftl2.events should work in the gate."""

    @pytest.mark.asyncio
    async def test_module_importing_events_succeeds(self):
        """A module that imports emit_progress can execute in the gate runtime."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
from ftl2.events import emit_progress

async def main(args):
    emit_progress(50, "halfway")
    return {"changed": True, "msg": "events import works"}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "events_test", module_b64, {})

        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][2]["result"]["msg"] == "events import works"

    @pytest.mark.asyncio
    async def test_module_importing_exceptions_succeeds(self):
        """A module that imports FTLModuleError can execute in the gate runtime."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
from ftl2.ftl_modules.exceptions import FTLModuleError

async def main(args):
    return {"changed": False, "msg": "exceptions import works"}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "exc_test", module_b64, {})

        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][2]["result"]["msg"] == "exceptions import works"

    @pytest.mark.asyncio
    async def test_module_importing_both_events_and_exceptions(self):
        """A module importing both ftl2.events and ftl2.ftl_modules.exceptions works."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
from ftl2.events import emit_progress
from ftl2.ftl_modules.exceptions import FTLModuleError

async def main(args):
    emit_progress(0, "starting")
    if args.get("fail"):
        raise FTLModuleError("intentional")
    emit_progress(100, "done")
    return {"changed": True}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "both_test", module_b64, {})

        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][2]["result"]["changed"] is True


class TestCopyEventsModule:
    """Unit tests for _copy_events_module method."""

    @pytest.fixture
    def temp_cache_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_copy_events_creates_file(self, temp_cache_dir):
        """_copy_events_module copies events.py to the ftl2 directory."""
        builder = GateBuilder(cache_dir=temp_cache_dir)

        with tempfile.TemporaryDirectory() as tmpdir:
            ftl2_dir = Path(tmpdir) / "ftl2"
            ftl2_dir.mkdir()
            (ftl2_dir / "__init__.py").write_text("")

            builder._copy_events_module(ftl2_dir)

            assert (ftl2_dir / "events.py").exists()
            content = (ftl2_dir / "events.py").read_text()
            assert "emit_progress" in content

    def test_copy_events_soft_failure(self, temp_cache_dir):
        """_copy_events_module warns but doesn't raise if events.py is missing."""
        builder = GateBuilder(cache_dir=temp_cache_dir)

        with tempfile.TemporaryDirectory() as tmpdir:
            ftl2_dir = Path(tmpdir) / "ftl2"
            ftl2_dir.mkdir()
            # No crash expected even with unusual state
            builder._copy_events_module(ftl2_dir)
