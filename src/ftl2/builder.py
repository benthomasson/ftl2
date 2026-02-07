"""FTL gate builder command-line tool for creating automation execution packages.

Standalone CLI for building FTL gates - self-contained Python executable
archives (.pyz) that package automation modules and dependencies for
remote execution.

Usage:
    ftl-gate-builder [options]

Options:
    -m, --module        Module to include (can be specified multiple times)
    -M, --module-dir    Module directory to search (can be specified multiple times)
    -r, --requirements  Python requirements file (can be specified multiple times)
    -I, --interpreter   Python interpreter for target system (default: /usr/bin/python3)
    -c, --cache-dir     Cache directory for built gates (default: ~/.ftl)
    -v, --verbose       Show verbose logging
    -d, --debug         Show debug logging

Examples:
    ftl-gate-builder -m ping -m setup -M /opt/modules
    ftl-gate-builder -m custom_module -r requirements.txt -M ./modules
    ftl-gate-builder -m module -I /opt/python3.9/bin/python --debug
"""

import logging
import sys
from pathlib import Path

import click

from .gate import GateBuildConfig, GateBuilder

logger = logging.getLogger("builder")


@click.command()
@click.option("--module", "-m", multiple=True, help="Module to include (repeatable)")
@click.option("--module-dir", "-M", multiple=True, help="Module search directory (repeatable)")
@click.option("--requirements", "-r", multiple=True, help="Requirements file (repeatable)")
@click.option("--interpreter", "-I", default="/usr/bin/python3", help="Target Python interpreter")
@click.option("--cache-dir", "-c", default="~/.ftl", help="Cache directory for built gates")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--debug", "-d", is_flag=True, help="Debug logging")
def main(
    module: tuple[str, ...],
    module_dir: tuple[str, ...],
    requirements: tuple[str, ...],
    interpreter: str,
    cache_dir: str,
    verbose: bool,
    debug: bool,
) -> None:
    """Build an FTL gate executable archive."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    elif verbose:
        logging.basicConfig(level=logging.INFO)

    # Parse requirements files
    dependencies: list[str] = []
    for reqs_path in requirements:
        with open(reqs_path) as f:
            dependencies.extend(
                line for line in f.read().splitlines() if line and not line.startswith("#")
            )

    # Default to built-in modules directory if no -M specified
    module_dirs = list(module_dir)
    if not module_dirs:
        builtin_modules = Path(__file__).parent / "modules"
        if builtin_modules.is_dir():
            module_dirs.append(str(builtin_modules))

    config = GateBuildConfig(
        modules=list(module),
        module_dirs=module_dirs,
        dependencies=dependencies,
        interpreter=interpreter,
    )

    builder = GateBuilder(cache_dir=cache_dir)
    gate_path, gate_hash = builder.build(config)

    click.echo(f"Gate: {gate_path}")
    click.echo(f"Hash: {gate_hash}")


def entry_point() -> None:
    """Package entry point for ftl-gate-builder CLI."""
    main(sys.argv[1:])
