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


class TestErrorContext:
    """Tests for rich error context."""

    def test_error_context_to_dict(self):
        """Test ErrorContext serialization to dictionary."""
        from ftl2.exceptions import ErrorContext, ErrorTypes

        context = ErrorContext(
            host="web01",
            host_address="192.168.1.10:22",
            user="admin",
            module="ping",
            error_type=ErrorTypes.CONNECTION_TIMEOUT,
            message="Connection timeout after 30s",
            attempt=3,
            max_attempts=3,
            suggestions=["Check network", "Verify firewall"],
            debug_command="ftl2 test-ssh -i hosts.yml",
        )

        result = context.to_dict()

        assert result["error_type"] == "ConnectionTimeout"
        assert result["message"] == "Connection timeout after 30s"
        assert result["host"] == "web01"
        assert result["host_address"] == "192.168.1.10:22"
        assert result["attempt"] == 3
        assert result["max_attempts"] == 3
        assert len(result["suggestions"]) == 2
        assert result["debug_command"] == "ftl2 test-ssh -i hosts.yml"

    def test_error_context_format_text(self):
        """Test ErrorContext formatting as text."""
        from ftl2.exceptions import ErrorContext, ErrorTypes

        context = ErrorContext(
            host="db01",
            host_address="192.168.1.20:22",
            user="postgres",
            module="setup",
            error_type=ErrorTypes.AUTHENTICATION_FAILED,
            message="SSH authentication failed",
            suggestions=["Check SSH key", "Verify credentials"],
        )

        output = context.format_text()

        assert "Error on host 'db01'" in output
        assert "Type: AuthenticationFailed" in output
        assert "Message: SSH authentication failed" in output
        assert "Host: 192.168.1.20:22" in output
        assert "User: postgres" in output
        assert "Suggested Actions:" in output
        assert "Check SSH key" in output

    def test_format_results_json_with_error_context(self):
        """Test JSON output includes error context."""
        import json
        from ftl2.cli import format_results_json
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult
        from ftl2.exceptions import ErrorContext, ErrorTypes

        error_ctx = ErrorContext(
            host="web01",
            host_address="192.168.1.10:22",
            user="admin",
            error_type=ErrorTypes.CONNECTION_TIMEOUT,
            message="Connection timeout after 30s",
            suggestions=["Check network connectivity"],
        )

        results = ExecutionResults(
            results={
                "web01": ModuleResult(
                    host_name="web01",
                    success=False,
                    changed=False,
                    output={},
                    error="Connection timeout",
                    error_context=error_ctx,
                ),
            }
        )

        output = format_results_json(results, "ping", 1.5)
        parsed = json.loads(output)

        assert parsed["failed"] == 1
        assert "errors" in parsed
        assert len(parsed["errors"]) == 1
        assert parsed["errors"][0]["error_type"] == "ConnectionTimeout"
        assert parsed["errors"][0]["host"] == "web01"
        assert "suggestions" in parsed["errors"][0]

    def test_format_results_text_with_error_context(self):
        """Test text output shows error context."""
        from ftl2.cli import format_results_text
        from ftl2.executor import ExecutionResults
        from ftl2.types import ModuleResult
        from ftl2.exceptions import ErrorContext, ErrorTypes

        error_ctx = ErrorContext(
            host="db01",
            host_address="192.168.1.20:5432",
            error_type=ErrorTypes.CONNECTION_REFUSED,
            message="Connection refused",
            suggestions=["Check if service is running", "Verify port is correct"],
        )

        results = ExecutionResults(
            results={
                "db01": ModuleResult(
                    host_name="db01",
                    success=False,
                    changed=False,
                    output={},
                    error="Connection refused",
                    error_context=error_ctx,
                ),
            }
        )

        output = format_results_text(results, verbose=False)

        assert "Error Details:" in output
        assert "Error on host 'db01'" in output
        assert "Type: ConnectionRefused" in output
        assert "Suggested Actions:" in output

    def test_get_suggestions(self):
        """Test suggestion generation with context substitution."""
        from ftl2.exceptions import get_suggestions, ErrorTypes

        suggestions = get_suggestions(
            ErrorTypes.CONNECTION_TIMEOUT,
            host="web01",
            host_address="192.168.1.10",
            port=22,
        )

        assert len(suggestions) > 0
        assert any("192.168.1.10" in s for s in suggestions)
        assert any("22" in s for s in suggestions)


