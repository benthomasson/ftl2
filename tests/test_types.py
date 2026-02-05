"""Tests for FTL2 type definitions."""

import tempfile
from getpass import getuser
from pathlib import Path

from ftl2.types import ExecutionConfig, GateConfig, HostConfig, ModuleResult


class TestHostConfig:
    """Tests for HostConfig dataclass."""

    def test_minimal_host_config(self):
        """Test creating host config with minimal required fields."""
        host = HostConfig(name="web01", ansible_host="192.168.1.10")

        assert host.name == "web01"
        assert host.ansible_host == "192.168.1.10"
        assert host.ansible_port == 22
        assert host.ansible_user == getuser()
        assert host.ansible_connection == "ssh"
        assert host.ansible_python_interpreter == "python3"
        assert host.vars == {}

    def test_full_host_config(self):
        """Test creating host config with all fields specified."""
        host = HostConfig(
            name="db01",
            ansible_host="192.168.1.20",
            ansible_port=2222,
            ansible_user="admin",
            ansible_connection="ssh",
            ansible_python_interpreter="/usr/bin/python3.11",
            vars={"role": "database", "tier": "production"},
        )

        assert host.name == "db01"
        assert host.ansible_host == "192.168.1.20"
        assert host.ansible_port == 2222
        assert host.ansible_user == "admin"
        assert host.ansible_connection == "ssh"
        assert host.ansible_python_interpreter == "/usr/bin/python3.11"
        assert host.vars == {"role": "database", "tier": "production"}

    def test_is_local_property(self):
        """Test is_local property for local and remote hosts."""
        local_host = HostConfig(
            name="localhost",
            ansible_host="127.0.0.1",
            ansible_connection="local",
        )
        remote_host = HostConfig(name="web01", ansible_host="192.168.1.10")

        assert local_host.is_local is True
        assert local_host.is_remote is False
        assert remote_host.is_local is False
        assert remote_host.is_remote is True

    def test_get_var(self):
        """Test getting host variables."""
        host = HostConfig(
            name="web01",
            ansible_host="192.168.1.10",
            vars={"role": "webserver", "env": "prod"},
        )

        assert host.get_var("role") == "webserver"
        assert host.get_var("env") == "prod"
        assert host.get_var("missing") is None
        assert host.get_var("missing", "default") == "default"

    def test_set_var(self):
        """Test setting host variables."""
        host = HostConfig(name="web01", ansible_host="192.168.1.10")

        assert host.vars == {}

        host.set_var("role", "webserver")
        assert host.get_var("role") == "webserver"

        host.set_var("port", 8080)
        assert host.get_var("port") == 8080


class TestExecutionConfig:
    """Tests for ExecutionConfig dataclass."""

    def test_minimal_execution_config(self):
        """Test creating execution config with minimal fields."""
        config = ExecutionConfig(module_name="ping")

        assert config.module_name == "ping"
        assert config.module_dirs == []
        assert config.module_args == {}
        assert config.modules == ["ping"]  # Auto-added
        assert config.dependencies == []

    def test_full_execution_config(self):
        """Test creating execution config with all fields."""
        config = ExecutionConfig(
            module_name="setup",
            module_dirs=[Path("/usr/lib/ftl/modules"), Path("/opt/modules")],
            module_args={"gather_subset": "all"},
            modules=["setup", "ping"],
            dependencies=["requests", "pyyaml"],
        )

        assert config.module_name == "setup"
        assert len(config.module_dirs) == 2
        assert all(isinstance(d, Path) for d in config.module_dirs)
        assert config.module_args == {"gather_subset": "all"}
        assert "setup" in config.modules
        assert "ping" in config.modules
        assert config.dependencies == ["requests", "pyyaml"]

    def test_module_name_added_to_modules(self):
        """Test that module_name is automatically added to modules list."""
        config = ExecutionConfig(module_name="command", modules=["ping", "shell"])

        assert "command" in config.modules
        assert "ping" in config.modules
        assert "shell" in config.modules

    def test_string_paths_converted_to_path(self):
        """Test that string paths are converted to Path objects."""
        config = ExecutionConfig(
            module_name="ping", module_dirs=["/usr/lib/modules", Path("/opt/modules")]
        )

        assert all(isinstance(d, Path) for d in config.module_dirs)
        assert config.module_dirs[0] == Path("/usr/lib/modules")
        assert config.module_dirs[1] == Path("/opt/modules")


class TestGateConfig:
    """Tests for GateConfig dataclass."""

    def test_default_gate_config(self):
        """Test creating gate config with defaults."""
        config = GateConfig()

        assert config.interpreter == "python3"
        assert config.local_interpreter == "python3"
        assert config.cache_dir == Path.home() / ".ftl2" / "gates"
        assert config.use_cache is True

    def test_custom_gate_config(self):
        """Test creating gate config with custom values."""
        cache_dir = Path("/tmp/ftl2/gates")
        config = GateConfig(
            interpreter="/usr/bin/python3.11",
            local_interpreter="/usr/local/bin/python3.13",
            cache_dir=cache_dir,
            use_cache=False,
        )

        assert config.interpreter == "/usr/bin/python3.11"
        assert config.local_interpreter == "/usr/local/bin/python3.13"
        assert config.cache_dir == cache_dir
        assert config.use_cache is False

    def test_cache_directory_created(self):
        """Test that cache directory is created when use_cache is True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "test_cache"
            assert not cache_dir.exists()

            config = GateConfig(cache_dir=cache_dir, use_cache=True)

            assert cache_dir.exists()
            assert config.cache_dir == cache_dir

    def test_cache_directory_not_created_when_disabled(self):
        """Test that cache directory is not created when use_cache is False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "test_cache"
            assert not cache_dir.exists()

            GateConfig(cache_dir=cache_dir, use_cache=False)

            assert not cache_dir.exists()

    def test_string_cache_dir_converted_to_path(self):
        """Test that string cache_dir is converted to Path."""
        config = GateConfig(cache_dir="/tmp/ftl2/cache")

        assert isinstance(config.cache_dir, Path)
        assert config.cache_dir == Path("/tmp/ftl2/cache")


class TestModuleResult:
    """Tests for ModuleResult dataclass."""

    def test_success_result_factory(self):
        """Test creating success result with factory method."""
        result = ModuleResult.success_result(
            host_name="web01", output={"ping": "pong"}, changed=False
        )

        assert result.host_name == "web01"
        assert result.success is True
        assert result.changed is False
        assert result.output == {"ping": "pong"}
        assert result.error is None
        assert result.is_success is True
        assert result.is_failure is False

    def test_error_result_factory(self):
        """Test creating error result with factory method."""
        result = ModuleResult.error_result(
            host_name="web01", error="Connection refused"
        )

        assert result.host_name == "web01"
        assert result.success is False
        assert result.changed is False
        assert result.output == {"error": True, "msg": "Connection refused"}
        assert result.error == "Connection refused"
        assert result.is_success is False
        assert result.is_failure is True

    def test_manual_result_creation(self):
        """Test creating result manually."""
        result = ModuleResult(
            host_name="db01",
            success=True,
            changed=True,
            output={"installed": ["nginx", "postgresql"]},
        )

        assert result.host_name == "db01"
        assert result.success is True
        assert result.changed is True
        assert result.output == {"installed": ["nginx", "postgresql"]}
        assert result.is_success is True
