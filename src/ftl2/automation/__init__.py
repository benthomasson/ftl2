"""FTL2 Automation Context Manager.

Provides a clean, AI-friendly interface for automation scripts:

    import asyncio
    from ftl2.automation import automation

    async def main():
        async with automation() as ftl:
            await ftl.file(path="/tmp/test", state="directory")
            await ftl.copy(src="config.yml", dest="/etc/app/config.yml")
            response = await ftl.uri(url="https://api.example.com/health")

    asyncio.run(main())

The context manager provides:
- Clean ftl.module_name() syntax
- Automatic module discovery
- Check mode (dry-run) support
- Execution result tracking
- 250x faster than subprocess execution

This module is designed for AI-generated automation scripts where
readability and natural language patterns are important.
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from ftl2.automation.context import (
    AutomationContext,
    AutomationError,
    OutputMode,
    EventCallback,
)
from ftl2.automation.proxy import (
    ModuleProxy,
    NamespaceProxy,
    HostScopedProxy,
    HostScopedModuleProxy,
)

__all__ = [
    "automation",
    "AutomationContext",
    "AutomationError",
    "ModuleProxy",
    "NamespaceProxy",
    "HostScopedProxy",
    "HostScopedModuleProxy",
    "OutputMode",
]


@asynccontextmanager
async def automation(
    modules: list[str] | None = None,
    inventory: str | None = None,
    secrets: list[str] | None = None,
    secret_bindings: dict[str, dict[str, str]] | None = None,
    check_mode: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    on_event: EventCallback | None = None,
    fail_fast: bool = False,
    print_summary: bool = True,
    print_errors: bool = True,
    auto_install_deps: bool = False,
    record_deps: bool = False,
    deps_file: str = ".ftl2-deps.txt",
    modules_file: str = ".ftl2-modules.txt",
    gate_modules: "list[str] | str | None" = None,
    gate_subsystem: bool = False,
    state_file: str | None = None,
    record: str | None = None,
) -> AsyncGenerator[AutomationContext, None]:
    """Create an automation context for running FTL modules.

    This is the main entry point for automation scripts. It provides
    a clean interface where modules are accessed as attributes:

        async with automation() as ftl:
            await ftl.file(path="/tmp/test", state="touch")

    Args:
        modules: List of module names to enable. If None, all modules
                are available. Use this to restrict which modules can
                be called (e.g., for safety or documentation).
        inventory: Path to inventory file, or None for localhost only.
                  Enables ftl.hosts access and ftl.run_on() for remote
                  execution.
        secrets: List of environment variable names to load as secrets.
                Access via ftl.secrets["NAME"]. Values are never logged.
        secret_bindings: Automatic secret injection for modules. Maps module
                patterns to {param: env_var} bindings. Secrets are injected
                automatically so scripts never see actual values:
                {"community.general.slack": {"token": "SLACK_TOKEN"}}
        check_mode: Enable dry-run mode. Modules will report what they
                   would change without making actual changes.
        verbose: Enable verbose output showing each module execution,
                including timing information.
        quiet: Suppress all output (overrides verbose). Useful for scripts
              where you only want to check ftl.results programmatically.
        on_event: Callback for structured events. Receives dict with keys:
                 event ("module_start" or "module_complete"), module, host,
                 timestamp, and event-specific data (success, changed, duration).
        fail_fast: Stop execution on first error. Raises AutomationError
                  immediately when a module fails. Default is False (continue
                  and collect errors in ftl.errors).
        print_summary: Print per-host summary on context exit. Default is True.
                      Shows counts of changed/ok/failed tasks per host.
        print_errors: Print error summary on context exit. Default is True.
                     Set to False to handle errors manually via ftl.errors.
        auto_install_deps: Automatically install missing Python dependencies
                          using uv when an Ansible module requires packages
                          that aren't installed. Default is False.
        record_deps: Record module dependencies during execution and write
                    to deps_file on context exit. Use with auto_install_deps
                    for development to capture all needed packages.
        deps_file: Path to write recorded dependencies. Default is
                  ".ftl2-deps.txt". Only used when record_deps=True.
        modules_file: Path to write recorded module names. Default is
                     ".ftl2-modules.txt". Only used when record_deps=True.
        gate_modules: Modules to bake into the gate for remote execution.
                     Accepts a list of module names, "auto" to read from
                     modules_file (or record on first run), or None for
                     per-task module transfer (default).
        gate_subsystem: Register the gate as an SSH subsystem on remote
                       hosts. Requires root. Eliminates shell startup
                       overhead on subsequent connections. Default False.
        state_file: Path to state file for persistent host/resource tracking.
                   When enabled, add_host() persists to state file immediately,
                   and hosts are loaded from state on context enter. Enables
                   crash recovery and idempotent provisioning. Default is None.
        record: Path to JSON file for recording all actions as an audit
                trail. Written on context exit with timestamps, durations,
                parameters (excluding secrets), and results. Default is None.

    Yields:
        AutomationContext with ftl.module_name() access to all modules

    Raises:
        AutomationError: If fail_fast=True and a module fails

    Example:
        # Basic usage (localhost)
        async with automation() as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            await ftl.command(cmd="echo hello")

        # With inventory for remote execution
        async with automation(inventory="hosts.yml") as ftl:
            # Local execution
            await ftl.file(path="/tmp/test", state="touch")

            # Remote execution on hosts/groups
            await ftl.run_on("webservers", "file", path="/var/www", state="directory")
            await ftl.run_on(ftl.hosts["db01"], "command", cmd="pg_dump mydb")

        # With secrets
        async with automation(secrets=["AWS_ACCESS_KEY_ID", "API_TOKEN"]) as ftl:
            key = ftl.secrets["AWS_ACCESS_KEY_ID"]  # Get value
            if "API_TOKEN" in ftl.secrets:          # Check exists
                token = ftl.secrets["API_TOKEN"]

        # Restricted modules
        async with automation(modules=["file", "copy"]) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            await ftl.command(cmd="echo")  # Raises AttributeError

        # Check mode (dry run)
        async with automation(check_mode=True) as ftl:
            await ftl.file(path="/tmp/test", state="absent")
            # Reports what would be deleted without deleting

        # Verbose output with timing
        async with automation(verbose=True) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            # Prints: [file] ok (changed) (0.02s)

        # Quiet mode for scripts
        async with automation(quiet=True) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            # No output, check ftl.results for status

        # Event callback for custom handling
        events = []
        async with automation(on_event=events.append) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
        print(f"Collected {len(events)} events")

        # Error handling - collect and inspect
        async with automation() as ftl:
            await ftl.file(path="/nonexistent/path", state="touch")  # May fail
            await ftl.file(path="/tmp/test", state="touch")  # Still runs

            if ftl.failed:
                for error in ftl.errors:
                    print(f"Error in {error.module}: {error.error}")

        # Error handling - fail fast
        try:
            async with automation(fail_fast=True) as ftl:
                await ftl.file(path="/nonexistent/path", state="touch")
                # Raises AutomationError, stops here
        except AutomationError as e:
            print(f"Failed: {e}")

        # Secret bindings - inject secrets without script access
        async with automation(
            secret_bindings={
                "community.general.slack": {"token": "SLACK_TOKEN"},
                "amazon.aws.*": {"aws_access_key_id": "AWS_KEY"},
            }
        ) as ftl:
            # Token injected automatically - script never sees it
            await ftl.community.general.slack(channel="#deploy", msg="Done!")

    Note:
        Module execution is 250x faster than subprocess-based Ansible
        because FTL modules run in-process as Python functions.
    """
    context = AutomationContext(
        modules=modules,
        inventory=inventory,
        secrets=secrets,
        secret_bindings=secret_bindings,
        check_mode=check_mode,
        verbose=verbose,
        quiet=quiet,
        on_event=on_event,
        fail_fast=fail_fast,
        print_summary=print_summary,
        print_errors=print_errors,
        auto_install_deps=auto_install_deps,
        record_deps=record_deps,
        deps_file=deps_file,
        modules_file=modules_file,
        gate_modules=gate_modules,
        gate_subsystem=gate_subsystem,
        state_file=state_file,
        record=record,
    )

    try:
        async with context:
            yield context
    finally:
        # Any additional cleanup would go here
        pass
