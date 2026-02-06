#!/usr/bin/env python3
"""Example: Phase 6 - Error Handling.

This example demonstrates error handling in the automation context:
- Checking for failures with ftl.failed
- Inspecting errors with ftl.errors
- Getting error messages with ftl.error_messages
- Using fail_fast mode for immediate failure
- Error handling patterns

Run with: uv run python example_phase6_error_handling.py
"""

import asyncio
import tempfile
from pathlib import Path

from ftl2 import automation, AutomationContext
from ftl2.automation import AutomationError
from ftl2.ftl_modules import ExecuteResult


async def example_basic_error_check():
    """Check for errors after execution."""
    print("\n" + "=" * 60)
    print("Example 1: Basic Error Check")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(quiet=True) as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
            await ftl.command(cmd="echo 'Hello World'")

            print(f"Total operations: {len(ftl.results)}")
            print(f"Any failures: {ftl.failed}")
            print(f"Error count: {len(ftl.errors)}")


async def example_inspecting_errors():
    """Inspect individual errors."""
    print("\n" + "=" * 60)
    print("Example 2: Inspecting Errors")
    print("=" * 60)

    # Simulate errors by manually adding results
    context = AutomationContext()

    # Add some results (simulating execution)
    context._results.append(ExecuteResult(
        success=True, changed=True, output={},
        module="file", host="web01"
    ))
    context._results.append(ExecuteResult(
        success=False, changed=False, output={},
        error="Connection refused", module="service", host="web02"
    ))
    context._results.append(ExecuteResult(
        success=True, changed=True, output={},
        module="file", host="web03"
    ))
    context._results.append(ExecuteResult(
        success=False, changed=False, output={},
        error="Permission denied", module="copy", host="db01"
    ))

    print(f"Failed: {context.failed}")
    print(f"Error count: {len(context.errors)}")

    if context.failed:
        print("\nError details:")
        for error in context.errors:
            print(f"  [{error.host}] {error.module}: {error.error}")


async def example_error_messages():
    """Get just the error messages."""
    print("\n" + "=" * 60)
    print("Example 3: Error Messages")
    print("=" * 60)

    context = AutomationContext()

    # Simulate mixed results
    context._results.append(ExecuteResult(
        success=True, changed=True, output={}, module="file", host="localhost"
    ))
    context._results.append(ExecuteResult(
        success=False, changed=False, output={},
        error="File not found: /etc/missing.conf", module="copy", host="localhost"
    ))
    context._results.append(ExecuteResult(
        success=False, changed=False, output={},
        error="Service nginx is not installed", module="service", host="localhost"
    ))

    print("Error messages:")
    for msg in context.error_messages:
        print(f"  - {msg}")


async def example_continue_on_error():
    """Continue execution after errors (default behavior)."""
    print("\n" + "=" * 60)
    print("Example 4: Continue on Error (Default)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(quiet=True) as ftl:
            # All of these will execute regardless of individual failures
            await ftl.file(path=f"{tmpdir}/file1.txt", state="touch")
            await ftl.command(cmd="echo 'Step 2'")
            await ftl.file(path=f"{tmpdir}/file2.txt", state="touch")
            await ftl.command(cmd="echo 'Step 4'")

            print(f"All {len(ftl.results)} operations completed")

            # Check overall status at the end
            if ftl.failed:
                print(f"Some operations failed ({len(ftl.errors)} errors)")
            else:
                print("All operations succeeded!")


async def example_fail_fast():
    """Stop immediately on first error."""
    print("\n" + "=" * 60)
    print("Example 5: Fail Fast Mode")
    print("=" * 60)

    print("With fail_fast=True, execution stops on first error")
    print("(In this example, we simulate with a context check)")

    context = AutomationContext(fail_fast=True)
    print(f"fail_fast enabled: {context.fail_fast}")

    # Note: In real usage, AutomationError would be raised:
    # try:
    #     async with automation(fail_fast=True) as ftl:
    #         await ftl.some_failing_module(...)  # Raises AutomationError
    # except AutomationError as e:
    #     print(f"Failed: {e}")
    #     print(f"Module: {e.result.module}")
    #     print(f"Error: {e.result.error}")


async def example_automation_error():
    """Understanding AutomationError."""
    print("\n" + "=" * 60)
    print("Example 6: AutomationError Exception")
    print("=" * 60)

    # Create an AutomationError with a result
    failed_result = ExecuteResult(
        success=False,
        changed=False,
        output={"failed": True, "msg": "Service not found"},
        error="Service 'nginx' not found",
        module="service",
        host="web01",
    )

    error = AutomationError("Module execution failed", result=failed_result)

    print(f"Error message: {error.message}")
    print(f"String representation: {error}")
    print(f"Has result: {error.result is not None}")
    print(f"Failed module: {error.result.module}")
    print(f"Failed host: {error.result.host}")


async def example_error_handling_pattern():
    """Recommended error handling pattern."""
    print("\n" + "=" * 60)
    print("Example 7: Recommended Error Handling Pattern")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(quiet=True) as ftl:
            # Execute operations
            await ftl.file(path=f"{tmpdir}/config", state="directory")
            await ftl.file(path=f"{tmpdir}/config/app.yml", state="touch")
            await ftl.command(cmd="echo 'Configuration complete'")

            # Summary
            success_count = sum(1 for r in ftl.results if r.success)
            changed_count = sum(1 for r in ftl.results if r.changed)
            failed_count = len(ftl.errors)

            print(f"Results: {success_count} succeeded, {failed_count} failed")
            print(f"Changes: {changed_count} operations made changes")

            if ftl.failed:
                print("\nFailed operations:")
                for error in ftl.errors:
                    print(f"  [{error.host}:{error.module}] {error.error}")
                return False  # Indicate failure
            else:
                print("\nAll operations completed successfully!")
                return True  # Indicate success


async def example_error_summary():
    """Create a summary of execution results."""
    print("\n" + "=" * 60)
    print("Example 8: Execution Summary")
    print("=" * 60)

    context = AutomationContext()

    # Simulate a complex execution
    hosts = ["web01", "web02", "db01", "db02", "cache01"]
    for host in hosts:
        # Some succeed, some fail
        success = host not in ["web02", "db02"]
        context._results.append(ExecuteResult(
            success=success,
            changed=success,
            output={},
            error="" if success else f"Connection to {host} timed out",
            module="deploy",
            host=host,
        ))

    # Generate summary
    print("Execution Summary")
    print("-" * 40)
    print(f"Total hosts: {len(hosts)}")
    print(f"Successful: {len(hosts) - len(context.errors)}")
    print(f"Failed: {len(context.errors)}")
    print(f"Overall: {'FAILED' if context.failed else 'SUCCESS'}")

    if context.failed:
        print("\nFailed hosts:")
        for error in context.errors:
            print(f"  - {error.host}: {error.error}")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - Phase 6: Error Handling")
    print("=" * 60)
    print("Demonstrates error checking and handling patterns")

    await example_basic_error_check()
    await example_inspecting_errors()
    await example_error_messages()
    await example_continue_on_error()
    await example_fail_fast()
    await example_automation_error()
    await example_error_handling_pattern()
    await example_error_summary()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nKey takeaways:")
    print("- ftl.failed: True if any module failed")
    print("- ftl.errors: List of failed ExecuteResult objects")
    print("- ftl.error_messages: List of error message strings")
    print("- fail_fast=True: Stop on first error (raises AutomationError)")
    print("- Default: Continue execution, collect all errors")
    print("- Check ftl.failed after execution for overall status")


if __name__ == "__main__":
    asyncio.run(main())
