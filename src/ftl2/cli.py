"""Command-line interface for FTL2."""

import asyncio
import logging
import shlex
from pathlib import Path
from pprint import pprint
from typing import Optional

import click

from ftl2 import __version__
from ftl2.executor import ModuleExecutor
from ftl2.inventory import load_inventory, Inventory
from ftl2.logging import configure_logging, get_logger
from ftl2.runners import ExecutionContext
from ftl2.types import ExecutionConfig, GateConfig

logger = get_logger("ftl2.cli")


# Main CLI group
@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version and exit")
@click.pass_context
def cli(ctx: click.Context, version: bool) -> None:
    """FTL2 - Fast automation framework for AI-assisted development."""
    if version:
        click.echo(f"ftl2 {__version__}")
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Inventory subcommand group
@cli.group()
def inventory() -> None:
    """Inventory management commands."""
    pass


@inventory.command("validate")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--check-ssh", is_flag=True, help="Also validate SSH key files exist")
def inventory_validate(inventory: str, check_ssh: bool) -> None:
    """Validate inventory structure and show summary.

    Loads the inventory file and displays:
    - Number of groups and hosts loaded
    - Host details (connection type, address, port)
    - Validation warnings for common issues
    """
    try:
        inv = load_inventory(inventory)
    except ValueError as e:
        raise click.ClickException(str(e))

    all_hosts = inv.get_all_hosts()
    groups = inv.list_groups()

    click.echo(f"\nInventory: {inventory}")
    click.echo(f"Loaded {len(all_hosts)} host(s) from {len(groups)} group(s)\n")

    # Show groups and their hosts
    for group in groups:
        host_count = len(group.hosts)
        click.echo(f"  {group.name} ({host_count} host{'s' if host_count != 1 else ''}):")

        for host_name, host in group.hosts.items():
            conn_type = host.ansible_connection
            addr = host.ansible_host
            port = host.ansible_port

            if conn_type == "local":
                click.echo(f"    - {host_name} (local)")
            else:
                click.echo(f"    - {host_name} ({addr}:{port})")

    # Validation checks
    click.echo("\nValidation:")
    warnings = []
    errors = []

    for host_name, host in all_hosts.items():
        # Check SSH authentication
        if host.ansible_connection == "ssh":
            ssh_password = host.get_var("ansible_password")
            ssh_key_file = host.get_var("ssh_private_key_file")

            if not ssh_password and not ssh_key_file:
                errors.append(f"{host_name}: No SSH authentication configured")
            elif ssh_key_file and check_ssh:
                expanded = Path(ssh_key_file).expanduser()
                if not expanded.exists():
                    errors.append(f"{host_name}: SSH key not found: {expanded}")

        # Check for missing ansible_host
        if not host.ansible_host:
            warnings.append(f"{host_name}: Missing ansible_host")

    if not errors and not warnings:
        click.echo("  All checks passed")
    else:
        for warning in warnings:
            click.echo(f"  Warning: {warning}")
        for error in errors:
            click.echo(f"  Error: {error}")

    if errors:
        raise click.ClickException(f"{len(errors)} validation error(s) found")

    click.echo()


