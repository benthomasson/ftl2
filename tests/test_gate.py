"""Tests for gate building system."""

import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from ftl2.exceptions import GateError, ModuleNotFound
from ftl2.gate import GateBuildConfig, GateBuilder


class TestGateBuildConfig:
    """Tests for GateBuildConfig dataclass."""

    def test_minimal_config(self):
        """Test creating config with minimal parameters."""
        config = GateBuildConfig()

        assert config.modules == []
        assert config.module_dirs == []
        assert config.dependencies == []
        assert config.interpreter == sys.executable
        assert config.local_interpreter == sys.executable

    def test_config_with_modules(self):
        """Test creating config with modules."""
        config = GateBuildConfig(modules=["ping", "setup"], module_dirs=[Path("/opt/modules")])

        assert config.modules == ["ping", "setup"]
        assert config.module_dirs == [Path("/opt/modules")]

    def test_config_with_dependencies(self):
        """Test creating config with dependencies."""
        config = GateBuildConfig(dependencies=["requests>=2.0", "pyyaml"])

        assert config.dependencies == ["requests>=2.0", "pyyaml"]

    def test_config_path_conversion(self):
        """Test that string paths are converted to Path objects."""
        config = GateBuildConfig(module_dirs=["/opt/modules", "/tmp/modules"])

        assert all(isinstance(d, Path) for d in config.module_dirs)
        assert config.module_dirs == [Path("/opt/modules"), Path("/tmp/modules")]

    def test_compute_hash_empty_config(self):
        """Test hash computation for empty configuration."""
        config = GateBuildConfig()
        hash1 = config.compute_hash()

        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA256 hex digest length

    def test_compute_hash_deterministic(self):
        """Test that hash computation is deterministic."""
        config1 = GateBuildConfig(modules=["ping"], dependencies=["requests"])
        config2 = GateBuildConfig(modules=["ping"], dependencies=["requests"])

        assert config1.compute_hash() == config2.compute_hash()

    def test_compute_hash_different_configs(self):
        """Test that different configs produce different hashes."""
        config1 = GateBuildConfig(modules=["ping"])
        config2 = GateBuildConfig(modules=["setup"])

        assert config1.compute_hash() != config2.compute_hash()

    def test_compute_hash_includes_all_fields(self):
        """Test that hash includes all configuration fields."""
        base_config = GateBuildConfig()
        base_hash = base_config.compute_hash()

        # Changing any field should change hash
        config_with_module = GateBuildConfig(modules=["ping"])
        assert config_with_module.compute_hash() != base_hash

        config_with_dir = GateBuildConfig(module_dirs=[Path("/opt")])
        assert config_with_dir.compute_hash() != base_hash

        config_with_dep = GateBuildConfig(dependencies=["requests"])
        assert config_with_dep.compute_hash() != base_hash

        config_with_interp = GateBuildConfig(interpreter="/usr/bin/python3")
        assert config_with_interp.compute_hash() != base_hash


