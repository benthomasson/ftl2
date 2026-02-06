"""FTL2 - Refactored Faster Than Light automation framework.

A high-performance automation framework built with modern Python patterns,
using dataclasses and composition for clean architecture that's portable to Go.

Quick Start:
    from ftl2 import automation

    async with automation() as ftl:
        await ftl.file(path="/tmp/test", state="touch")
        await ftl.command(cmd="echo hello")
"""

__version__ = "0.1.0"

from ftl2.automation import automation, AutomationContext

__all__ = ["__version__", "automation", "AutomationContext"]