class TestVarsCommands:
    """Test variable inspection commands."""

    def test_vars_list_command(self):
        """Test vars list command shows host summary."""
        import tempfile
        from pathlib import Path

        yaml_content = """
webservers:
  vars:
    http_port: 80
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_user: admin
      ansible_password: secret
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["vars", "list", "-i", inv_path])

            assert result.exit_code == 0
            assert "web01" in result.output
            assert "variable(s)" in result.output
            assert "webservers" in result.output
        finally:
            Path(inv_path).unlink()

    def test_vars_list_json(self):
        """Test vars list command with JSON output."""
        import json
        import tempfile
        from pathlib import Path

        yaml_content = """
servers:
  hosts:
    server01:
      ansible_host: 10.0.0.1
      ansible_user: root
      ansible_password: secret
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["vars", "list", "-i", inv_path, "--format", "json"])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["host_name"] == "server01"
            assert "variable_count" in data[0]
        finally:
            Path(inv_path).unlink()

    def test_vars_show_command(self):
        """Test vars show command displays variable details."""
        import tempfile
        from pathlib import Path

        yaml_content = """
webservers:
  vars:
    app_name: myapp
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_user: admin
      ansible_password: secret
      custom_var: custom_value
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["vars", "show", "web01", "-i", inv_path])

            assert result.exit_code == 0
            assert "Variables for web01:" in result.output
            assert "Groups: webservers" in result.output
            assert "ansible_host" in result.output
            assert "192.168.1.10" in result.output
            assert "app_name" in result.output
            assert "myapp" in result.output
            assert "custom_var" in result.output
            assert "custom_value" in result.output
        finally:
            Path(inv_path).unlink()

    def test_vars_show_json(self):
        """Test vars show command with JSON output."""
        import json
        import tempfile
        from pathlib import Path

        yaml_content = """
databases:
  hosts:
    db01:
      ansible_host: 10.0.0.5
      ansible_user: dbuser
      ansible_password: dbpass
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["vars", "show", "db01", "-i", inv_path, "--format", "json"])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["host_name"] == "db01"
            assert "databases" in data["groups"]
            assert isinstance(data["variables"], list)

            # Check that variables have expected structure
            var_names = [v["name"] for v in data["variables"]]
            assert "ansible_host" in var_names
            assert "ansible_user" in var_names
        finally:
            Path(inv_path).unlink()

    def test_vars_show_unknown_host(self):
        """Test vars show command with unknown host shows error."""
        import tempfile
        from pathlib import Path

        yaml_content = """
servers:
  hosts:
    server01:
      ansible_host: 10.0.0.1
      ansible_user: root
      ansible_password: secret
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, ["vars", "show", "nonexistent", "-i", inv_path])

            assert result.exit_code != 0
            assert "not found in inventory" in result.output
            assert "server01" in result.output  # Shows available hosts
        finally:
            Path(inv_path).unlink()

    def test_vars_help(self):
        """Test vars command help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["vars", "--help"])

        assert result.exit_code == 0
        assert "Variable inspection" in result.output
        assert "list" in result.output
        assert "show" in result.output


