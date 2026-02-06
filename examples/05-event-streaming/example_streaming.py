#!/usr/bin/env python3
"""Example: Event streaming with real-time progress display.

This example demonstrates:
1. Local streaming execution with event callbacks
2. Rich progress bar display for long-running operations
3. Simple text-based event display
4. Multi-host streaming with per-host progress tracking

Run with: uv run python example_streaming.py
"""

import asyncio
import tempfile
from pathlib import Path

from ftl2.module_loading.executor import (
    execute_local_streaming,
    execute_local_fqcn_streaming,
)
from ftl2.progress import EventProgressDisplay, SimpleEventDisplay


async def example_basic_streaming():
    """Basic streaming execution with callback."""
    print("\n" + "=" * 60)
    print("Example 1: Basic Streaming with Callback")
    print("=" * 60)

    # Create a test module that emits events
    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "progress_module.py"
        module_path.write_text('''
import sys
import json
import time

# Emit progress events to stderr
def emit_event(event):
    print(json.dumps(event), file=sys.stderr, flush=True)

if __name__ == "__main__":
    # Read params from stdin (executor sends JSON via stdin)
    input_data = sys.stdin.read()
    data = json.loads(input_data) if input_data else {}
    params = data.get("ANSIBLE_MODULE_ARGS", {})

    steps = params.get("steps", 5)

    for i in range(steps + 1):
        percent = int(i * 100 / steps)
        emit_event({
            "event": "progress",
            "percent": percent,
            "message": f"Processing step {i}/{steps}",
            "current": i,
            "total": steps,
        })
        time.sleep(0.1)

    # Emit log event
    emit_event({
        "event": "log",
        "level": "info",
        "message": "All steps completed successfully",
    })

    # Return result on stdout
    print(json.dumps({"changed": True, "steps_completed": steps}))
''')

        # Collect events via callback
        events_received = []

        def on_event(event):
            events_received.append(event)
            event_type = event.get("event")
            if event_type == "progress":
                print(f"  Progress: {event.get('percent')}% - {event.get('message')}")
            elif event_type == "log":
                print(f"  Log [{event.get('level')}]: {event.get('message')}")

        print("\nExecuting module with event callback...")
        result = await execute_local_streaming(
            module_path,
            {"steps": 5},
            event_callback=on_event,
        )

        print(f"\nResult: success={result.success}, output={result.output}")
        print(f"Total events received: {len(events_received)}")


async def example_rich_progress():
    """Rich progress bar display."""
    print("\n" + "=" * 60)
    print("Example 2: Rich Progress Bar Display")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a module that simulates file copy with progress
        module_path = Path(tmpdir) / "copy_module.py"
        module_path.write_text('''
import sys
import json
import time

def emit_event(event):
    print(json.dumps(event), file=sys.stderr, flush=True)

if __name__ == "__main__":
    input_data = sys.stdin.read()
    data = json.loads(input_data) if input_data else {}
    params = data.get("ANSIBLE_MODULE_ARGS", {})

    filename = params.get("filename", "data.bin")
    total_bytes = params.get("size", 1000000)

    # Simulate chunked transfer
    chunk_size = 100000
    transferred = 0

    while transferred < total_bytes:
        transferred = min(transferred + chunk_size, total_bytes)
        percent = int(transferred * 100 / total_bytes)
        emit_event({
            "event": "progress",
            "percent": percent,
            "message": f"Copying {filename}",
            "current": transferred,
            "total": total_bytes,
        })
        time.sleep(0.05)

    print(json.dumps({"changed": True, "bytes": transferred}))
''')

        print("\nExecuting with Rich progress display...")
        print("(Watch the progress bar below)\n")

        display = EventProgressDisplay()
        with display:
            result = await execute_local_streaming(
                module_path,
                {"filename": "backup.tar.gz", "size": 2000000},
                event_callback=display.handle_event,
            )

        print(f"\nResult: success={result.success}")
        print(f"Bytes transferred: {result.output.get('bytes', 0):,}")