@cli.command("test-ssh")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--timeout", "-t", default=10, help="Connection timeout in seconds")
def test_ssh(inventory: str, timeout: int) -> None:
    """Test SSH connectivity to all hosts in inventory.

    Attempts to connect to each SSH host and reports success/failure.
    Useful for verifying SSH setup before running modules.

    Examples:
        ftl2 test-ssh -i hosts.yml

        ftl2 test-ssh -i inventory.yml --timeout 5
    """
    import asyncio
    import socket

    try:
        inv = load_inventory(inventory)
    except ValueError as e:
        raise click.ClickException(str(e))

    all_hosts = inv.get_all_hosts()
    ssh_hosts = {name: host for name, host in all_hosts.items()
                 if host.ansible_connection == "ssh"}

    if not ssh_hosts:
        click.echo("No SSH hosts found in inventory")
        return

    click.echo(f"\nTesting SSH connectivity to {len(ssh_hosts)} host(s)...\n")

    async def test_host(host_name: str, host) -> tuple[str, bool, str]:
        """Test SSH connectivity to a single host."""
        addr = host.ansible_host
        port = host.ansible_port
        user = host.ansible_user or "root"

        # Step 1: Test port connectivity
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((addr, port))
            sock.close()

            if result != 0:
                return (host_name, False, f"Port {port} not reachable")
        except socket.error as e:
            return (host_name, False, f"Socket error: {e}")

        # Step 2: Test SSH authentication
        try:
            import asyncssh
            import os

            ssh_password = host.get_var("ansible_password")
            ssh_key_file = host.get_var("ssh_private_key_file")

            connect_kwargs = {
                "host": addr,
                "port": port,
                "username": user,
                "known_hosts": None,
                "connect_timeout": timeout,
            }

            if ssh_password:
                connect_kwargs["password"] = ssh_password
            elif ssh_key_file:
                expanded_key = os.path.expanduser(ssh_key_file)
                connect_kwargs["client_keys"] = [expanded_key]

            conn = await asyncssh.connect(**connect_kwargs)
            conn.close()
            return (host_name, True, "OK")

        except asyncio.TimeoutError:
            return (host_name, False, "Connection timeout")
        except Exception as e:
            error_msg = str(e)
            # Simplify common error messages
            if "Permission denied" in error_msg:
                return (host_name, False, "Authentication failed (permission denied)")
            elif "Connection refused" in error_msg:
                return (host_name, False, "Connection refused")
            return (host_name, False, f"SSH error: {error_msg[:50]}")

    async def run_tests():
        """Run all SSH tests concurrently."""
        tasks = [test_host(name, host) for name, host in ssh_hosts.items()]
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_tests())

    # Display results
    success_count = 0
    fail_count = 0

    for host_name, success, message in results:
        host = ssh_hosts[host_name]
        addr = host.ansible_host
        port = host.ansible_port

        if success:
            click.echo(f"  {host_name} ({addr}:{port}): OK")
            success_count += 1
        else:
            click.echo(f"  {host_name} ({addr}:{port}): FAILED - {message}")
            fail_count += 1

    click.echo(f"\nResults: {success_count} passed, {fail_count} failed")

    if fail_count > 0:
        raise click.ClickException(f"{fail_count} host(s) failed SSH connectivity test")


def parse_module_args(args: str | None) -> dict[str, str]:
    """Parse module arguments from command-line string into dictionary format.

    Converts a space-separated string of key=value pairs into a dictionary
    suitable for passing to automation modules. Properly handles quoted values.

    Args:
        args: String containing space-separated key=value pairs. Can be empty
            or None. Example: "host=example.com port=80 debug=true"
            Supports quoted values: "cmd='echo hello' path=/tmp/file"

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

        >>> parse_module_args("cmd='echo hello world'")
        {'cmd': 'echo hello world'}
    """
    if not args:
        return {}

    # Use shlex to properly handle quoted strings
    try:
        key_value_pairs = shlex.split(args)
    except ValueError as e:
        raise ValueError(f"Failed to parse arguments: {e}") from e

    result = {}
    for pair in key_value_pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid argument format: '{pair}'. Expected key=value format.")

        # Split on first = only to handle values with =
        key, value = pair.split("=", 1)
        result[key] = value

    return result


