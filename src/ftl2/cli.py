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
from ftl2.logging import (
    configure_logging,
    get_logger,
    get_level_from_verbosity,
    get_level_from_name,
    TRACE,
)
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
from ftl2.state import (
    ExecutionState,
    load_state,
    save_state,
    create_state_from_results,
    filter_hosts_for_resume,
)
from ftl2.progress import create_progress_reporter
from ftl2.workflow import (
    Workflow,
    WorkflowStep,
    load_workflow,
    save_workflow,
    list_workflows,
    delete_workflow,
    add_step_to_workflow,
)
from ftl2.host_filter import (
    filter_hosts,
    get_group_hosts_mapping,
    format_filter_summary,
)
from ftl2.config_profiles import (
    ConfigProfile,
    load_profile,
    save_profile,
    list_profiles,
    delete_profile,
)
from ftl2.backup import (
    BackupManager,
    list_backups,
    restore_backup,
    prune_backups,
    delete_backup,
    format_backup_list_text,
    format_backup_list_json,
    determine_operation,
)
from ftl2.module_docs import BackupMetadata
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


def format_explain_text(
    module: str,
    inventory_file: str,
    hosts: dict[str, Any],
    module_path: Path | None,
    parallel: int,
    timeout: int,
    retry: int,
    args: dict[str, Any],
) -> str:
    """Format execution plan as human-readable text.

    Args:
        module: Module name
        inventory_file: Path to inventory file
        hosts: Dictionary of hosts
        module_path: Path to module file
        parallel: Parallel connection limit
        timeout: Execution timeout
        retry: Retry count
        args: Module arguments

    Returns:
        Formatted execution plan
    """
    lines = [
        "",
        "Execution Plan:",
        "=" * 50,
        "",
    ]

    # Step 1: Load inventory
    lines.append(f"  1. Load inventory from {inventory_file}")
    lines.append(f"     - {len(hosts)} host(s) found")
    lines.append("")

    # Step 2: Resolve module
    lines.append(f"  2. Resolve module '{module}'")
    if module_path:
        lines.append(f"     - Path: {module_path}")
    else:
        lines.append(f"     - Using built-in module")
    lines.append("")

    # Step 3: Build gate
    lines.append("  3. Build gate executable")
    lines.append("     - Package module and dependencies into pyz archive")
    lines.append("     - Cache for reuse if unchanged")
    lines.append("")

    # Step 4: Connect
    local_hosts = [h for h, host in hosts.items() if host.ansible_connection == "local"]
    ssh_hosts = [h for h, host in hosts.items() if host.ansible_connection == "ssh"]

    lines.append(f"  4. Connect to hosts (parallel: {parallel})")
    if local_hosts:
        lines.append(f"     - Local: {len(local_hosts)} host(s)")
    if ssh_hosts:
        lines.append(f"     - SSH: {len(ssh_hosts)} host(s)")
    lines.append("")

    # Step 5: Upload gate
    if ssh_hosts:
        lines.append("  5. Upload gate to remote hosts")
        lines.append("     - Target: /tmp/ftl_gate_<hash>.pyz")
        lines.append("")

    # Step 6: Execute
    step_num = 6 if ssh_hosts else 5
    lines.append(f"  {step_num}. Execute module '{module}' on each host")
    if args:
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        lines.append(f"     - Args: {args_str}")
    lines.append(f"     - Timeout: {timeout}s per host")
    if retry > 0:
        lines.append(f"     - Retry: up to {retry} times on failure")
    lines.append("")

    # Step 7: Collect results
    step_num += 1
    lines.append(f"  {step_num}. Collect results and close connections")
    lines.append("")

    # Host summary
    lines.append("Hosts:")
    lines.append("-" * 30)
    for host_name, host in sorted(hosts.items()):
        conn = host.ansible_connection
        if conn == "local":
            lines.append(f"  - {host_name} (local)")
        else:
            addr = host.ansible_host
            port = host.ansible_port
            user = host.ansible_user
            lines.append(f"  - {host_name} ({user}@{addr}:{port})")

    lines.append("")
    lines.append("This is an explanation only. No changes will be made.")
    lines.append("")

    return "\n".join(lines)


