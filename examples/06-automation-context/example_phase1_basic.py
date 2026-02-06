#!/usr/bin/env python3
"""Example: Phase 1 - Basic Automation Context Manager.

This example demonstrates the core automation context manager interface:
- Clean ftl.module_name() syntax
- Module restriction
- Verbose mode
- Result tracking

Run with: uv run python example_phase1_basic.py
"""

import asyncio
import tempfile
from pathlib import Path

from ftl2 import automation


async def example_basic_usage():
    """Basic usage of the automation context manager."""
    print("\n" + "=" * 60)
    print("Example 1: Basic Usage")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "hello.txt"
        test_dir = Path(tmpdir) / "mydir"

        async with automation() as ftl:
            # Create a directory
            result = await ftl.file(path=str(test_dir), state="directory")
            print(f"Created directory: {test_dir}")
            print(f"  changed: {result['changed']}")

            # Touch a file
            result = await ftl.file(path=str(test_file), state="touch")
            print(f"Touched file: {test_file}")
            print(f"  changed: {result['changed']}")

            # Run a command
            result = await ftl.command(cmd="echo 'Hello from FTL2!'")
            print(f"Command output: {result['stdout'].strip()}")

        print(f"\nFiles exist: dir={test_dir.exists()}, file={test_file.exists()}")


async def example_copy_files():
    """Copying files with the automation context."""
    print("\n" + "=" * 60)
    print("Example 2: Copy Files")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create source file
        src = Path(tmpdir) / "source.txt"
        src.write_text("Hello, this is the source content!")

        dest = Path(tmpdir) / "destination.txt"

        async with automation() as ftl:
            result = await ftl.copy(src=str(src), dest=str(dest))
            print(f"Copied {src.name} -> {dest.name}")
            print(f"  changed: {result['changed']}")

        print(f"Destination content: {dest.read_text()}")


async def example_restricted_modules():
    """Restricting available modules."""
    print("\n" + "=" * 60)
    print("Example 3: Restricted Modules")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"

        # Only allow file and copy modules
        async with automation(modules=["file", "copy"]) as ftl:
            print(f"Available modules: {ftl.available_modules}")

            # This works
            await ftl.file(path=str(test_file), state="touch")
            print("ftl.file() - OK")

            # This would raise AttributeError:
            # await ftl.command(cmd="echo hello")
            print("ftl.command() - Would raise AttributeError (not enabled)")


async def example_verbose_mode():
    """Verbose mode for debugging."""
    print("\n" + "=" * 60)
    print("Example 4: Verbose Mode")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "verbose_test.txt"

        print("Running with verbose=True:")
        async with automation(verbose=True) as ftl:
            await ftl.file(path=str(test_file), state="touch")
            await ftl.command(cmd="echo 'verbose output'")


async def example_result_tracking():
    """Tracking execution results."""
    print("\n" + "=" * 60)
    print("Example 5: Result Tracking")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation() as ftl:
            # Execute several modules
            await ftl.file(path=str(Path(tmpdir) / "file1.txt"), state="touch")
            await ftl.file(path=str(Path(tmpdir) / "file2.txt"), state="touch")
            await ftl.command(cmd="echo test")

            # Check tracked results
            print(f"Total executions: {len(ftl.results)}")
            for i, result in enumerate(ftl.results):
                status = "OK" if result.success else "FAILED"
                changed = " (changed)" if result.changed else ""
                print(f"  {i+1}. [{result.module}] {status}{changed}")


async def example_chained_operations():
    """Chaining multiple operations."""
    print("\n" + "=" * 60)
    print("Example 6: Chained Operations")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base_dir = Path(tmpdir)

        async with automation() as ftl:
            # Create directory structure
            await ftl.file(path=str(base_dir / "app"), state="directory")
            await ftl.file(path=str(base_dir / "app" / "config"), state="directory")
            await ftl.file(path=str(base_dir / "app" / "logs"), state="directory")

            # Create some files
            await ftl.file(path=str(base_dir / "app" / "config" / "settings.yml"), state="touch")
            await ftl.file(path=str(base_dir / "app" / "logs" / ".gitkeep"), state="touch")

            # Verify with command
            result = await ftl.command(cmd=f"find {base_dir / 'app'} -type f")
            print("Created files:")
            for line in result["stdout"].strip().split("\n"):
                print(f"  {line}")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context Manager - Phase 1 Examples")
    print("=" * 60)
    print("Demonstrates the clean ftl.module_name() syntax")
    print("that's 250x faster than subprocess execution.")

    await example_basic_usage()
    await example_copy_files()
    await example_restricted_modules()
    await example_verbose_mode()
    await example_result_tracking()
    await example_chained_operations()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