def validate_execution_requirements(inventory, module_name: str, module_dirs: list[Path]) -> None:
    """Validate all requirements before attempting execution.

    Performs pre-flight checks to catch configuration errors early:
    - Module exists in search paths
    - SSH hosts have authentication configured
    - SSH key files exist if specified

    Args:
        inventory: Loaded inventory object
        module_name: Name of module to execute
        module_dirs: List of directories to search for modules

    Raises:
        ValueError: If any validation check fails with detailed error message

    Example:
        >>> validate_execution_requirements(inv, "ping", [Path("/modules")])
    """
    from ftl2.inventory import Inventory

    # 1. Check module exists
    module_found = False
    for module_dir in module_dirs:
        if (module_dir / f"{module_name}.py").exists():
            module_found = True
            break

    if not module_found:
        # List available modules for helpful error message
        available_modules = []
        for module_dir in module_dirs:
            if module_dir.exists():
                available_modules.extend([m.stem for m in module_dir.glob("*.py")])

        error_msg = f"Module '{module_name}' not found in:\n"
        error_msg += "\n".join(f"  - {d}" for d in module_dirs)

        if available_modules:
            error_msg += f"\n\nAvailable modules:\n"
            error_msg += "\n".join(f"  - {m}" for m in sorted(set(available_modules)))
        else:
            error_msg += f"\n\nNo modules found in search paths"

        raise ValueError(error_msg)

    # 2. For remote hosts, validate SSH configuration
    all_hosts = inventory.get_all_hosts()
    for host_name, host in all_hosts.items():
        if host.ansible_connection == "ssh":
            ssh_password = host.get_var("ansible_password")
            ssh_key_file = host.get_var("ssh_private_key_file")

            # Check that at least one auth method is configured
            if not ssh_password and not ssh_key_file:
                raise ValueError(
                    f"Host '{host_name}': No SSH authentication configured\n"
                    f"  Set either:\n"
                    f"    - ansible_password: 'password'\n"
                    f"    - ssh_private_key_file: ~/.ssh/id_rsa"
                )

            # Check that SSH key file exists if specified
            if ssh_key_file:
                expanded = Path(ssh_key_file).expanduser()
                if not expanded.exists():
                    raise ValueError(
                        f"Host '{host_name}': SSH key not found: {expanded}\n"
                        f"  Generate with: ssh-keygen -t rsa -f {expanded}"
                    )


@cli.command("run")
@click.option("--module", "-m", required=True, help="Module to execute")
@click.option("--module-dir", "-M", help="Module directory to search for modules")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--requirements", "-r", help="Python requirements file")
@click.option("--args", "-a", help="Module arguments in key=value format")
@click.option("--debug", is_flag=True, help="Show debug logging")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose logging")
def run_module(
    module: str,
    module_dir: Optional[str],
    inventory: str,
    requirements: Optional[str],
    args: Optional[str],
    debug: bool,
    verbose: bool,
) -> None:
    """Execute a module across inventory hosts.

    Runs the specified automation module on all hosts in the inventory,
    with support for variable references, host-specific arguments, and
    remote execution via SSH.

    Examples:
        ftl2 run -m ping -i hosts.yml

        ftl2 run -m file -i inventory.yml -a "path=/tmp/test state=touch"

        ftl2 run -m shell -i hosts.yml -a "cmd='uptime'" --verbose
    """
    # Configure logging
    if debug:
        configure_logging(level=logging.DEBUG, debug=True)
    elif verbose:
        configure_logging(level=logging.INFO)
    else:
        configure_logging(level=logging.WARNING)

    # Load dependencies if requirements file specified
    dependencies = []
    if requirements:
        with open(requirements) as f:
            dependencies = [x for x in f.read().splitlines() if x]

    # Build module directories list
    module_dirs = []

    # Add default built-in modules directory
    default_module_dir = Path(__file__).parent / "modules"
    if default_module_dir.exists():
        module_dirs.append(default_module_dir)

    # Add user-specified module directory if provided
    if module_dir:
        module_dirs.append(Path(module_dir))

    async def run_async() -> None:
        """Inner async function to handle async operations."""
        # Add module context to logger
        logger.add_context(module=module)

        with logger.performance("Total execution", module=module):
            # Load inventory
            logger.debug("Loading inventory", file=inventory)
            inv = load_inventory(inventory)
            logger.info("Inventory loaded", hosts=len(inv.get_all_hosts()))

            # Validate execution requirements (fail-fast)
            logger.debug("Validating execution requirements")
            validate_execution_requirements(inv, module, module_dirs)
            logger.debug("Validation passed")

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
                with logger.scope("Module execution"):
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

                logger.info("Execution complete",
                           successful=results.successful,
                           failed=results.failed)

                # Exit with error if any host failed
                if not results.is_success():
                    raise click.ClickException(f"{results.failed} host(s) failed execution")

            finally:
                # Clean up resources
                logger.debug("Cleaning up resources")
                await executor.cleanup()

    # Run the async operations
    asyncio.run(run_async())


def main() -> None:
    """Package entry point for the FTL2 command-line interface."""
    cli()


def entry_point() -> None:
    """Package entry point for the FTL2 command-line interface."""
    cli()


if __name__ == "__main__":
    cli()