def format_explain_json(
    module: str,
    inventory_file: str,
    hosts: dict[str, Any],
    module_path: Path | None,
    parallel: int,
    timeout: int,
    retry: int,
    args: dict[str, Any],
) -> str:
    """Format execution plan as JSON.

    Args:
        module: Module name
        inventory_file: Path to inventory file
        hosts: Dictionary of hosts
        module_path: Path to module file
        parallel: Parallel connection limit
        timeout: Execution timeout
        retry: Retry count
        args: Module arguments

    Returns:
        JSON string with execution plan
    """
    local_hosts = [h for h in hosts if hosts[h].ansible_connection == "local"]
    ssh_hosts = [h for h in hosts if hosts[h].ansible_connection == "ssh"]

    steps = [
        {"step": 1, "action": "load_inventory", "file": inventory_file, "hosts": len(hosts)},
        {"step": 2, "action": "resolve_module", "module": module, "path": str(module_path) if module_path else None},
        {"step": 3, "action": "build_gate", "description": "Package module into pyz archive"},
        {"step": 4, "action": "connect", "parallel": parallel, "local": len(local_hosts), "ssh": len(ssh_hosts)},
    ]

    step_num = 5
    if ssh_hosts:
        steps.append({"step": step_num, "action": "upload_gate", "target": "/tmp/ftl_gate_<hash>.pyz"})
        step_num += 1

    steps.append({
        "step": step_num,
        "action": "execute_module",
        "module": module,
        "args": args,
        "timeout": timeout,
        "retry": retry,
    })
    step_num += 1

    steps.append({"step": step_num, "action": "collect_results"})

    host_details = []
    for host_name, host in sorted(hosts.items()):
        detail = {
            "name": host_name,
            "connection": host.ansible_connection,
        }
        if host.ansible_connection == "ssh":
            detail["host"] = host.ansible_host
            detail["port"] = host.ansible_port
            detail["user"] = host.ansible_user
        host_details.append(detail)

    output = {
        "explain": True,
        "module": module,
        "inventory_file": inventory_file,
        "total_hosts": len(hosts),
        "parallel": parallel,
        "timeout": timeout,
        "retry": retry,
        "args": args,
        "steps": steps,
        "hosts": host_details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return json.dumps(output, indent=2)


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


# Workflow subcommand group
@cli.group()
def workflow() -> None:
    """Workflow tracking commands.

    Track and manage multi-step execution workflows.
    """
    pass


@workflow.command("list")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def workflow_list(output_format: str) -> None:
    """List all tracked workflows.

    Examples:
        ftl2 workflow list

        ftl2 workflow list --format json
    """
    workflows = list_workflows()

    if not workflows:
        click.echo("No workflows found.")
        return

    if output_format == "json":
        click.echo(json.dumps({"workflows": workflows}, indent=2))
    else:
        click.echo("\nWorkflows:")
        click.echo("-" * 30)
        for wf_id in workflows:
            wf = load_workflow(wf_id)
            if wf:
                click.echo(f"  {wf_id} ({len(wf.steps)} steps)")
            else:
                click.echo(f"  {wf_id}")
        click.echo(f"\nTotal: {len(workflows)} workflow(s)")
        click.echo("")


@workflow.command("show")
@click.argument("workflow_id")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def workflow_show(workflow_id: str, output_format: str) -> None:
    """Show workflow details and report.

    Examples:
        ftl2 workflow show deploy-2026-02-05

        ftl2 workflow show deploy-2026-02-05 --format json
    """
    wf = load_workflow(workflow_id)

    if wf is None:
        raise click.ClickException(f"Workflow not found: {workflow_id}")

    if output_format == "json":
        click.echo(json.dumps(wf.to_dict(), indent=2))
    else:
        click.echo(wf.format_report())


@workflow.command("delete")
@click.argument("workflow_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def workflow_delete(workflow_id: str, yes: bool) -> None:
    """Delete a workflow.

    Examples:
        ftl2 workflow delete deploy-2026-02-05

        ftl2 workflow delete deploy-2026-02-05 -y
    """
    wf = load_workflow(workflow_id)

    if wf is None:
        raise click.ClickException(f"Workflow not found: {workflow_id}")

    if not yes:
        click.confirm(
            f"Delete workflow '{workflow_id}' with {len(wf.steps)} step(s)?",
            abort=True
        )

    if delete_workflow(workflow_id):
        click.echo(f"Workflow '{workflow_id}' deleted.")
    else:
        raise click.ClickException(f"Failed to delete workflow: {workflow_id}")


# Backup subcommand group
@cli.group()
def backup() -> None:
    """Backup management commands.

    List, restore, and manage file backups.
    """
    pass


@backup.command("list")
@click.argument("path", required=False)
@click.option("--backup-dir", type=click.Path(), help="Central backup directory to search")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def backup_list(path: Optional[str], backup_dir: Optional[str], output_format: str) -> None:
    """List backups for a path or all backups.

    Examples:
        ftl2 backup list

        ftl2 backup list /etc/app.conf

        ftl2 backup list --backup-dir ~/.ftl2/backups

        ftl2 backup list --format json
    """
    backup_dir_path = Path(backup_dir) if backup_dir else None
    backups = list_backups(path, backup_dir_path)

    if output_format == "json":
        click.echo(json.dumps(format_backup_list_json(backups), indent=2))
    else:
        click.echo(format_backup_list_text(backups))


@backup.command("restore")
@click.argument("backup_path")
@click.option("--force", is_flag=True, help="Overwrite existing file")
@click.option("--dry-run", is_flag=True, help="Show what would be restored")
def backup_restore(backup_path: str, force: bool, dry_run: bool) -> None:
    """Restore a file from backup.

    Examples:
        ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500

        ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500 --force

        ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500 --dry-run
    """
    from ftl2.backup import get_original_path

    if dry_run:
        original = get_original_path(backup_path)
        original_exists = Path(original).exists()
        click.echo(f"Would restore: {backup_path}")
        click.echo(f"         To: {original}")
        if original_exists:
            click.echo(f"Note: Target exists (use --force to overwrite)")
        return

    result = restore_backup(backup_path, force=force)

    if result.success:
        click.echo(f"Restored: {result.backup} -> {result.original}")
    else:
        raise click.ClickException(f"Restore failed: {result.error}")


@backup.command("delete")
@click.argument("backup_path")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def backup_delete(backup_path: str, yes: bool) -> None:
    """Delete a backup file.

    Examples:
        ftl2 backup delete /etc/app.conf.ftl2-backup-20260205-113500

        ftl2 backup delete /etc/app.conf.ftl2-backup-20260205-113500 -y
    """
    if not Path(backup_path).exists():
        raise click.ClickException(f"Backup not found: {backup_path}")

    if not yes:
        click.confirm(f"Delete backup: {backup_path}?", abort=True)

    if delete_backup(backup_path):
        click.echo(f"Deleted: {backup_path}")
    else:
        raise click.ClickException(f"Failed to delete: {backup_path}")


@backup.command("prune")
@click.option("--path", "-p", help="Only prune backups for this path")
@click.option("--keep", "-k", type=int, help="Keep N most recent backups per file")
@click.option("--older-than", type=int, help="Delete backups older than N days")
@click.option("--backup-dir", type=click.Path(), help="Central backup directory")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def backup_prune(
    path: Optional[str],
    keep: Optional[int],
    older_than: Optional[int],
    backup_dir: Optional[str],
    dry_run: bool,
    yes: bool,
) -> None:
    """Prune old backups.

    Examples:
        ftl2 backup prune --keep 3

        ftl2 backup prune --older-than 7

        ftl2 backup prune --path /etc/app.conf --keep 2

        ftl2 backup prune --older-than 30 --dry-run
    """
    if keep is None and older_than is None:
        raise click.ClickException("Specify --keep or --older-than")

    backup_dir_path = Path(backup_dir) if backup_dir else None

    if dry_run:
        # Show what would be deleted
        backups = list_backups(path, backup_dir_path)
        from datetime import datetime, timedelta

        cutoff = None
        if older_than:
            cutoff = datetime.now() - timedelta(days=older_than)

        # Group by original
        by_original: dict[str, list] = {}
        for b in backups:
            if b.original not in by_original:
                by_original[b.original] = []
            by_original[b.original].append(b)

        to_delete = []
        for orig, orig_backups in by_original.items():
            orig_backups.sort(key=lambda b: b.timestamp, reverse=True)
            for i, b in enumerate(orig_backups):
                if keep is not None and i >= keep:
                    to_delete.append(b)
                elif cutoff is not None and b.timestamp < cutoff:
                    to_delete.append(b)

        if not to_delete:
            click.echo("No backups would be deleted.")
            return

        click.echo(f"Would delete {len(to_delete)} backup(s):")
        for b in to_delete:
            click.echo(f"  - {b.backup}")
        return

    if not yes:
        click.confirm("Prune backups?", abort=True)

    deleted = prune_backups(path, keep, older_than, backup_dir_path)

    if deleted:
        click.echo(f"Deleted {len(deleted)} backup(s)")
    else:
        click.echo("No backups deleted")


# Config subcommand group
@cli.group()
def config() -> None:
    """Configuration profile management.

    Save and reuse common execution configurations.
    """
    pass


@config.command("save")
@click.argument("name")
@click.option("--module", "-m", required=True, help="Module to execute")
@click.option("--args", "-a", help="Module arguments in key=value format")
@click.option("--description", "-d", help="Profile description")
@click.option("--parallel", "-p", type=int, help="Number of concurrent connections")
@click.option("--timeout", "-t", type=int, help="Execution timeout in seconds")
@click.option("--retry", type=int, help="Number of retry attempts")
@click.option("--retry-delay", type=float, help="Delay between retries")
@click.option("--smart-retry", is_flag=True, default=None, help="Only retry transient errors")
@click.option("--circuit-breaker", type=float, help="Circuit breaker threshold")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              help="Output format")
@click.option("--allow-destructive", is_flag=True, default=None, help="Allow destructive commands")
def config_save(
    name: str,
    module: str,
    args: Optional[str],
    description: Optional[str],
    parallel: Optional[int],
    timeout: Optional[int],
    retry: Optional[int],
    retry_delay: Optional[float],
    smart_retry: Optional[bool],
    circuit_breaker: Optional[float],
    output_format: Optional[str],
    allow_destructive: Optional[bool],
) -> None:
    """Save a configuration profile.

    Create a reusable configuration with common options.
    Use template variables with {{var_name}} syntax in arguments.

    Examples:
        ftl2 config save web-deploy -m copy -a "src=app.tgz dest=/opt/"

        ftl2 config save db-backup -m shell -a "cmd='pg_dump db'" --parallel 1

        ftl2 config save deploy-template -m copy -a "src={{app_path}} dest={{dest}}"
    """
    parsed_args = parse_module_args(args)

    profile = ConfigProfile(
        name=name,
        module=module,
        args=parsed_args,
        description=description or "",
        parallel=parallel,
        timeout=timeout,
        retry=retry,
        retry_delay=retry_delay,
        smart_retry=smart_retry if smart_retry else None,
        circuit_breaker=circuit_breaker,
        format=output_format,
        allow_destructive=allow_destructive if allow_destructive else None,
    )

    path = save_profile(profile)
    click.echo(f"Profile '{name}' saved to {path}")

    # Show template variables if any
    template_vars = profile.get_template_variables()
    if template_vars:
        click.echo(f"Template variables: {', '.join(template_vars)}")
        click.echo("Use --var name=value when running this profile")


@config.command("list")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def config_list(output_format: str) -> None:
    """List all saved configuration profiles.

    Examples:
        ftl2 config list

        ftl2 config list --format json
    """
    profiles = list_profiles()

    if not profiles:
        click.echo("No profiles found.")
        return

    if output_format == "json":
        click.echo(json.dumps({"profiles": profiles}, indent=2))
    else:
        click.echo("\nSaved Profiles:")
        click.echo("-" * 30)
        for name in profiles:
            profile = load_profile(name)
            if profile:
                desc = f" - {profile.description}" if profile.description else ""
                click.echo(f"  {name} ({profile.module}){desc}")
            else:
                click.echo(f"  {name}")
        click.echo(f"\nTotal: {len(profiles)} profile(s)")
        click.echo("")


@config.command("show")
@click.argument("name")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              default="text", help="Output format")
def config_show(name: str, output_format: str) -> None:
    """Show details of a configuration profile.

    Examples:
        ftl2 config show web-deploy

        ftl2 config show web-deploy --format json
    """
    profile = load_profile(name)

    if profile is None:
        raise click.ClickException(f"Profile not found: {name}")

    if output_format == "json":
        click.echo(json.dumps(profile.to_dict(), indent=2))
    else:
        click.echo("")
        click.echo(profile.format_text())
        template_vars = profile.get_template_variables()
        if template_vars:
            click.echo(f"\nTemplate variables: {', '.join(template_vars)}")
        click.echo("")


