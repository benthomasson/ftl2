"""Command-line interface for FTL2."""

import asyncio
import json
import logging
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint
from typing import Any, Optional

import click

from ftl2 import __version__
from ftl2.executor import ModuleExecutor, ExecutionResults
from ftl2.inventory import load_inventory, Inventory
from ftl2.logging import configure_logging, get_logger
from ftl2.module_docs import discover_modules, extract_module_doc, format_module_list, format_module_list_json
from ftl2.vars import (
    collect_host_variables,
    get_all_host_variables,
    format_all_hosts_text,
    format_all_hosts_json,
)
from ftl2.safety import (
    check_module_args_safety,
    format_safety_error,
    DEFAULT_PARALLEL,
    DEFAULT_TIMEOUT,
    MAX_PARALLEL,
)
from ftl2.retry import (
    RetryConfig,
    CircuitBreakerConfig,
    RetryStats,
    is_transient_error,
)
from ftl2.runners import ExecutionContext
from ftl2.types import ExecutionConfig, GateConfig, ModuleResult

logger = get_logger("ftl2.cli")


def format_results_json(
    results: ExecutionResults,
    module: str,
    duration: float,
) -> str:
    """Format execution results as JSON.

    Args:
        results: Execution results from module run
        module: Name of module that was executed
        duration: Execution duration in seconds

    Returns:
        JSON string with structured results
    """
    # Convert ModuleResult objects to dictionaries
    host_results: dict[str, Any] = {}
    errors_list: list[dict[str, Any]] = []

    for host_name, result in results.results.items():
        host_results[host_name] = {
            "success": result.success,
            "changed": result.changed,
            "output": result.output,
        }
        if result.error:
            host_results[host_name]["error"] = result.error
            # Include rich error context if available
            if result.error_context:
                error_dict = result.error_context.to_dict()
                error_dict["host"] = host_name
                errors_list.append(error_dict)
                host_results[host_name]["error_context"] = error_dict

    output: dict[str, Any] = {
        "module": module,
        "total_hosts": results.total_hosts,
        "successful": results.successful,
        "failed": results.failed,
        "results": host_results,
        "duration": round(duration, 3),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Add errors summary if there are any
    if errors_list:
        output["errors"] = errors_list

    # Add retry stats if available
    if results.retry_stats:
        output["retry_stats"] = results.retry_stats.to_dict()

    return json.dumps(output, indent=2)


def format_results_text(
    results: ExecutionResults,
    verbose: bool = False,
) -> str:
    """Format execution results as human-readable text.

    Args:
        results: Execution results from module run
        verbose: Whether to include detailed per-host results

    Returns:
        Formatted text string
    """
    lines = [
        "",
        "Execution Results:",
        f"Total hosts: {results.total_hosts}",
        f"Successful: {results.successful}",
        f"Failed: {results.failed}",
        "",
    ]

    if verbose and results.results:
        lines.append("Detailed Results:")
        for host_name, result in results.results.items():
            status = "OK" if result.success else "FAILED"
            changed = " (changed)" if result.changed else ""
            lines.append(f"  {host_name}: {status}{changed}")
            if result.error:
                lines.append(f"    Error: {result.error}")
            if result.output and verbose:
                for key, value in result.output.items():
                    lines.append(f"    {key}: {value}")
        lines.append("")

    # Show rich error context for failed hosts
    failed_results = [r for r in results.results.values() if not r.success and r.error_context]
    if failed_results:
        lines.append("Error Details:")
        for result in failed_results:
            lines.append("")
            lines.append(result.error_context.format_text())
        lines.append("")

    # Show retry stats if available
    if results.retry_stats and (
        results.retry_stats.succeeded_after_retry > 0 or
        results.retry_stats.failed_after_retries > 0 or
        results.retry_stats.circuit_breaker_triggered
    ):
        lines.append(results.retry_stats.format_text())
        lines.append("")

    return "\n".join(lines)


def format_dry_run_json(
    results: ExecutionResults,
    module: str,
) -> str:
    """Format dry-run results as JSON.

    Args:
        results: Dry-run results from module preview
        module: Name of module that would be executed

    Returns:
        JSON string with structured dry-run preview
    """
    host_previews: dict[str, Any] = {}
    for host_name, result in results.results.items():
        host_previews[host_name] = {
            "would_execute": result.output.get("would_execute", True),
            "module": result.output.get("module", module),
            "connection": result.output.get("connection", "unknown"),
            "args": result.output.get("args", {}),
            "preview": result.output.get("preview", ""),
        }
        # Include SSH details for remote hosts
        if result.output.get("connection") == "ssh":
            host_previews[host_name]["ssh_host"] = result.output.get("ssh_host")
            host_previews[host_name]["ssh_port"] = result.output.get("ssh_port")
            host_previews[host_name]["ssh_user"] = result.output.get("ssh_user")

    output = {
        "dry_run": True,
        "module": module,
        "total_hosts": results.total_hosts,
        "hosts": host_previews,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return json.dumps(output, indent=2)


def format_dry_run_text(
    results: ExecutionResults,
    module: str,
) -> str:
    """Format dry-run results as human-readable text.

    Args:
        results: Dry-run results from module preview
        module: Name of module that would be executed

    Returns:
        Formatted text string
    """
    lines = [
        "",
        "Dry Run Preview:",
        f"Module: {module}",
        f"Would execute on {results.total_hosts} host(s):",
        "",
    ]

    for host_name, result in results.results.items():
        connection = result.output.get("connection", "unknown")
        preview = result.output.get("preview", "No preview available")

        if connection == "ssh":
            ssh_host = result.output.get("ssh_host", "unknown")
            ssh_port = result.output.get("ssh_port", 22)
            ssh_user = result.output.get("ssh_user", "unknown")
            lines.append(f"  {host_name} ({ssh_user}@{ssh_host}:{ssh_port}):")
        else:
            lines.append(f"  {host_name} (local):")

        lines.append(f"    {preview}")

        # Show args if present
        args = result.output.get("args", {})
        if args:
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            lines.append(f"    Args: {args_str}")

        lines.append("")

    lines.append("No changes made (dry-run mode)")
    lines.append("")

    return "\n".join(lines)


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


# Module subcommand group
@cli.group()
def module() -> None:
    """Module discovery and documentation commands."""
    pass


def _get_module_dirs(module_dir: tuple[str, ...]) -> list[Path]:
    """Build list of module directories to search.

    Args:
        module_dir: User-specified module directories

    Returns:
        List of Path objects to search for modules
    """
    module_dirs = []

    # Add user-specified directories first
    for user_dir in module_dir:
        module_dirs.append(Path(user_dir))

    # Add built-in modules directory
    default_module_dir = Path(__file__).parent / "modules"
    if default_module_dir.exists():
        module_dirs.append(default_module_dir)

    return module_dirs


@module.command("list")
@click.option("--module-dir", "-M", multiple=True, help="Additional module directory to search")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def module_list(module_dir: tuple[str, ...], output_format: str) -> None:
    """List all available modules.

    Shows all modules found in module directories with their descriptions.

    Examples:
        ftl2 module list

        ftl2 module list -M ./my_modules

        ftl2 module list --format json
    """
    module_dirs = _get_module_dirs(module_dir)
    modules = discover_modules(module_dirs)

    if output_format == "json":
        output = format_module_list_json(modules)
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(format_module_list(modules))


@module.command("doc")
@click.argument("name")
@click.option("--module-dir", "-M", multiple=True, help="Additional module directory to search")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def module_doc(name: str, module_dir: tuple[str, ...], output_format: str) -> None:
    """Show documentation for a specific module.

    Displays detailed documentation including arguments, return values,
    and usage examples.

    Examples:
        ftl2 module doc ping

        ftl2 module doc file --format json

        ftl2 module doc shell -M ./my_modules
    """
    module_dirs = _get_module_dirs(module_dir)

    # Find the module
    module_path = None
    for dir_path in module_dirs:
        candidate = dir_path / f"{name}.py"
        if candidate.exists():
            module_path = candidate
            break

    if module_path is None:
        # List available modules in error message
        modules = discover_modules(module_dirs)
        available = ", ".join(m.name for m in modules)
        raise click.ClickException(
            f"Module '{name}' not found.\n"
            f"Available modules: {available}"
        )

    doc = extract_module_doc(module_path)

    if output_format == "json":
        click.echo(json.dumps(doc.to_dict(), indent=2))
    else:
        click.echo("")
        click.echo(doc.format_text())
        click.echo("")


# Vars subcommand group
@cli.group()
def vars() -> None:
    """Variable inspection and validation commands."""
    pass


@vars.command("list")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def vars_list(inventory: str, output_format: str) -> None:
    """List all hosts and their variable counts.

    Shows a summary of variables defined for each host in the inventory.

    Examples:
        ftl2 vars list -i hosts.yml

        ftl2 vars list -i hosts.yml --format json
    """
    inv = load_inventory(inventory)
    all_vars = get_all_host_variables(inv)

    if output_format == "json":
        output = format_all_hosts_json(all_vars)
        click.echo(json.dumps(output, indent=2))
    else:
        click.echo(format_all_hosts_text(all_vars))


@vars.command("show")
@click.argument("hostname")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def vars_show(hostname: str, inventory: str, output_format: str) -> None:
    """Show all variables for a specific host.

    Displays detailed variable information including:
    - Variable name and value
    - Source (host, group, or builtin)
    - Groups the host belongs to

    Examples:
        ftl2 vars show web01 -i hosts.yml

        ftl2 vars show db01 -i hosts.yml --format json
    """
    inv = load_inventory(inventory)
    all_hosts = inv.get_all_hosts()

    if hostname not in all_hosts:
        available = ", ".join(sorted(all_hosts.keys()))
        raise click.ClickException(
            f"Host '{hostname}' not found in inventory.\n"
            f"Available hosts: {available}"
        )

    host = all_hosts[hostname]
    host_vars = collect_host_variables(inv, host)

    if output_format == "json":
        click.echo(json.dumps(host_vars.to_dict(), indent=2))
    else:
        click.echo("")
        click.echo(host_vars.format_text())


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
@click.option("--module-dir", "-M", multiple=True, help="Module directory to search (can specify multiple, searched before built-ins)")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--requirements", "-r", help="Python requirements file")
@click.option("--args", "-a", help="Module arguments in key=value format")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format (default: text)")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
@click.option("--allow-destructive", is_flag=True, help="Allow execution of destructive commands")
@click.option("--parallel", "-p", type=int, default=DEFAULT_PARALLEL,
              help=f"Number of concurrent host connections (default: {DEFAULT_PARALLEL}, max: {MAX_PARALLEL})")
@click.option("--timeout", "-t", type=int, default=DEFAULT_TIMEOUT,
              help=f"Execution timeout in seconds (default: {DEFAULT_TIMEOUT})")
@click.option("--retry", type=int, default=0,
              help="Number of retry attempts for failed hosts (default: 0)")
@click.option("--retry-delay", type=float, default=5.0,
              help="Initial delay between retries in seconds (default: 5)")
@click.option("--smart-retry", is_flag=True,
              help="Only retry transient errors (connection timeout, etc.)")
@click.option("--circuit-breaker", type=float, default=0,
              help="Stop if failure percentage exceeds threshold (e.g., 30 for 30%%)")
@click.option("--debug", is_flag=True, help="Show debug logging")
@click.option("--verbose", "-v", is_flag=True, help="Show verbose logging")
def run_module(
    module: str,
    module_dir: tuple[str, ...],
    inventory: str,
    requirements: Optional[str],
    args: Optional[str],
    output_format: str,
    dry_run: bool,
    allow_destructive: bool,
    parallel: int,
    timeout: int,
    retry: int,
    retry_delay: float,
    smart_retry: bool,
    circuit_breaker: float,
    debug: bool,
    verbose: bool,
) -> None:
    """Execute a module across inventory hosts.

    Runs the specified automation module on all hosts in the inventory,
    with support for variable references, host-specific arguments, and
    remote execution via SSH.

    Safe defaults are enforced:
    - Destructive commands require --allow-destructive flag
    - Default parallel connections: 10 (max: 100)
    - Default timeout: 300 seconds (5 minutes)

    Retry options:
    - --retry N: Retry failed hosts up to N times
    - --smart-retry: Only retry transient errors (timeouts, connection issues)
    - --circuit-breaker N: Stop if N% of hosts are failing

    Examples:
        ftl2 run -m ping -i hosts.yml

        ftl2 run -m ping -i hosts.yml --format json

        ftl2 run -m file -i inventory.yml -a "path=/tmp/test state=touch"

        ftl2 run -m shell -i hosts.yml -a "cmd='uptime'" --verbose

        ftl2 run -m file -i hosts.yml -a "path=/tmp/test state=absent" --dry-run

        ftl2 run -m shell -i hosts.yml -a "cmd='rm -rf /old'" --allow-destructive

        ftl2 run -m ping -i hosts.yml --parallel 50 --timeout 600

        ftl2 run -m ping -i hosts.yml --retry 3 --smart-retry

        ftl2 run -m setup -i hosts.yml --retry 2 --circuit-breaker 30
    """
    # Configure logging
    # For JSON output, suppress logging to avoid polluting the output
    if output_format == "json":
        configure_logging(level=logging.CRITICAL)  # Only critical errors
    elif debug:
        configure_logging(level=logging.DEBUG, debug=True)
    elif verbose:
        configure_logging(level=logging.INFO)
    else:
        configure_logging(level=logging.WARNING)

    # Validate parallel connections limit
    if parallel < 1:
        raise click.ClickException("--parallel must be at least 1")
    if parallel > MAX_PARALLEL:
        raise click.ClickException(
            f"--parallel cannot exceed {MAX_PARALLEL} (requested: {parallel})\n"
            f"High parallelism can overwhelm target systems."
        )

    # Validate timeout
    if timeout < 1:
        raise click.ClickException("--timeout must be at least 1 second")

    # Parse module arguments for safety check
    parsed_args = parse_module_args(args)

    # Safety check for destructive commands
    safety_result = check_module_args_safety(module, parsed_args)

    if safety_result.blocked:
        # Blocked commands cannot be overridden
        raise click.ClickException(format_safety_error(safety_result, module))

    if not safety_result.safe and not allow_destructive:
        # Destructive commands require explicit override
        raise click.ClickException(format_safety_error(safety_result, module))

    if not safety_result.safe and allow_destructive:
        # User acknowledged the risk
        if output_format != "json":
            click.echo("Warning: Executing destructive command with --allow-destructive flag")

    # Load dependencies if requirements file specified
    dependencies = []
    if requirements:
        with open(requirements) as f:
            dependencies = [x for x in f.read().splitlines() if x]

    # Build module directories list
    # User-specified directories are searched first (higher priority)
    module_dirs = []

    # Add user-specified module directories first (searched before built-ins)
    for user_dir in module_dir:
        module_dirs.append(Path(user_dir))

    # Add default built-in modules directory last (fallback)
    default_module_dir = Path(__file__).parent / "modules"
    if default_module_dir.exists():
        module_dirs.append(default_module_dir)

    async def run_async() -> tuple[ExecutionResults, float]:
        """Inner async function to handle async operations.

        Returns:
            Tuple of (results, duration_seconds)
        """
        start_time = time.time()

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
                module_args=parsed_args,
                modules=[module],
                dependencies=dependencies,
                dry_run=dry_run,
            )

            # Create gate configuration
            gate_config = GateConfig()

            # Create execution context
            context = ExecutionContext(
                execution_config=exec_config,
                gate_config=gate_config,
            )

            # Create retry configuration
            retry_cfg = RetryConfig(
                max_attempts=retry,
                initial_delay=retry_delay,
                smart_retry=smart_retry,
            )

            # Create circuit breaker configuration
            cb_cfg = CircuitBreakerConfig(
                enabled=circuit_breaker > 0,
                threshold_percent=circuit_breaker,
            )

            # Create executor and run (parallel controls concurrent connections)
            executor = ModuleExecutor(
                chunk_size=parallel,
                retry_config=retry_cfg,
                circuit_breaker_config=cb_cfg,
            )
            try:
                with logger.scope("Module execution"):
                    results = await executor.run(inv, context)

                logger.info("Execution complete",
                           successful=results.successful,
                           failed=results.failed)

                duration = time.time() - start_time
                return results, duration

            finally:
                # Clean up resources
                logger.debug("Cleaning up resources")
                await executor.cleanup()

    # Run the async operations
    results, duration = asyncio.run(run_async())

    # Display results based on format and mode
    if dry_run:
        # Dry-run mode - show preview
        if output_format == "json":
            click.echo(format_dry_run_json(results, module))
        else:
            click.echo(format_dry_run_text(results, module))
        # Dry-run always succeeds (no actual execution)
    else:
        # Normal execution mode
        if output_format == "json":
            click.echo(format_results_json(results, module, duration))
        else:
            click.echo(format_results_text(results, verbose=verbose or debug))

        # Exit with error if any host failed
        if not results.is_success():
            # For JSON format, the error info is already in the output
            # Use exit code to indicate failure without extra message
            if output_format == "json":
                raise SystemExit(1)
            else:
                raise click.ClickException(f"{results.failed} host(s) failed execution")


def main() -> None:
    """Package entry point for the FTL2 command-line interface."""
    cli()


def entry_point() -> None:
    """Package entry point for the FTL2 command-line interface."""
    cli()


if __name__ == "__main__":
    cli()
