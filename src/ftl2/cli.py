"""Command-line interface for FTL2."""

import asyncio
import logging
from pathlib import Path
from pprint import pprint
from typing import Optional

import click

from ftl2 import __version__
from ftl2.executor import ModuleExecutor
from ftl2.inventory import load_inventory
from ftl2.runners import ExecutionContext
from ftl2.types import ExecutionConfig, GateConfig

logger = logging.getLogger("ftl2.cli")


def parse_module_args(args: str | None) -> dict[str, str]:
    """Parse module arguments from command-line string into dictionary format.

    Converts a space-separated string of key=value pairs into a dictionary
    suitable for passing to automation modules.

    Args:
        args: String containing space-separated key=value pairs. Can be empty
            or None. Example: "host=example.com port=80 debug=true"

    Returns:
        Dictionary mapping argument keys to values. All keys and values are
        strings. Returns empty dictionary if args is None or empty.

    Raises:
        ValueError: If any argument pair does not contain exactly one equals
            sign, indicating malformed key=value syntax.

    Example:
        >>> parse_module_args("host=web01 port=80")
        {'host': 'web01', 'port': '80'}

        >>> parse_module_args("")
        {}

        >>> parse_module_args("path=/tmp/test state=touch")
        {'path': '/tmp/test', 'state': 'touch'}
    """
    if not args:
        return {}

    key_value_pairs = args.split(" ")
    key_value_tuples = [tuple(i.split("=")) for i in key_value_pairs]
    return {k: v for k, v in key_value_tuples}


@click.command()
@click.option("--module", "-m", help="Module to execute")
@click.option("--module-dir", "-M", help="Module directory to search for modules")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--requirements", "-r", help="Python requirements file")
@click.option("--args", "-a", help="Module arguments in key=value format")
@click.option("--debug", is_flag=True, help="Show debug logging")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose logging")
@click.version_option(version=__version__)
def main(
    module: Optional[str],
    module_dir: Optional[str],
    inventory: str,
    requirements: Optional[str],
    args: Optional[str],
    debug: bool,
    verbose: bool,
) -> None:
    """FTL2 - Refactored automation framework.

    Execute automation modules across an inventory of hosts with support for
    variable references, host-specific arguments, and remote execution.

    Args:
        module: Module name to execute
        module_dir: Directory to search for modules
        inventory: YAML inventory file (required)
        requirements: Python requirements file for dependencies
        args: Module arguments in key=value format
        debug: Enable debug logging
        verbose: Enable verbose logging

    Example:
        >>> ftl2 --module ping --inventory hosts.yml

        >>> ftl2 -m file -i inventory.yml -a "path=/tmp/test state=touch"

        >>> ftl2 --module copy --inventory hosts.yml --debug
    """
    # Validate required options
    if not module:
        raise click.ClickException("Must specify --module")

    # Configure logging
    if debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    elif verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Load dependencies if requirements file specified
    dependencies = []
    if requirements:
        with open(requirements) as f:
            dependencies = [x for x in f.read().splitlines() if x]

    # Build module directories list
    module_dirs = []
    if module_dir:
        module_dirs.append(Path(module_dir))

    async def run_async() -> None:
        """Inner async function to handle async operations."""
        # Load inventory
        inv = load_inventory(inventory)

        # Create execution configuration
        exec_config = ExecutionConfig(
            module_name=module,
            module_dirs=module_dirs,
            module_args=parse_module_args(args),
            modules=[module],
            dependencies=dependencies,
        )

        # Create gate configuration
        gate_config = GateConfig()

        # Create execution context
        context = ExecutionContext(
            execution_config=exec_config,
            gate_config=gate_config,
        )

        # Create executor and run
        executor = ModuleExecutor()
        try:
            results = await executor.run(inv, context)

            # Display results
            click.echo(f"\nExecution Results:")
            click.echo(f"Total hosts: {results.total_hosts}")
            click.echo(f"Successful: {results.successful}")
            click.echo(f"Failed: {results.failed}")
            click.echo()

            if verbose or debug:
                click.echo("Detailed Results:")
                pprint(results.results)

            # Exit with error if any host failed
            if not results.is_success():
                raise click.ClickException(f"{results.failed} host(s) failed execution")

        finally:
            # Clean up resources
            await executor.cleanup()

    # Run the async operations
    asyncio.run(run_async())


def entry_point() -> None:
    """Package entry point for the FTL2 command-line interface."""
    main()


if __name__ == "__main__":
    main()
