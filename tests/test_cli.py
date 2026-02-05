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
