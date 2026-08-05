"""FTL gate builder command-line tool for creating automation execution packages.

Standalone CLI for building FTL gates - self-contained Python executable
archives (.pyz) that package automation modules and dependencies for
remote execution.

Usage:
    ftl-gate-builder [options]

Options:
    -m, --module            Module to include (can be specified multiple times)
    -A, --all-modules       Include all built-in gate and FTL modules
    -f, --from-modules-file Read module names from file (one per line)
    -M, --module-dir        Module directory to search (can be specified multiple times)
    -r, --requirements      Python requirements file (can be specified multiple times)
    -I, --interpreter       Python interpreter for target system (default: /usr/bin/python3)
    -o, --output            Copy built gate to this path
    -c, --cache-dir         Cache directory for built gates (default: ~/.ftl)
    -v, --verbose           Show verbose logging
    -d, --debug             Show debug logging

Examples:
    ftl-gate-builder -m ping -m setup -M /opt/modules
    ftl-gate-builder --all-modules -o gate.pyz
    ftl-gate-builder -f .ftl2-modules.txt
    ftl-gate-builder -f .ftl2-modules.txt -m extra_module -r requirements.txt
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
@click.option("--all-modules", "-A", is_flag=True, help="Include all built-in gate and FTL modules")
@click.option("--from-modules-file", "-f", default=None, help="Read module names from file (one per line, e.g. .ftl2-modules.txt)")
@click.option("--module-dir", "-M", multiple=True, help="Module search directory (repeatable)")
@click.option("--requirements", "-r", multiple=True, help="Requirements file (repeatable)")
@click.option("--interpreter", "-I", default="/usr/bin/python3", help="Target Python interpreter")
@click.option("--output", "-o", default=None, type=click.Path(), help="Copy built gate to this path")
@click.option("--cache-dir", "-c", default="~/.ftl", help="Cache directory for built gates")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--debug", "-d", is_flag=True, help="Debug logging")
def main(
    module: tuple[str, ...],
    all_modules: bool,
    from_modules_file: str | None,
    module_dir: tuple[str, ...],
    requirements: tuple[str, ...],
    interpreter: str,
    output: str | None,
    cache_dir: str,
    verbose: bool,
    debug: bool,
) -> None:
    """Build an FTL gate executable archive."""
    if debug:
        logging.basicConfig(level=logging.DEBUG)
    elif verbose:
        logging.basicConfig(level=logging.INFO)

    # Collect modules from -m flags and --from-modules-file
    modules = list(module)
    if from_modules_file:
        modules_path = Path(from_modules_file)
        if not modules_path.exists():
            raise click.ClickException(f"Modules file not found: {from_modules_file}")
        text = modules_path.read_text().strip()
        if text:
            file_modules = [
                line.strip() for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            modules.extend(file_modules)
            click.echo(f"Loaded {len(file_modules)} modules from {from_modules_file}")

    if all_modules:
        builtin_modules_dir = Path(__file__).parent / "modules"
        for p in sorted(builtin_modules_dir.glob("*.py")):
            if not p.name.startswith("_"):
                modules.append(p.stem)
        from ftl2.ftl_modules.executor import _FTL_MODULE_FILES
        for name in _FTL_MODULE_FILES:
            modules.append(name)
        click.echo(f"Auto-discovered {len(modules)} modules (--all-modules)")

    if not modules:
        raise click.ClickException("No modules specified. Use -m, -f, or --all-modules.")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_modules: list[str] = []
    for m in modules:
        if m not in seen:
            seen.add(m)
            unique_modules.append(m)
    modules = unique_modules

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
        modules=modules,
        module_dirs=module_dirs,
        dependencies=dependencies,
        interpreter=interpreter,
    )

    builder = GateBuilder(cache_dir=cache_dir)
    gate_path, gate_hash = builder.build(config)

    if output:
        import shutil
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(gate_path, output_path)
        click.echo(f"Gate: {output_path}")
    else:
        click.echo(f"Gate: {gate_path}")
    click.echo(f"Hash: {gate_hash}")


def entry_point() -> None:
    """Package entry point for ftl-gate-builder CLI."""
    main(sys.argv[1:])