@config.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def config_delete(name: str, yes: bool) -> None:
    """Delete a configuration profile.

    Examples:
        ftl2 config delete web-deploy

        ftl2 config delete web-deploy -y
    """
    profile = load_profile(name)

    if profile is None:
        raise click.ClickException(f"Profile not found: {name}")

    if not yes:
        click.confirm(f"Delete profile '{name}'?", abort=True)

    if delete_profile(name):
        click.echo(f"Profile '{name}' deleted.")
    else:
        raise click.ClickException(f"Failed to delete profile: {name}")


@config.command("run")
@click.argument("name")
@click.option("--inventory", "-i", required=True, help="Inventory file (YAML format)")
@click.option("--var", "-v", multiple=True, help="Template variable (name=value)")
@click.option("--limit", "-l", type=str, help="Limit execution to matching hosts")
@click.option("--format", "-f", "output_format", type=click.Choice(["text", "json"]),
              help="Override output format")
def config_run(
    name: str,
    inventory: str,
    var: tuple[str, ...],
    limit: Optional[str],
    output_format: Optional[str],
) -> None:
    """Run a saved configuration profile.

    Executes a module with saved configuration options.
    Use --var to substitute template variables.

    Examples:
        ftl2 config run web-deploy -i hosts.yml

        ftl2 config run deploy-template -i hosts.yml --var app_path=/local/app --var dest=/opt/

        ftl2 config run web-deploy -i hosts.yml --limit web01
    """
    profile = load_profile(name)

    if profile is None:
        raise click.ClickException(f"Profile not found: {name}")

    # Parse template variables
    variables: dict[str, str] = {}
    for v in var:
        if "=" not in v:
            raise click.ClickException(f"Invalid variable format: {v}. Expected name=value")
        var_name, var_value = v.split("=", 1)
        variables[var_name] = var_value

    # Check for missing template variables
    template_vars = profile.get_template_variables()
    missing = [v for v in template_vars if v not in variables]
    if missing:
        raise click.ClickException(
            f"Missing template variables: {', '.join(missing)}\n"
            f"Use --var {missing[0]}=value to provide them"
        )

    # Apply template variables to arguments
    args = profile.apply_args_with_vars(variables)
    args_str = " ".join(f"{k}={v}" for k, v in args.items())

    # Build command arguments
    cmd_args = ["run", "-m", profile.module, "-i", inventory]

    if args_str:
        cmd_args.extend(["-a", args_str])

    if limit:
        cmd_args.extend(["--limit", limit])

    # Apply saved options
    fmt = output_format or profile.format
    if fmt:
        cmd_args.extend(["--format", fmt])

    if profile.parallel is not None:
        cmd_args.extend(["--parallel", str(profile.parallel)])

    if profile.timeout is not None:
        cmd_args.extend(["--timeout", str(profile.timeout)])

    if profile.retry is not None:
        cmd_args.extend(["--retry", str(profile.retry)])

    if profile.retry_delay is not None:
        cmd_args.extend(["--retry-delay", str(profile.retry_delay)])

    if profile.smart_retry:
        cmd_args.append("--smart-retry")

    if profile.circuit_breaker is not None:
        cmd_args.extend(["--circuit-breaker", str(profile.circuit_breaker)])

    if profile.allow_destructive:
        cmd_args.append("--allow-destructive")

    # Execute by invoking the run command
    click.echo(f"Running profile '{name}' with module '{profile.module}'")
    ctx = click.get_current_context()
    ctx.invoke(cli, args=cmd_args)


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
@click.option("--state-file", type=click.Path(), default=None,
              help="Save execution state to file for resume capability")