async def example_simple_display():
    """Simple text-based event display."""
    print("\n" + "=" * 60)
    print("Example 3: Simple Text Display (no Rich)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "simple_module.py"
        module_path.write_text('''
import sys
import json
import time

def emit_event(event):
    print(json.dumps(event), file=sys.stderr, flush=True)

if __name__ == "__main__":
    sys.stdin.read()  # Consume stdin

    for percent in [0, 25, 50, 75, 100]:
        emit_event({
            "event": "progress",
            "percent": percent,
            "message": "Working...",
        })
        time.sleep(0.1)

    emit_event({
        "event": "log",
        "level": "info",
        "message": "Task completed",
    })

    print(json.dumps({"changed": True}))
''')

        print("\nExecuting with SimpleEventDisplay...")
        print("(Prints every 10% progress change)\n")

        display = SimpleEventDisplay()
        result = await execute_local_streaming(
            module_path,
            {},
            event_callback=display.handle_event,
        )

        print(f"\nResult: success={result.success}")


async def example_multi_task():
    """Multiple concurrent tasks with progress tracking."""
    print("\n" + "=" * 60)
    print("Example 4: Multiple Concurrent Tasks")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "multi_module.py"
        module_path.write_text('''
import sys
import json
import time
import random

def emit_event(event):
    print(json.dumps(event), file=sys.stderr, flush=True)

if __name__ == "__main__":
    input_data = sys.stdin.read()
    data = json.loads(input_data) if input_data else {}
    params = data.get("ANSIBLE_MODULE_ARGS", {})

    task_id = params.get("task_id", "default")
    task_name = params.get("task_name", "Task")
    duration = params.get("duration", 1.0)

    steps = 10
    for i in range(steps + 1):
        percent = int(i * 100 / steps)
        emit_event({
            "event": "progress",
            "percent": percent,
            "message": task_name,
            "task_id": task_id,
        })
        time.sleep(duration / steps)

    print(json.dumps({"changed": True, "task": task_name}))
''')

        print("\nExecuting multiple tasks concurrently...")
        print("(Each task has its own progress bar)\n")

        display = EventProgressDisplay()

        async def run_task(task_id: str, task_name: str, duration: float):
            callback = display.make_callback(task_id)
            return await execute_local_streaming(
                module_path,
                {"task_id": task_id, "task_name": task_name, "duration": duration},
                event_callback=callback,
            )

        with display:
            results = await asyncio.gather(
                run_task("download", "Downloading package", 0.8),
                run_task("extract", "Extracting files", 1.2),
                run_task("install", "Installing dependencies", 1.0),
            )

        print(f"\nAll {len(results)} tasks completed!")
        for r in results:
            print(f"  - {r.output.get('task')}: success={r.success}")


async def example_events_in_result():
    """Access events from result object."""
    print("\n" + "=" * 60)
    print("Example 5: Events in Result Object")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "events_module.py"
        module_path.write_text('''
import sys
import json

def emit_event(event):
    print(json.dumps(event), file=sys.stderr, flush=True)

if __name__ == "__main__":
    # Emit various events
    emit_event({"event": "log", "level": "debug", "message": "Starting..."})
    emit_event({"event": "progress", "percent": 50, "message": "Working"})
    emit_event({"event": "log", "level": "info", "message": "Halfway done"})
    emit_event({"event": "progress", "percent": 100, "message": "Complete"})
    emit_event({"event": "log", "level": "info", "message": "Finished!"})

    print(json.dumps({"changed": True, "msg": "done"}))
''')

        print("\nExecuting module (events stored in result)...")

        # Execute without callback - events are still captured in result
        result = await execute_local_streaming(module_path, {})

        print(f"\nResult: success={result.success}")
        print(f"Events captured: {len(result.events)}")
        print("\nAll events:")
        for event in result.events:
            event_type = event.get("event")
            if event_type == "progress":
                print(f"  [progress] {event.get('percent')}% - {event.get('message')}")
            elif event_type == "log":
                print(f"  [log/{event.get('level')}] {event.get('message')}")


async def main():
    """Run all examples."""
    print("FTL2 Event Streaming Examples")
    print("=" * 60)
    print("These examples demonstrate real-time event streaming from")
    print("modules, including progress bars and log messages.")

    await example_basic_streaming()
    await example_rich_progress()
    await example_simple_display()
    await example_multi_task()
    await example_events_in_result()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
