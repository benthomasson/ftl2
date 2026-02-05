"""Test CLI functionality."""

from click.testing import CliRunner

from ftl2 import __version__
from ftl2.cli import cli, parse_module_args


def test_cli_version():
    """Test CLI version output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help():
    """Test CLI help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "FTL2" in result.output
    assert "run" in result.output
    assert "inventory" in result.output


def test_cli_run_help():
    """Test CLI run command help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--module" in result.output
    assert "--inventory" in result.output


def test_cli_missing_module():
    """Test CLI error when module not specified."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "-i", "inventory.yml"])
    assert result.exit_code != 0
    # Click automatically adds error message for required option


def test_cli_missing_inventory():
    """Test CLI error when inventory not specified."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "-m", "ping"])
    assert result.exit_code != 0
    # Click automatically adds error message for required option


def test_parse_module_args_empty():
    """Test parsing empty module args."""
    assert parse_module_args("") == {}
    assert parse_module_args(None) == {}


def test_parse_module_args_single():
    """Test parsing single module arg."""
    result = parse_module_args("host=localhost")
    assert result == {"host": "localhost"}


def test_parse_module_args_multiple():
    """Test parsing multiple module args."""
    result = parse_module_args("host=web01 port=80 debug=true")
    assert result == {"host": "web01", "port": "80", "debug": "true"}


def test_parse_module_args_paths():
    """Test parsing args with file paths."""
    result = parse_module_args("path=/tmp/test state=touch mode=0644")
    assert result == {"path": "/tmp/test", "state": "touch", "mode": "0644"}


def test_parse_module_args_quoted_values():
    """Test parsing args with quoted values."""
    result = parse_module_args("cmd='echo hello world' path=/tmp/file")
    assert result == {"cmd": "echo hello world", "path": "/tmp/file"}


class TestOutputFormatters:
    """Tests for output formatting functions."""

    def test_format_results_json(self):
        """Test JSON output formatting."""
        import json
        from ftl2.cli import format_results_json
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=True,
                    changed=False,
                    output={"ping": "pong"},
                ),
                "web02": ModuleResult(
                    host_name="web02",
                    success=False,
                    changed=False,
                    output={},
                    error="Connection timeout",
                ),
            }
        )

        output = format_results_json(results, "ping", 1.234)
        parsed = json.loads(output)

        assert parsed["module"] == "ping"
        assert parsed["total_hosts"] == 2
        assert parsed["successful"] == 1
        assert parsed["failed"] == 1
        assert parsed["duration"] == 1.234
        assert "timestamp" in parsed
        assert parsed["results"]["web01"]["success"] is True
        assert parsed["results"]["web01"]["output"]["ping"] == "pong"
        assert parsed["results"]["web02"]["success"] is False
        assert parsed["results"]["web02"]["error"] == "Connection timeout"

    def test_format_results_text(self):
        """Test text output formatting."""
        from ftl2.cli import format_results_text
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=True,
                    changed=False,
                    output={"ping": "pong"},
                ),
            }
        )

        output = format_results_text(results, verbose=False)

        assert "Execution Results:" in output
        assert "Total hosts: 1" in output
        assert "Successful: 1" in output
        assert "Failed: 0" in output

    def test_format_results_text_verbose(self):
        """Test verbose text output formatting."""
        from ftl2.cli import format_results_text
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=True,
                    changed=True,
                    output={"ping": "pong"},
                ),
                "web02": ModuleResult(
                    host_name="web02",
                    success=False,
                    changed=False,
                    output={},
                    error="Connection failed",
                ),
            }
        )

        output = format_results_text(results, verbose=True)

        assert "Detailed Results:" in output
        assert "web01: OK (changed)" in output
        assert "web02: FAILED" in output
        assert "Error: Connection failed" in output


class TestValidateExecutionRequirements:
    """Tests for validate_execution_requirements function."""

    def test_validate_module_not_found(self):
        """Test validation fails when module not found."""
        import pytest
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_localhost

        inventory = load_localhost()
        module_dirs = [Path(tempfile.mkdtemp())]

        with pytest.raises(ValueError, match="Module 'nonexistent' not found"):
            validate_execution_requirements(inventory, "nonexistent", module_dirs)

    def test_validate_module_found(self):
        """Test validation passes when module exists."""
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_localhost

        inventory = load_localhost()

        # Create a temporary module directory with a test module
        module_dir = Path(tempfile.mkdtemp())
        (module_dir / "test_module.py").write_text("# test module")

        try:
            # Should not raise
            validate_execution_requirements(inventory, "test_module", [module_dir])
        finally:
            (module_dir / "test_module.py").unlink()
            module_dir.rmdir()

    def test_validate_ssh_no_auth_configured(self):
        """Test validation fails when SSH host has no authentication."""
        import pytest
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_inventory

        # Create inventory with SSH host but no auth
        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        module_dir = Path(tempfile.mkdtemp())
        (module_dir / "ping.py").write_text("# ping module")

        try:
            inventory = load_inventory(inv_path)

            with pytest.raises(ValueError, match="No SSH authentication configured"):
                validate_execution_requirements(inventory, "ping", [module_dir])
        finally:
            inv_path.unlink()
            (module_dir / "ping.py").unlink()
            module_dir.rmdir()

    def test_validate_ssh_key_not_found(self):
        """Test validation fails when SSH key file doesn't exist."""
        import pytest
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_inventory

        # Create inventory with SSH host and non-existent key
        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
      ssh_private_key_file: /tmp/nonexistent_key_12345.pem
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        module_dir = Path(tempfile.mkdtemp())
        (module_dir / "ping.py").write_text("# ping module")

        try:
            inventory = load_inventory(inv_path)

            with pytest.raises(ValueError, match="SSH key not found"):
                validate_execution_requirements(inventory, "ping", [module_dir])
        finally:
            inv_path.unlink()
            (module_dir / "ping.py").unlink()
            module_dir.rmdir()

    def test_validate_ssh_key_exists(self):
        """Test validation passes when SSH key file exists."""
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_inventory

        # Create a temporary SSH key file
        key_file = Path(tempfile.mktemp(suffix=".pem"))
        key_file.write_text("fake ssh key")

        # Create inventory with SSH host and existing key
        yaml_content = f"""
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
      ssh_private_key_file: {key_file}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        module_dir = Path(tempfile.mkdtemp())
        (module_dir / "ping.py").write_text("# ping module")

        try:
            inventory = load_inventory(inv_path)

            # Should not raise
            validate_execution_requirements(inventory, "ping", [module_dir])
        finally:
            inv_path.unlink()
            key_file.unlink()
            (module_dir / "ping.py").unlink()
            module_dir.rmdir()

    def test_validate_ssh_password_auth(self):
        """Test validation passes when SSH password is configured."""
        import tempfile
        from pathlib import Path

        from ftl2.cli import validate_execution_requirements
        from ftl2.inventory import load_inventory

        # Create inventory with SSH host and password
        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
      ansible_password: secret123
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        module_dir = Path(tempfile.mkdtemp())
        (module_dir / "ping.py").write_text("# ping module")

        try:
            inventory = load_inventory(inv_path)

            # Should not raise
            validate_execution_requirements(inventory, "ping", [module_dir])
        finally:
            inv_path.unlink()
            (module_dir / "ping.py").unlink()
            module_dir.rmdir()


class TestTestSsh:
    """Tests for ftl2 test-ssh command."""

    def test_test_ssh_no_ssh_hosts(self):
        """Test test-ssh with inventory containing only local hosts."""
        import tempfile
        from pathlib import Path

        yaml_content = """