@click.option("--resume", type=click.Path(exists=True), default=None,
              help="Resume from previous state file, skipping succeeded hosts")
@click.option("--log-file", type=click.Path(), default=None,
              help="Write logs to file (in addition to console)")
@click.option("--log-level", type=click.Choice(["trace", "debug", "info", "warning", "error"]),
              default=None, help="Set log level explicitly (overrides -v)")
@click.option("-v", "--verbose", count=True,
              help="Increase verbosity: -v=info, -vv=debug, -vvv=trace")
@click.option("--explain", is_flag=True,
              help="Show execution plan without running anything")
@click.option("--progress", is_flag=True,
              help="Show real-time progress as hosts complete")
@click.option("--workflow-id", type=str, default=None,
              help="Track this execution as part of a workflow")
@click.option("--step", type=str, default=None,
              help="Name/label for this workflow step (requires --workflow-id)")
@click.option("--limit", "-l", type=str, default=None,
              help="Limit execution to matching hosts (patterns: web*,!db*,@group)")
@click.option("--save-results", type=click.Path(), default=None,
              help="Save execution results to file for later use")
@click.option("--retry-failed", type=click.Path(exists=True), default=None,
              help="Retry only hosts that failed in a previous results file")
@click.option("--no-backup", is_flag=True,
              help="Skip automatic backups before destructive operations")
