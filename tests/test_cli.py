"""Test CLI functionality."""

from click.testing import CliRunner

from ftl2 import __version__
from ftl2.cli import main, parse_module_args


def test_cli_version():
    """Test CLI version output."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help():
    """Test CLI help output."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "FTL2" in result.output
    assert "--module" in result.output
    assert "--inventory" in result.output


def test_cli_missing_module():
    """Test CLI error when module not specified."""
    runner = CliRunner()
    result = runner.invoke(main, ["-i", "inventory.yml"])
    assert result.exit_code != 0
    assert "Must specify --module" in result.output


def test_cli_missing_inventory():
    """Test CLI error when inventory not specified."""
    runner = CliRunner()
    result = runner.invoke(main, ["-m", "ping"])
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
