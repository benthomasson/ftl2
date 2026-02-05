"""Test CLI functionality."""

from click.testing import CliRunner

from ftl2 import __version__
from ftl2.cli import main


def test_cli_version():
    """Test CLI version output."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_main():
    """Test CLI main command."""
    runner = CliRunner()
    result = runner.invoke(main)
    assert result.exit_code == 0
    assert "FTL2" in result.output
    assert __version__ in result.output