all:
  hosts:
    localhost:
      ansible_host: 127.0.0.1
      ansible_connection: local
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["test-ssh", "-i", str(inv_path)])
            assert result.exit_code == 0
            assert "No SSH hosts found" in result.output
        finally:
            inv_path.unlink()

    def test_test_ssh_help(self):
        """Test test-ssh help output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["test-ssh", "--help"])
        assert result.exit_code == 0
        assert "--inventory" in result.output
        assert "--timeout" in result.output


class TestInventoryValidate:
    """Tests for ftl2 inventory validate command."""

    def test_inventory_validate_success(self):
        """Test inventory validate with valid inventory."""
        import tempfile
        from pathlib import Path

        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: local
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["inventory", "validate", "-i", str(inv_path)])
            assert result.exit_code == 0
            assert "1 host(s)" in result.output
            assert "1 group(s)" in result.output
            assert "web01" in result.output
            assert "All checks passed" in result.output
        finally:
            inv_path.unlink()

    def test_inventory_validate_empty(self):
        """Test inventory validate with empty inventory."""
        import tempfile
        from pathlib import Path

        yaml_content = ""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["inventory", "validate", "-i", str(inv_path)])
            assert result.exit_code != 0
            assert "No hosts loaded" in result.output
        finally:
            inv_path.unlink()

    def test_inventory_validate_ssh_no_auth(self):
        """Test inventory validate catches missing SSH auth."""
        import tempfile
        from pathlib import Path

        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["inventory", "validate", "-i", str(inv_path)])
            assert result.exit_code != 0
            assert "No SSH authentication configured" in result.output
        finally:
            inv_path.unlink()

    def test_inventory_validate_check_ssh_missing_key(self):
        """Test inventory validate --check-ssh catches missing key file."""
        import tempfile
        from pathlib import Path

        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_connection: ssh
      ssh_private_key_file: /tmp/nonexistent_key_12345.pem
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["inventory", "validate", "-i", str(inv_path), "--check-ssh"])
            assert result.exit_code != 0
            assert "SSH key not found" in result.output
        finally:
            inv_path.unlink()


class TestDryRun:
    """Tests for dry-run mode."""

    def test_dry_run_help(self):
        """Test --dry-run option appears in help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_format_dry_run_text(self):
        """Test dry-run text output formatting."""
        from ftl2.cli import format_dry_run_text
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "localhost": ModuleResult(
                    host_name="localhost",
                    success=True,
                    changed=False,
                    output={
                        "dry_run": True,
                        "would_execute": True,
                        "module": "file",
                        "connection": "local",
                        "args": {"path": "/tmp/test", "state": "touch"},
                        "preview": "Would create file: /tmp/test",
                    },
                ),
            }
        )

        output = format_dry_run_text(results, "file")

        assert "Dry Run Preview:" in output
        assert "Module: file" in output
        assert "localhost (local):" in output
        assert "Would create file: /tmp/test" in output
        assert "No changes made (dry-run mode)" in output

    def test_format_dry_run_json(self):
        """Test dry-run JSON output formatting."""
        import json
        from ftl2.cli import format_dry_run_json
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=True,
                    changed=False,
                    output={
                        "dry_run": True,
                        "would_execute": True,
                        "module": "ping",
                        "connection": "ssh",
                        "ssh_host": "192.168.1.10",
                        "ssh_port": 22,
                        "ssh_user": "admin",
                        "args": {},
                        "preview": "Would test connectivity (response: pong)",
                    },
                ),
            }
        )

        output = format_dry_run_json(results, "ping")
        parsed = json.loads(output)

        assert parsed["dry_run"] is True
        assert parsed["module"] == "ping"
        assert parsed["total_hosts"] == 1
        assert "timestamp" in parsed
        assert parsed["hosts"]["web01"]["would_execute"] is True
        assert parsed["hosts"]["web01"]["connection"] == "ssh"
        assert parsed["hosts"]["web01"]["ssh_host"] == "192.168.1.10"
        assert parsed["hosts"]["web01"]["preview"] == "Would test connectivity (response: pong)"

    def test_format_dry_run_text_ssh(self):
        """Test dry-run text output for SSH hosts."""
        from ftl2.cli import format_dry_run_text
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=True,
                    changed=False,
                    output={
                        "dry_run": True,
                        "would_execute": True,
                        "module": "shell",
                        "connection": "ssh",
                        "ssh_host": "192.168.1.10",
                        "ssh_port": 2222,
                        "ssh_user": "deploy",
                        "args": {"cmd": "uptime"},
                        "preview": "Would execute: uptime",
                    },
                ),
            }
        )

        output = format_dry_run_text(results, "shell")

        assert "web01 (deploy@192.168.1.10:2222):" in output
        assert "Would execute: uptime" in output
        assert "Args: cmd=uptime" in output
