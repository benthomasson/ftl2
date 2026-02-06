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
from typing import AsyncGenerator

from ftl2.automation.context import AutomationContext
from ftl2.automation.proxy import ModuleProxy

__all__ = [
    "automation",
    "AutomationContext",
    "ModuleProxy",
]


@asynccontextmanager
async def automation(
    modules: list[str] | None = None,
    check_mode: bool = False,
    verbose: bool = False,
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
        check_mode: Enable dry-run mode. Modules will report what they
                   would change without making actual changes.
        verbose: Enable verbose output showing each module execution.

    Yields:
        AutomationContext with ftl.module_name() access to all modules

    Example:
        # Basic usage
        async with automation() as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            await ftl.command(cmd="echo hello")

        # Restricted modules
        async with automation(modules=["file", "copy"]) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            await ftl.command(cmd="echo")  # Raises AttributeError

        # Check mode (dry run)
        async with automation(check_mode=True) as ftl:
            await ftl.file(path="/tmp/test", state="absent")
            # Reports what would be deleted without deleting

        # Verbose output
        async with automation(verbose=True) as ftl:
            await ftl.file(path="/tmp/test", state="touch")
            # Prints: [file] ok (changed)

    Note:
        Module execution is 250x faster than subprocess-based Ansible
        because FTL modules run in-process as Python functions.
    """
    context = AutomationContext(
        modules=modules,
        check_mode=check_mode,
        verbose=verbose,
    )

    try:
        async with context:
            yield context
    finally:
        # Any additional cleanup would go here
        pass
