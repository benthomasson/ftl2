#!/usr/bin/env python3
"""Example: Phase 5 - Progress and Output.

This example demonstrates output modes in the automation context:
- Quiet mode for scripts
- Verbose mode with timing
- Event callbacks for custom handling
- Output mode property

Run with: uv run python example_phase5_output.py
"""

import asyncio
import json
import tempfile
from pathlib import Path

from ftl2 import automation, AutomationContext
from ftl2.automation import OutputMode


async def example_verbose_with_timing():
    """Verbose mode shows execution timing."""
    print("\n" + "=" * 60)
    print("Example 1: Verbose Mode with Timing")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(verbose=True) as ftl:
            await ftl.file(path=f"{tmpdir}/test1.txt", state="touch")
            await ftl.file(path=f"{tmpdir}/test2.txt", state="touch")
            await ftl.command(cmd="echo 'Hello World'")


async def example_quiet_mode():
    """Quiet mode suppresses all output."""
    print("\n" + "=" * 60)
    print("Example 2: Quiet Mode")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        print("Running 3 operations in quiet mode...")

        async with automation(quiet=True) as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
            await ftl.command(cmd="echo 'This will not be shown'")
            await ftl.file(path=f"{tmpdir}/test.txt", state="absent")

            # Check results programmatically
            success_count = sum(1 for r in ftl.results if r.success)
            changed_count = sum(1 for r in ftl.results if r.changed)

        print(f"Completed: {len(ftl.results)} operations")
        print(f"  Successful: {success_count}")
        print(f"  Changed: {changed_count}")


async def example_event_callback():
    """Use event callback for custom handling."""
    print("\n" + "=" * 60)
    print("Example 3: Event Callback")
    print("=" * 60)

    events = []

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(on_event=events.append) as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
            await ftl.command(cmd="echo hello")

    print(f"Captured {len(events)} events:")
    for event in events:
        event_type = event["event"]
        module = event["module"]
        if event_type == "module_start":
            print(f"  START: {module}")
        else:
            duration = event.get("duration", 0)
            status = "OK" if event.get("success") else "FAILED"
            print(f"  COMPLETE: {module} -> {status} ({duration:.3f}s)")


async def example_event_json_logging():
    """Log events as JSON for processing."""
    print("\n" + "=" * 60)
    print("Example 4: JSON Event Logging")
    print("=" * 60)

    events = []

    def json_logger(event):
        # In production, you might write to a file or send to a service
        events.append(event)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation(on_event=json_logger) as ftl:
            await ftl.file(path=f"{tmpdir}/config", state="directory")
            await ftl.file(path=f"{tmpdir}/config/app.yml", state="touch")

    print("JSON events:")
    for event in events:
        print(f"  {json.dumps(event)}")


async def example_output_modes():
    """Demonstrate different output modes."""
    print("\n" + "=" * 60)
    print("Example 5: Output Mode Property")
    print("=" * 60)

    # Normal mode (default)
    context1 = AutomationContext()
    print(f"Default: {context1.output_mode}")

    # Verbose mode
    context2 = AutomationContext(verbose=True)
    print(f"Verbose: {context2.output_mode}")

    # Quiet mode
    context3 = AutomationContext(quiet=True)
    print(f"Quiet: {context3.output_mode}")

    # Events mode
    context4 = AutomationContext(on_event=lambda e: None)
    print(f"With callback: {context4.output_mode}")


async def example_quiet_with_event_callback():
    """Combine quiet mode with event callback."""
    print("\n" + "=" * 60)
    print("Example 6: Quiet Mode with Event Callback")
    print("=" * 60)

    events = []

    with tempfile.TemporaryDirectory() as tmpdir:
        print("Running silently but collecting events...")

        async with automation(quiet=True, on_event=events.append) as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
            await ftl.command(cmd="echo 'silent execution'")

    # Process events after execution
    completions = [e for e in events if e["event"] == "module_complete"]
    total_time = sum(e.get("duration", 0) for e in completions)

    print(f"Modules executed: {len(completions)}")
    print(f"Total time: {total_time:.3f}s")
    print(f"All successful: {all(e['success'] for e in completions)}")


async def example_verbose_vs_normal():
    """Compare verbose and normal mode output."""
    print("\n" + "=" * 60)
    print("Example 7: Verbose vs Normal Mode")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        print("\nNormal mode (errors only):")
        async with automation() as ftl:
            await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
            await ftl.command(cmd="echo 'hello'")
        print("  (no output for successful operations)")

        print("\nVerbose mode:")
        async with automation(verbose=True) as ftl:
            await ftl.file(path=f"{tmpdir}/test2.txt", state="touch")
            await ftl.command(cmd="echo 'hello'")


async def example_custom_progress_display():
    """Build a custom progress display with events."""
    print("\n" + "=" * 60)
    print("Example 8: Custom Progress Display")
    print("=" * 60)

    completed = {"count": 0, "total": 3}

    def progress_callback(event):
        if event["event"] == "module_complete":
            completed["count"] += 1
            pct = int(completed["count"] / completed["total"] * 100)
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            module = event["module"]
            print(f"  [{bar}] {pct}% - {module} complete")

    with tempfile.TemporaryDirectory() as tmpdir:
        print("Progress:")
        async with automation(on_event=progress_callback) as ftl:
            await ftl.file(path=f"{tmpdir}/step1", state="directory")
            await ftl.file(path=f"{tmpdir}/step2", state="directory")
            await ftl.command(cmd="echo 'Done!'")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - Phase 5: Progress and Output")
    print("=" * 60)
    print("Demonstrates output modes and event handling")

    await example_verbose_with_timing()
    await example_quiet_mode()
    await example_event_callback()
    await example_event_json_logging()
    await example_output_modes()
    await example_quiet_with_event_callback()
    await example_verbose_vs_normal()
    await example_custom_progress_display()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nKey takeaways:")
    print("- verbose=True: Show all operations with timing")
    print("- quiet=True: Suppress all output (for scripts)")
    print("- on_event=callback: Receive structured events")
    print("- Events include: module_start, module_complete")
    print("- Events have: module, host, success, changed, duration, timestamp")
    print("- Use ftl.output_mode to check current mode")


if __name__ == "__main__":
    asyncio.run(main())