@click.option("--backup-dir", type=click.Path(), default=None,
              help="Central directory for backups (default: adjacent to original)")
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
    state_file: Optional[str],
    resume: Optional[str],
    log_file: Optional[str],
    log_level: Optional[str],
    verbose: int,
    explain: bool,
    progress: bool,
    workflow_id: Optional[str],
    step: Optional[str],
    limit: Optional[str],
    save_results: Optional[str],
    retry_failed: Optional[str],
    no_backup: bool,
    backup_dir: Optional[str],
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

    State tracking:
    - --state-file FILE: Save results for later resume
    - --resume FILE: Resume from previous run, skip succeeded hosts

    Logging options:
    - -v: Info level (show progress)
    - -vv: Debug level (show details)
    - -vvv: Trace level (show SSH commands, full details)
    - --log-file FILE: Also write logs to file
    - --log-level LEVEL: Set level explicitly (trace, debug, info, warning, error)

    Preview options:
    - --dry-run: Show what modules would do without executing
    - --explain: Show step-by-step execution plan
    - --progress: Show real-time progress as hosts complete

    Workflow tracking:
    - --workflow-id ID: Track execution as part of a workflow
    - --step NAME: Label for this step (defaults to module name)

    Host filtering:
    - --limit PATTERN: Filter hosts (web*,!db*,@webservers)
    - --retry-failed FILE: Retry hosts that failed in previous results

    Integration:
    - --save-results FILE: Save results for later use

    Backup options:
    - --no-backup: Skip automatic backups before destructive changes
    - --backup-dir DIR: Store backups in central directory

    Examples:
        ftl2 run -m ping -i hosts.yml

        ftl2 run -m ping -i hosts.yml --format json

        ftl2 run -m file -i inventory.yml -a "path=/tmp/test state=touch"

        ftl2 run -m shell -i hosts.yml -a "cmd='uptime'" -v

        ftl2 run -m ping -i hosts.yml -vv

        ftl2 run -m ping -i hosts.yml -vvv --log-file /tmp/ftl2.log

        ftl2 run -m setup -i hosts.yml --log-file /tmp/debug.log --log-level debug

        ftl2 run -m file -i hosts.yml -a "path=/tmp/test state=absent" --dry-run

        ftl2 run -m ping -i hosts.yml --explain

        ftl2 run -m setup -i hosts.yml --explain --format json

        ftl2 run -m ping -i hosts.yml --progress

        ftl2 run -m setup -i hosts.yml --progress --format json

        ftl2 run -m shell -i hosts.yml -a "cmd='rm -rf /old'" --allow-destructive

        ftl2 run -m ping -i hosts.yml --parallel 50 --timeout 600

        ftl2 run -m ping -i hosts.yml --retry 3 --smart-retry

        ftl2 run -m setup -i hosts.yml --retry 2 --circuit-breaker 30

        ftl2 run -m copy -i hosts.yml -a "src=app.tgz dest=/opt/" --state-file /tmp/deploy.json

        ftl2 run -m copy -i hosts.yml -a "src=app.tgz dest=/opt/" --resume /tmp/deploy.json

        ftl2 run -m setup -i hosts.yml --workflow-id deploy-2026-02-05 --step 1-gather-facts

        ftl2 run -m copy -i hosts.yml --workflow-id deploy-2026-02-05 --step 2-deploy

        ftl2 run -m ping -i hosts.yml --limit web01,web02

        ftl2 run -m ping -i hosts.yml --limit "web*"

        ftl2 run -m ping -i hosts.yml --limit "!db*"

        ftl2 run -m setup -i hosts.yml --limit @webservers

        ftl2 run -m ping -i hosts.yml --save-results /tmp/ping-results.json

        ftl2 run -m setup -i hosts.yml --retry-failed /tmp/ping-results.json

        ftl2 run -m file -i hosts.yml -a "path=/etc/app.conf state=absent" --no-backup

        ftl2 run -m copy -i hosts.yml -a "src=app.conf dest=/etc/" --backup-dir /var/ftl2/backups
    """
    # Configure logging
    # Determine log level from options
    if log_level:
        level = get_level_from_name(log_level)
    else:
        level = get_level_from_verbosity(verbose)

    # For JSON output, suppress console logging to avoid polluting the output
    # But still allow file logging if specified
    if output_format == "json":
        console_level = logging.CRITICAL
    else:
        console_level = level

    # Configure logging with file support
    configure_logging(
        level=console_level,
        log_file=log_file,
        file_level=level if log_file else None,
        debug=(level <= logging.DEBUG),
    )

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

    # Handle explain mode - show execution plan without running
    if explain:
        # Load inventory for explain output
        try:
            inv = load_inventory(inventory)
        except ValueError as e:
            raise click.ClickException(str(e))

        hosts = inv.get_all_hosts()

        # Find module path
        module_path = None
        for mod_dir in module_dirs:
            candidate = mod_dir / f"{module}.py"
            if candidate.exists():
                module_path = candidate
                break

        parsed_args = parse_module_args(args)

        if output_format == "json":
            click.echo(format_explain_json(
                module=module,
                inventory_file=inventory,
                hosts=hosts,
                module_path=module_path,
                parallel=parallel,
                timeout=timeout,
                retry=retry,
                args=parsed_args,
            ))
        else:
            click.echo(format_explain_text(
                module=module,
                inventory_file=inventory,
                hosts=hosts,
                module_path=module_path,
                parallel=parallel,
                timeout=timeout,
                retry=retry,
                args=parsed_args,
            ))
        return

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
            original_host_count = len(inv.get_all_hosts())
            logger.info("Inventory loaded", hosts=original_host_count)

            # Handle --retry-failed: load failed hosts from previous results
            retry_failed_hosts: set[str] | None = None
            if retry_failed:
                try:
                    with open(retry_failed) as f:
                        previous_results = json.load(f)
                    retry_failed_hosts = set()
                    for host_name, result in previous_results.get("results", {}).items():
                        if not result.get("success", True):
                            retry_failed_hosts.add(host_name)
                    if not retry_failed_hosts:
                        click.echo("No failed hosts found in previous results. Nothing to retry.")
                        return ExecutionResults(), 0.0
                    if output_format != "json":
                        click.echo(f"Retrying {len(retry_failed_hosts)} failed host(s) from previous run")
                    logger.info("Retry-failed mode", hosts=len(retry_failed_hosts))
                except (json.JSONDecodeError, KeyError) as e:
                    raise click.ClickException(f"Failed to parse results file: {e}")

            # Handle --limit: filter hosts by pattern
            if limit or retry_failed_hosts:
                all_hosts = inv.get_all_hosts()
                group_hosts = get_group_hosts_mapping(inv)

                # Apply limit pattern if specified
                if limit:
                    filtered = filter_hosts(all_hosts, limit, group_hosts)
                else:
                    filtered = all_hosts

                # Further filter by retry-failed hosts if specified
                if retry_failed_hosts:
                    filtered = {
                        name: host for name, host in filtered.items()
                        if name in retry_failed_hosts
                    }

                if not filtered:
                    if limit and retry_failed_hosts:
                        click.echo(f"No hosts match both limit '{limit}' and retry-failed criteria")
                    elif limit:
                        click.echo(f"No hosts match limit pattern: {limit}")
                    else:
                        click.echo("No hosts to retry")
                    return ExecutionResults(), 0.0

                # Update inventory to only include filtered hosts
                for group in inv.list_groups():
                    group.hosts = {
                        name: host for name, host in group.hosts.items()
                        if name in filtered
                    }
                inv._invalidate_cache()

                if output_format != "json" and limit:
                    click.echo(format_filter_summary(original_host_count, len(filtered), limit))

                logger.info("Host filtering applied",
                           original=original_host_count,
                           filtered=len(filtered))

            # Handle resume mode - filter out already-succeeded hosts
            previous_state = None
            skipped_hosts: set[str] = set()
            if resume:
                previous_state = load_state(resume)
                if previous_state:
                    all_host_names = set(inv.get_all_hosts().keys())
                    hosts_to_run, skipped_hosts, new_hosts = filter_hosts_for_resume(
                        all_host_names, previous_state
                    )

                    if output_format != "json":
                        click.echo(previous_state.format_resume_summary(all_host_names))

                    if not hosts_to_run:
                        click.echo("All hosts already succeeded. Nothing to do.")
                        return ExecutionResults(), 0.0

                    # Filter inventory to only run on needed hosts
                    # We'll do this by removing succeeded hosts from groups
                    for group in inv.list_groups():
                        group.hosts = {
                            name: host for name, host in group.hosts.items()
                            if name in hosts_to_run
                        }
                    inv._invalidate_cache()

                    logger.info(
                        f"Resume mode: running on {len(hosts_to_run)} hosts, "
                        f"skipping {len(skipped_hosts)} succeeded hosts"
                    )

            # Validate execution requirements (fail-fast)
            logger.debug("Validating execution requirements")
            validate_execution_requirements(inv, module, module_dirs)
            logger.debug("Validation passed")

            # Check module backup capability
            backup_manager = None
            backup_metadata = None
            module_path = None
            for mod_dir in module_dirs:
                candidate = mod_dir / f"{module}.py"
                if candidate.exists():
                    module_path = candidate
                    break

            if module_path and not no_backup and not dry_run:
                from ftl2.module_docs import extract_module_doc
                module_doc = extract_module_doc(module_path)
                backup_metadata = module_doc.backup

                if backup_metadata.capable:
                    # Determine operation type from args
                    operation = determine_operation(module, parsed_args)

                    if backup_manager is None:
                        backup_dir_path = Path(backup_dir) if backup_dir else None
                        backup_manager = BackupManager(
                            backup_dir=backup_dir_path,
                            enabled=True,
                        )

                    if backup_manager.should_backup(
                        backup_metadata.capable,
                        backup_metadata.triggers,
                        operation,
                    ):
                        # Discover paths that need backup
                        backup_paths = backup_manager.discover_backup_paths(
                            parsed_args,
                            backup_metadata.paths,
                            operation,
                        )

                        # Create backups for existing paths
                        if backup_paths:
                            existing_paths = [p for p in backup_paths if p.exists]
                            if existing_paths:
                                if output_format != "json":
                                    click.echo("\nBacking up files before execution:")
                                    for bp in existing_paths:
                                        click.echo(f"  {bp.path}")

                                backup_results = backup_manager.create_backups(backup_paths)
                                successful_backups = [b for b in backup_results if b.success]
                                failed_backups = [b for b in backup_results if not b.success]

                                if failed_backups:
                                    for fb in failed_backups:
                                        logger.error(f"Backup failed for {fb.original}: {fb.error}")
                                    raise click.ClickException(
                                        f"Backup failed for {len(failed_backups)} file(s). "
                                        f"Use --no-backup to skip backups."
                                    )

                                if output_format != "json" and successful_backups:
                                    for sb in successful_backups:
                                        click.echo(f"  -> {sb.backup}")
                                    click.echo("")

                                logger.info(
                                    f"Created {len(successful_backups)} backup(s)",
                                    backups=[b.backup for b in successful_backups]
                                )

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

            # Create progress reporter
            progress_reporter = create_progress_reporter(
                enabled=progress,
                json_format=(output_format == "json"),
            )

            # Create executor and run (parallel controls concurrent connections)
            executor = ModuleExecutor(
                chunk_size=parallel,
                retry_config=retry_cfg,
                circuit_breaker_config=cb_cfg,
                progress_reporter=progress_reporter,
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

    # Save state if state-file specified (not for dry-run)
    if state_file and not dry_run and results.results:
        exec_state = create_state_from_results(
            results, module, parsed_args, inventory
        )
        save_state(exec_state, state_file)
        if output_format != "json":
            click.echo(f"State saved to {state_file}")

    # Track workflow if workflow-id specified (not for dry-run)
    if workflow_id and not dry_run and results.results:
        step_name = step or module  # Default step name to module name
        failed_hosts = [
            host for host, result in results.results.items()
            if not result.success
        ]
        workflow_step = WorkflowStep(
            step_name=step_name,
            module=module,
            args=parsed_args,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration=duration,
            total_hosts=results.total_hosts,
            successful=results.successful,
            failed=results.failed,
            failed_hosts=failed_hosts,
        )
        workflow = add_step_to_workflow(workflow_id, workflow_step)
        if output_format != "json":
            click.echo(f"Workflow step '{step_name}' added to workflow '{workflow_id}'")

    # Save results if --save-results specified (not for dry-run)
    if save_results and not dry_run and results.results:
        results_output = format_results_json(results, module, duration)
        with open(save_results, "w") as f:
            f.write(results_output)
        if output_format != "json":
            click.echo(f"Results saved to {save_results}")

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
            click.echo(format_results_text(results, verbose=(verbose > 0)))

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
