#!/usr/bin/env python3
"""Example: Phase 4 - Check Mode (Dry Run).

This example demonstrates check mode (dry run) in the automation context:
- Running modules without making changes
- Seeing what would be changed
- Combining with verbose mode for detailed output
- Using check mode for validation

Run with: uv run python example_phase4_check_mode.py
"""

import asyncio
import tempfile
from pathlib import Path

from ftl2 import automation, AutomationContext


async def example_basic_check_mode():
    """Basic check mode usage."""
    print("\n" + "=" * 60)
    print("Example 1: Basic Check Mode")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "would_be_created.txt"

        print(f"File exists before: {test_file.exists()}")

        async with automation(check_mode=True) as ftl:
            print(f"Check mode enabled: {ftl.check_mode}")

            # This would normally create the file
            result = await ftl.file(path=str(test_file), state="touch")
            print(f"Result: {result}")

        # Note: Whether the file is created depends on module implementation
        # Some modules fully support check mode, others may not
        print(f"File exists after: {test_file.exists()}")


async def example_check_mode_with_verbose():
    """Check mode with verbose output."""
    print("\n" + "=" * 60)
    print("Example 2: Check Mode with Verbose Output")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"

        async with automation(check_mode=True, verbose=True) as ftl:
            print("Running file module in check mode:")
            await ftl.file(path=str(test_file), state="touch")

            print("\nRunning command module in check mode:")
            await ftl.command(cmd="echo 'Hello from check mode'")


async def example_check_mode_results():
    """Tracking results in check mode."""
    print("\n" + "=" * 60)
    print("Example 3: Check Mode Result Tracking")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(check_mode=True) as ftl:
            # Run several operations
            await ftl.file(path=f"{tmpdir}/dir1", state="directory")
            await ftl.file(path=f"{tmpdir}/dir2", state="directory")
            await ftl.command(cmd="echo test")

            print(f"Total operations: {len(ftl.results)}")
            for i, result in enumerate(ftl.results, 1):
                status = "OK" if result.success else "FAILED"
                changed = " (would change)" if result.changed else ""
                print(f"  {i}. [{result.module}] {status}{changed}")


async def example_comparison_with_real_mode():
    """Compare check mode vs real mode."""
    print("\n" + "=" * 60)
    print("Example 4: Check Mode vs Real Mode Comparison")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        check_file = Path(tmpdir) / "check_mode_file.txt"
        real_file = Path(tmpdir) / "real_mode_file.txt"

        # Check mode - should NOT create file
        print("CHECK MODE:")
        async with automation(check_mode=True, verbose=True) as ftl:
            await ftl.file(path=str(check_file), state="touch")
        print(f"  File created: {check_file.exists()}")

        # Real mode - should create file
        print("\nREAL MODE:")
        async with automation(check_mode=False, verbose=True) as ftl:
            await ftl.file(path=str(real_file), state="touch")
        print(f"  File created: {real_file.exists()}")


async def example_check_mode_with_inventory():
    """Check mode with run_on."""
    print("\n" + "=" * 60)
    print("Example 5: Check Mode with run_on")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(check_mode=True, verbose=True) as ftl:
            print("Running on localhost in check mode:")
            results = await ftl.run_on(
                "localhost",
                "file",
                path=f"{tmpdir}/remote_file.txt",
                state="touch",
            )

            for r in results:
                status = "OK" if r.success else "FAILED"
                print(f"  [{r.host}] {status}")


async def example_validation_workflow():
    """Using check mode for validation before execution."""
    print("\n" + "=" * 60)
    print("Example 6: Validation Workflow")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        operations = [
            ("file", {"path": f"{tmpdir}/config", "state": "directory"}),
            ("file", {"path": f"{tmpdir}/config/app.conf", "state": "touch"}),
            ("command", {"cmd": "echo 'Configuration complete'"}),
        ]

        # Phase 1: Validate with check mode
        print("Phase 1: Validating operations...")
        async with automation(check_mode=True) as ftl:
            for module_name, params in operations:
                module_func = getattr(ftl, module_name)
                await module_func(**params)

            # Check for any failures
            failures = [r for r in ftl.results if not r.success]
            if failures:
                print(f"  VALIDATION FAILED: {len(failures)} errors")
                for f in failures:
                    print(f"    - {f.module}: {f.error}")
                return
            else:
                print(f"  Validation passed: {len(ftl.results)} operations OK")

        # Phase 2: Execute for real
        print("\nPhase 2: Executing operations...")
        async with automation(check_mode=False, verbose=True) as ftl:
            for module_name, params in operations:
                module_func = getattr(ftl, module_name)
                await module_func(**params)

        print("\nDone!")


async def example_check_mode_with_context():
    """Direct context creation with check mode."""
    print("\n" + "=" * 60)
    print("Example 7: Direct Context with Check Mode")
    print("=" * 60)

    # Create context directly
    context = AutomationContext(
        check_mode=True,
        verbose=True,
        modules=["file", "command"],
    )

    print(f"Context check_mode: {context.check_mode}")
    print(f"Context verbose: {context.verbose}")
    print(f"Available modules: {context.available_modules}")

    with tempfile.TemporaryDirectory() as tmpdir:
        async with context as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")


async def example_check_mode_with_secrets():
    """Check mode combined with secrets."""
    print("\n" + "=" * 60)
    print("Example 8: Check Mode with Secrets")
    print("=" * 60)

    import os
    os.environ["API_KEY"] = "demo-key-12345"

    async with automation(
        check_mode=True,
        secrets=["API_KEY"],
        verbose=True,
    ) as ftl:
        print(f"Check mode: {ftl.check_mode}")
        print(f"Secrets loaded: {len(ftl.secrets)}")

        # In check mode, would simulate using the API key
        print(f"Would use API key: {ftl.secrets['API_KEY'][:8]}...")

        await ftl.command(cmd="echo 'Would call API'")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - Phase 4: Check Mode (Dry Run)")
    print("=" * 60)
    print("Demonstrates running modules without making changes")

    await example_basic_check_mode()
    await example_check_mode_with_verbose()
    await example_check_mode_results()
    await example_comparison_with_real_mode()
    await example_check_mode_with_inventory()
    await example_validation_workflow()
    await example_check_mode_with_context()
    await example_check_mode_with_secrets()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nKey takeaways:")
    print("- Enable check mode: automation(check_mode=True)")
    print("- Check mode shows what WOULD change without changing")
    print("- Combine with verbose=True for detailed output")
    print("- Use for validation before real execution")
    print("- Results show [CHECK MODE] indicator in verbose output")
    print("- Works with run_on, secrets, and all other features")


if __name__ == "__main__":
    asyncio.run(main())