class TestSafetyChecks:
    """Test safety checks and destructive command detection."""

    def test_destructive_command_detected(self):
        """Test that destructive commands are detected."""
        from ftl2.safety import check_command_safety

        result = check_command_safety("rm -rf /var/data")
        assert not result.safe
        assert not result.blocked
        assert len(result.warnings) > 0

    def test_blocked_command_detected(self):
        """Test that blocked commands cannot be overridden."""
        from ftl2.safety import check_command_safety

        result = check_command_safety("rm -rf /")
        assert result.blocked
        assert not result.safe
        assert "destroy entire filesystem" in result.blocked_reason

    def test_safe_path_allowed(self):
        """Test that commands on safe paths are allowed."""
        from ftl2.safety import check_command_safety

        result = check_command_safety("rm -rf /tmp/old_data")
        assert result.safe
        assert not result.blocked
        assert len(result.warnings) == 0

    def test_module_args_safety_shell(self):
        """Test safety check for shell module."""
        from ftl2.safety import check_module_args_safety

        result = check_module_args_safety("shell", {"cmd": "rm -rf /var/log/old"})
        assert not result.safe

        result = check_module_args_safety("shell", {"cmd": "ls -la"})
        assert result.safe

    def test_module_args_safety_file_absent(self):
        """Test safety check for file module with state=absent."""
        from ftl2.safety import check_module_args_safety

        # Removing system file should warn
        result = check_module_args_safety("file", {"path": "/etc/important.conf", "state": "absent"})
        assert not result.safe

        # Removing temp file should be fine
        result = check_module_args_safety("file", {"path": "/tmp/test", "state": "absent"})
        assert result.safe

    def test_run_destructive_command_blocked(self):
        """Test that run command blocks destructive commands without flag."""
        import tempfile
        from pathlib import Path

        yaml_content = """
servers:
  hosts:
    server01:
      ansible_host: 10.0.0.1
      ansible_user: root
      ansible_password: secret
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, [
                "run", "-m", "shell", "-i", inv_path,
                "-a", "cmd='rm -rf /var/data'"
            ])

            assert result.exit_code != 0
            assert "Destructive command detected" in result.output
            assert "--allow-destructive" in result.output
        finally:
            Path(inv_path).unlink()

    def test_run_parallel_limit_enforced(self):
        """Test that parallel limit is enforced."""
        import tempfile
        from pathlib import Path

        yaml_content = """
servers:
  hosts:
    server01:
      ansible_host: 10.0.0.1
      ansible_user: root
      ansible_password: secret
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            inv_path = f.name

        try:
            runner = CliRunner()
            result = runner.invoke(cli, [
                "run", "-m", "ping", "-i", inv_path,
                "--parallel", "150"
            ])

            assert result.exit_code != 0
            assert "cannot exceed 100" in result.output
        finally:
            Path(inv_path).unlink()

    def test_run_help_shows_safe_defaults(self):
        """Test that run help shows safe defaults information."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--allow-destructive" in result.output
        assert "--parallel" in result.output
        assert "--timeout" in result.output
        assert "Safe defaults" in result.output


class TestRetryLogic:
    """Test retry logic and error classification."""

    def test_is_transient_error(self):
        """Test transient error classification."""
        from ftl2.retry import is_transient_error, is_permanent_error
        from ftl2.exceptions import ErrorTypes

        # Transient errors should be retried
        assert is_transient_error(ErrorTypes.CONNECTION_TIMEOUT)
        assert is_transient_error(ErrorTypes.CONNECTION_REFUSED)
        assert is_transient_error(ErrorTypes.HOST_UNREACHABLE)

        # Permanent errors should not be retried
        assert is_permanent_error(ErrorTypes.AUTHENTICATION_FAILED)
        assert is_permanent_error(ErrorTypes.PERMISSION_DENIED)
        assert is_permanent_error(ErrorTypes.MODULE_NOT_FOUND)

    def test_should_retry_smart(self):
        """Test smart retry logic."""
        from ftl2.retry import should_retry
        from ftl2.exceptions import ErrorTypes

        # Smart retry: only transient errors
        assert should_retry(ErrorTypes.CONNECTION_TIMEOUT, smart_retry=True)
        assert not should_retry(ErrorTypes.AUTHENTICATION_FAILED, smart_retry=True)

        # Non-smart retry: retry most errors
        assert should_retry(ErrorTypes.CONNECTION_TIMEOUT, smart_retry=False)
        assert should_retry(ErrorTypes.AUTHENTICATION_FAILED, smart_retry=False)
        # But not module not found
        assert not should_retry(ErrorTypes.MODULE_NOT_FOUND, smart_retry=False)

    def test_retry_config_delay_backoff(self):
        """Test exponential backoff calculation."""
        from ftl2.retry import RetryConfig

        config = RetryConfig(
            initial_delay=5.0,
            backoff_factor=2.0,
            max_delay=60.0,
        )

        # First delay should be initial
        delay1 = config.get_delay(1)
        assert 4.5 <= delay1 <= 5.5  # Allow for jitter

        # Second delay should be doubled
        delay2 = config.get_delay(2)
        assert 9.0 <= delay2 <= 11.0

        # Third delay should be quadrupled
        delay3 = config.get_delay(3)
        assert 18.0 <= delay3 <= 22.0

    def test_retry_config_max_delay_cap(self):
        """Test that max delay is capped."""
        from ftl2.retry import RetryConfig

        config = RetryConfig(
            initial_delay=10.0,
            backoff_factor=10.0,
            max_delay=30.0,
        )

        # Very high attempt should be capped at max_delay
        delay = config.get_delay(10)
        assert delay <= 33.0  # max_delay + jitter

    def test_circuit_breaker_check(self):
        """Test circuit breaker threshold."""
        from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig

        config = CircuitBreakerConfig(
            enabled=True,
            threshold_percent=30.0,
            min_hosts=5,
        )

        # Below threshold - don't trigger
        assert not check_circuit_breaker(10, 2, config)  # 20%

        # Above threshold - trigger
        assert check_circuit_breaker(10, 4, config)  # 40%

        # Too few hosts - don't trigger
        assert not check_circuit_breaker(3, 3, config)  # 100% but only 3 hosts

    def test_circuit_breaker_disabled(self):
        """Test that disabled circuit breaker never triggers."""
        from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig

        config = CircuitBreakerConfig(enabled=False)

        # Even 100% failure shouldn't trigger when disabled
        assert not check_circuit_breaker(10, 10, config)

    def test_run_help_shows_retry_options(self):
        """Test that run help shows retry options."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--retry" in result.output
        assert "--retry-delay" in result.output
        assert "--smart-retry" in result.output
        assert "--circuit-breaker" in result.output
        assert "Retry options" in result.output


