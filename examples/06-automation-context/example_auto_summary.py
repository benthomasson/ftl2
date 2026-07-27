#!/usr/bin/env python3
"""Example: Automatic result collection and summary.

Shows that AutomationContext automatically:
1. Collects all results (success and failure)
2. Prints a per-host summary on exit (changed/ok/failed)
3. Prints error details on exit

No manual error checking needed — just run your tasks and
the context manager handles the rest.

Run with: uv run python example_auto_summary.py
"""

import asyncio
import tempfile

from ftl2 import automation


async def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        # print_summary=True and print_errors=True are the defaults.
        # No need to check ftl.failed or ftl.errors manually.
        async with automation(fail_fast=False) as ftl:
            # These succeed
            await ftl.file(path=f"{tmpdir}/app", state="directory")
            await ftl.file(path=f"{tmpdir}/app/config.yml", state="touch")
            await ftl.command(cmd="echo 'deployed v1.2.3'")

            # This fails (nonexistent path)
            await ftl.file(path="/root/cannot-write-here/file.txt", state="touch")

            # Execution continues despite the failure above
            await ftl.command(cmd="echo 'cleanup done'")

        # On exit, the context manager prints:
        #
        # SUMMARY:
        #   localhost: 5 tasks (3 changed, 1 ok, 1 failed)
        #
        # ERRORS (1):
        #   file on localhost: ...


if __name__ == "__main__":
    asyncio.run(main())