class TestGateBuilder:
    """Tests for GateBuilder class."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def temp_module_dir(self):
        """Create temporary module directory with test module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)

            # Create a simple test module
            test_module = module_dir / "test_module.py"
            test_module.write_text('#!/usr/bin/env python3\nprint("test module")\n')

            yield module_dir

    def test_create_builder(self, temp_cache_dir):
        """Test creating a gate builder."""
        builder = GateBuilder(cache_dir=temp_cache_dir)

        # Use resolve() to handle symlinks (e.g., /var vs /private/var on macOS)
        assert builder.cache_dir.resolve() == temp_cache_dir.resolve()
        assert builder.cache_dir.exists()

    def test_build_minimal_gate(self, temp_cache_dir):
        """Test building a gate with no modules or dependencies."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()

        gate_path, gate_hash = builder.build(config)

        assert Path(gate_path).exists()
        assert gate_path.endswith(".pyz")
        assert len(gate_hash) == 64

    def test_build_gate_caching(self, temp_cache_dir):
        """Test that identical configs reuse cached gates."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig()

        # Build first time
        gate_path1, gate_hash1 = builder.build(config)

        # Build again with same config
        gate_path2, gate_hash2 = builder.build(config)

        assert gate_path1 == gate_path2
        assert gate_hash1 == gate_hash2

    def test_build_gate_with_module(self, temp_cache_dir, temp_module_dir):
        """Test building a gate with a module."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig(modules=["test_module"], module_dirs=[temp_module_dir])

        gate_path, gate_hash = builder.build(config)

        assert Path(gate_path).exists()

        # Verify gate is a valid zip file
        with zipfile.ZipFile(gate_path, "r") as zf:
            namelist = zf.namelist()
            # Should contain __main__.py and the module
            assert "__main__.py" in namelist
            assert any("test_module.py" in name for name in namelist)

    def test_build_gate_module_not_found(self, temp_cache_dir):
        """Test that missing modules are skipped during gate build."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig(modules=["nonexistent"], module_dirs=[Path("/tmp/nonexistent")])

        # Missing modules are silently skipped — gate still builds
        gate_path, gate_hash = builder.build(config)
        assert Path(gate_path).exists()

    def test_build_gate_different_interpreters(self, temp_cache_dir):
        """Test that different interpreters produce different gates."""
        builder = GateBuilder(cache_dir=temp_cache_dir)

        config1 = GateBuildConfig(interpreter="/usr/bin/python3")
        config2 = GateBuildConfig(interpreter="/opt/python3/bin/python3")

        gate_path1, gate_hash1 = builder.build(config1)
        gate_path2, gate_hash2 = builder.build(config2)

        assert gate_hash1 != gate_hash2
        assert gate_path1 != gate_path2

    def test_gate_structure(self, temp_cache_dir, temp_module_dir):
        """Test that built gate has correct internal structure."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig(modules=["test_module"], module_dirs=[temp_module_dir])

        gate_path, _ = builder.build(config)

        # Verify gate structure
        with zipfile.ZipFile(gate_path, "r") as zf:
            namelist = zf.namelist()

            # Must have __main__.py entry point
            assert "__main__.py" in namelist

            # Must have ftl_gate package
            assert "ftl_gate/__init__.py" in namelist

            # Must have the test module
            assert any("test_module.py" in name for name in namelist)

    def test_gate_hash_consistency(self, temp_cache_dir, temp_module_dir):
        """Test that gate hash matches config hash."""
        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig(modules=["test_module"], module_dirs=[temp_module_dir])

        gate_path, gate_hash = builder.build(config)

        # Hash from builder should match hash from config
        assert gate_hash == config.compute_hash()

        # Gate filename should contain the hash
        assert gate_hash in gate_path

    def test_multiple_modules(self, temp_cache_dir, temp_module_dir):
        """Test building a gate with multiple modules."""
        # Create additional module
        module2 = temp_module_dir / "module2.py"
        module2.write_text('print("module 2")\n')

        builder = GateBuilder(cache_dir=temp_cache_dir)
        config = GateBuildConfig(modules=["test_module", "module2"], module_dirs=[temp_module_dir])

        gate_path, _ = builder.build(config)

        # Verify both modules are in gate
        with zipfile.ZipFile(gate_path, "r") as zf:
            namelist = zf.namelist()
            assert any("test_module.py" in name for name in namelist)
            assert any("module2.py" in name for name in namelist)


# =============================================================================
# Gate Runtime Tests (module execution in ftl_gate)
# =============================================================================

import base64


class TestModuleTypeDetection:
    """Tests for module type detection functions."""

    def test_is_binary_module_true(self):
        """Binary modules contain non-UTF8 bytes."""
        from ftl2.ftl_gate.__main__ import is_binary_module

        # Invalid UTF-8 sequence (continuation byte without start)
        binary_content = b"\x80\x81\x82\x83"
        assert is_binary_module(binary_content) is True

    def test_is_binary_module_false(self):
        """Text modules decode as UTF-8."""
        from ftl2.ftl_gate.__main__ import is_binary_module

        text_content = b"#!/usr/bin/python3\nprint('hello')"
        assert is_binary_module(text_content) is False

    def test_is_new_style_module_true(self):
        """New-style modules contain AnsibleModule(."""
        from ftl2.ftl_gate.__main__ import is_new_style_module

        module_content = b"""
from ansible.module_utils.basic import AnsibleModule

def main():
    module = AnsibleModule(argument_spec={})
    module.exit_json(changed=False)
"""
        assert is_new_style_module(module_content) is True

    def test_is_new_style_module_false(self):
        """Old-style modules don't contain AnsibleModule(."""
        from ftl2.ftl_gate.__main__ import is_new_style_module

        module_content = b"#!/usr/bin/python3\nprint('hello')"
        assert is_new_style_module(module_content) is False

    def test_is_want_json_module_true(self):
        """WANT_JSON modules contain the marker."""
        from ftl2.ftl_gate.__main__ import is_want_json_module

        module_content = b"""#!/usr/bin/python3
# WANT_JSON
import json, sys
with open(sys.argv[1]) as f:
    args = json.load(f)
"""
        assert is_want_json_module(module_content) is True

    def test_is_want_json_module_false(self):
        """Non-WANT_JSON modules don't have the marker."""
        from ftl2.ftl_gate.__main__ import is_want_json_module

        module_content = b"#!/usr/bin/python3\nprint('hello')"
        assert is_want_json_module(module_content) is False


class TestCheckOutput:
    """Tests for async command execution."""

    @pytest.mark.asyncio
    async def test_check_output_simple(self):
        """check_output runs simple commands."""
        from ftl2.ftl_gate.__main__ import check_output

        stdout, stderr = await check_output("echo hello")
        assert stdout.strip() == b"hello"

    @pytest.mark.asyncio
    async def test_check_output_with_stdin(self):
        """check_output can send stdin data."""
        from ftl2.ftl_gate.__main__ import check_output

        stdout, stderr = await check_output("cat", stdin=b"test input")
        assert stdout == b"test input"

    @pytest.mark.asyncio
    async def test_check_output_captures_stderr(self):
        """check_output captures stderr."""
        from ftl2.ftl_gate.__main__ import check_output

        stdout, stderr = await check_output("echo error >&2")
        assert b"error" in stderr


class TestGetPythonPath:
    """Tests for Python path helper."""

    def test_get_python_path_returns_string(self):
        """get_python_path returns a path-separated string."""
        from ftl2.ftl_gate.__main__ import get_python_path
        import os

        path = get_python_path()
        assert isinstance(path, str)
        assert os.pathsep in path or len(path) > 0


class TestExecuteFTLModule:
    """Tests for FTL native module execution."""

    @pytest.mark.asyncio
    async def test_execute_ftl_module_async_main(self):
        """execute_ftl_module can run async main()."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol
        from unittest.mock import AsyncMock, MagicMock

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
async def main():
    return {"changed": True, "msg": "success"}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "test_module", module_b64, {})

        protocol.send_message.assert_called_once()
        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][2]["result"]["changed"] is True

    @pytest.mark.asyncio
    async def test_execute_ftl_module_sync_main(self):
        """execute_ftl_module can run sync main()."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol
        from unittest.mock import AsyncMock, MagicMock

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
def main():
    return {"changed": False, "value": 42}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "sync_module", module_b64, {})

        protocol.send_message.assert_called_once()
        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "FTLModuleResult"
        assert call_args[0][2]["result"]["value"] == 42

    @pytest.mark.asyncio
    async def test_execute_ftl_module_with_args(self):
        """execute_ftl_module passes args to main()."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol
        from unittest.mock import AsyncMock, MagicMock

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
async def main(args):
    return {"received": args.get("name")}
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(
            protocol, writer, "args_module", module_b64, {"name": "test"}
        )

        call_args = protocol.send_message.call_args
        assert call_args[0][2]["result"]["received"] == "test"

    @pytest.mark.asyncio
    async def test_execute_ftl_module_error(self):
        """execute_ftl_module sends error on exception."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol
        from unittest.mock import AsyncMock, MagicMock

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
async def main():
    raise ValueError("intentional error")
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "error_module", module_b64, {})

        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "Error"
        assert "intentional error" in call_args[0][2]["message"]

    @pytest.mark.asyncio
    async def test_execute_ftl_module_no_main(self):
        """execute_ftl_module errors if no main()."""
        from ftl2.ftl_gate.__main__ import execute_ftl_module
        from ftl2.message import GateProtocol
        from unittest.mock import AsyncMock, MagicMock

        protocol = GateProtocol()
        protocol.send_message = AsyncMock()
        writer = MagicMock()

        module_source = b"""
def helper():
    pass
"""
        module_b64 = base64.b64encode(module_source).decode()

        await execute_ftl_module(protocol, writer, "no_main_module", module_b64, {})

        call_args = protocol.send_message.call_args
        assert call_args[0][1] == "Error"
        assert "no main()" in call_args[0][2]["message"]