class TestStateTracking:
    """Test state tracking and resume functionality."""

    def test_host_state_serialization(self):
        """Test HostState to/from dict."""
        from ftl2.state import HostState

        state = HostState(
            host_name="web01",
            success=True,
            changed=True,
            timestamp="2026-02-05T12:00:00Z",
            attempts=2,
        )

        data = state.to_dict()
        assert data["host_name"] == "web01"
        assert data["success"] is True
        assert data["changed"] is True

        restored = HostState.from_dict(data)
        assert restored.host_name == "web01"
        assert restored.success is True

    def test_execution_state_serialization(self):
        """Test ExecutionState to/from dict."""
        from ftl2.state import ExecutionState, HostState

        state = ExecutionState(
            module="ping",
            args={"data": "hello"},
            inventory_file="hosts.yml",
            timestamp="2026-02-05T12:00:00Z",
            completed=True,
            hosts={
                "web01": HostState("web01", success=True),
                "web02": HostState("web02", success=False, error="timeout"),
            },
            total_hosts=2,
            successful=1,
            failed=1,
        )

        data = state.to_dict()
        assert data["module"] == "ping"
        assert data["total_hosts"] == 2
        assert "web01" in data["hosts"]

        restored = ExecutionState.from_dict(data)
        assert restored.module == "ping"
        assert len(restored.hosts) == 2
        assert restored.hosts["web01"].success is True
        assert restored.hosts["web02"].success is False

    def test_get_succeeded_failed_hosts(self):
        """Test getting succeeded and failed hosts from state."""
        from ftl2.state import ExecutionState, HostState

        state = ExecutionState(
            module="ping",
            hosts={
                "web01": HostState("web01", success=True),
                "web02": HostState("web02", success=True),
                "db01": HostState("db01", success=False),
            },
        )

        succeeded = state.get_succeeded_hosts()
        failed = state.get_failed_hosts()

        assert succeeded == {"web01", "web02"}
        assert failed == {"db01"}

    def test_get_pending_hosts(self):
        """Test getting pending (new) hosts."""
        from ftl2.state import ExecutionState, HostState

        state = ExecutionState(
            module="ping",
            hosts={
                "web01": HostState("web01", success=True),
                "web02": HostState("web02", success=False),
            },
        )

        all_hosts = {"web01", "web02", "web03", "db01"}
        pending = state.get_pending_hosts(all_hosts)

        assert pending == {"web03", "db01"}

    def test_filter_hosts_for_resume(self):
        """Test filtering hosts for resume mode."""
        from ftl2.state import ExecutionState, HostState, filter_hosts_for_resume

        state = ExecutionState(
            module="ping",
            hosts={
                "web01": HostState("web01", success=True),
                "web02": HostState("web02", success=True),
                "db01": HostState("db01", success=False),
            },
        )

        all_hosts = {"web01", "web02", "db01", "db02"}
        to_run, skipped, new = filter_hosts_for_resume(all_hosts, state)

        # Should skip succeeded hosts
        assert skipped == {"web01", "web02"}
        # Should run failed and new hosts
        assert to_run == {"db01", "db02"}
        # New hosts
        assert new == {"db02"}

    def test_save_and_load_state(self):
        """Test saving and loading state to/from file."""
        import tempfile
        from pathlib import Path
        from ftl2.state import ExecutionState, HostState, save_state, load_state

        state = ExecutionState(
            module="copy",
            args={"src": "app.tgz", "dest": "/opt/"},
            inventory_file="hosts.yml",
            timestamp="2026-02-05T12:00:00Z",
            completed=True,
            hosts={
                "web01": HostState("web01", success=True, changed=True),
            },
            total_hosts=1,
            successful=1,
            failed=0,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_path = Path(f.name)

        try:
            save_state(state, state_path)
            assert state_path.exists()

            loaded = load_state(state_path)
            assert loaded is not None
            assert loaded.module == "copy"
            assert loaded.hosts["web01"].success is True
        finally:
            state_path.unlink()

    def test_load_nonexistent_state(self):
        """Test loading state from nonexistent file returns None."""
        from ftl2.state import load_state

        result = load_state("/tmp/nonexistent-state-file-12345.json")
        assert result is None

    def test_run_help_shows_state_options(self):
        """Test that run help shows state tracking options."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--state-file" in result.output
        assert "--resume" in result.output
        assert "State tracking" in result.output


class TestEnhancedLogging:
    """Test enhanced logging features."""

    def test_verbosity_levels(self):
        """Test verbosity count to log level conversion."""
        from ftl2.logging import get_level_from_verbosity, TRACE
        import logging

        assert get_level_from_verbosity(0) == logging.WARNING
        assert get_level_from_verbosity(1) == logging.INFO
        assert get_level_from_verbosity(2) == logging.DEBUG
        assert get_level_from_verbosity(3) == TRACE
        assert get_level_from_verbosity(4) == TRACE  # Max out at trace

    def test_level_from_name(self):
        """Test log level from name conversion."""
        from ftl2.logging import get_level_from_name, TRACE
        import logging

        assert get_level_from_name("trace") == TRACE
        assert get_level_from_name("debug") == logging.DEBUG
        assert get_level_from_name("info") == logging.INFO
        assert get_level_from_name("warning") == logging.WARNING
        assert get_level_from_name("error") == logging.ERROR
        assert get_level_from_name("critical") == logging.CRITICAL

    def test_level_from_name_case_insensitive(self):
        """Test log level name is case insensitive."""
        from ftl2.logging import get_level_from_name
        import logging

        assert get_level_from_name("DEBUG") == logging.DEBUG
        assert get_level_from_name("Info") == logging.INFO
        assert get_level_from_name("WARNING") == logging.WARNING

    def test_level_from_name_invalid(self):
        """Test invalid log level name raises error."""
        from ftl2.logging import get_level_from_name
        import pytest

        with pytest.raises(ValueError) as exc_info:
            get_level_from_name("invalid")
        assert "Invalid log level" in str(exc_info.value)

    def test_configure_logging_with_file(self):
        """Test logging configuration with file output."""
        import tempfile
        import logging
        from pathlib import Path
        from ftl2.logging import configure_logging

        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = Path(f.name)

        try:
            configure_logging(
                level=logging.INFO,
                log_file=log_path,
                file_level=logging.DEBUG,
            )

            # Get a logger and log a message
            logger = logging.getLogger("test.logging")
            logger.info("Test info message")
            logger.debug("Test debug message")

            # Read log file
            log_content = log_path.read_text()
            assert "Test info message" in log_content
            assert "Test debug message" in log_content
        finally:
            log_path.unlink()

    def test_run_help_shows_logging_options(self):
        """Test that run help shows enhanced logging options."""
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--help"])

        assert result.exit_code == 0
        assert "--log-file" in result.output
        assert "--log-level" in result.output
        assert "-v" in result.output
        assert "trace" in result.output.lower()

    def test_verbose_count_option(self):
        """Test that -v can be specified multiple times."""
        runner = CliRunner()
        # Just verify the help mentions verbosity levels
        result = runner.invoke(cli, ["run", "--help"])

        assert "Increase verbosity" in result.output
        assert "-vv" in result.output or "debug" in result.output.lower()


class TestIdempotencyParsing:
    """Test idempotency parsing from module docstrings."""

    def test_parse_idempotent_yes(self):
        """Test parsing Idempotent: Yes from docstring."""
        from ftl2.module_docs import parse_module_docstring

        docstring = """
Module - Test module.

Description of the module.

Arguments:
  arg1 (str, optional): An argument

Returns:
  result (str): The result

Idempotent: Yes
"""
        parsed = parse_module_docstring(docstring)
        assert parsed["idempotent"] is True

    def test_parse_idempotent_no(self):
        """Test parsing Idempotent: No from docstring."""
        from ftl2.module_docs import parse_module_docstring

        docstring = """
Module - Test module.

Idempotent: No
"""
        parsed = parse_module_docstring(docstring)
        assert parsed["idempotent"] is False

    def test_parse_idempotent_true_false(self):
        """Test parsing Idempotent: True/False from docstring."""
        from ftl2.module_docs import parse_module_docstring

        docstring_true = "Module - Test.\n\nIdempotent: True"
        docstring_false = "Module - Test.\n\nIdempotent: False"

        assert parse_module_docstring(docstring_true)["idempotent"] is True
        assert parse_module_docstring(docstring_false)["idempotent"] is False

    def test_parse_idempotent_missing(self):
        """Test that missing idempotency returns None."""
        from ftl2.module_docs import parse_module_docstring

        docstring = """
Module - Test module.

Arguments:
  arg1 (str, optional): An argument
"""
        parsed = parse_module_docstring(docstring)
        assert parsed["idempotent"] is None

    def test_extract_module_doc_uses_docstring_idempotency(self):
        """Test that extract_module_doc uses parsed idempotency from docstring."""
        import tempfile
        from pathlib import Path
        from ftl2.module_docs import extract_module_doc

        module_content = '''#!/usr/bin/env python3
"""
Custom module - A custom test module.

Does something custom.

Arguments:
  value (str, required): A value

Returns:
  result (str): The result

Idempotent: Yes
"""

def main():
    pass
'''
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(module_content)
            f.flush()
            module_path = Path(f.name)

        try:
            doc = extract_module_doc(module_path)
            assert doc.idempotent is True
        finally:
            module_path.unlink()

    def test_extract_module_doc_fallback_to_hardcoded(self):
        """Test that extract_module_doc falls back to hardcoded list when not in docstring."""
        import tempfile
        from pathlib import Path
        from ftl2.module_docs import extract_module_doc

        # Create a module named 'ping' without Idempotent declaration
        # Should fall back to hardcoded list (ping is idempotent)
        module_content = '''#!/usr/bin/env python3
"""
Ping - Test connectivity.

Tests network connectivity.
"""

def main():
    pass
'''
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="ping", suffix=".py", delete=False
        ) as f:
            f.write(module_content)
            f.flush()
            module_path = Path(f.name)

        try:
            # Rename to match 'ping' for hardcoded fallback test
            ping_path = module_path.parent / "ping.py"
            module_path.rename(ping_path)
            doc = extract_module_doc(ping_path)
            # Should fall back to hardcoded: ping is idempotent
            assert doc.idempotent is True
        finally:
            if ping_path.exists():
                ping_path.unlink()
