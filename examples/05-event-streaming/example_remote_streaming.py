#!/usr/bin/env python3
"""Example: Remote SSH streaming with real-time progress.

This example demonstrates:
1. SSH streaming execution with event callbacks
2. Multi-host progress tracking
3. Remote bundle execution with events

Prerequisites:
- Docker container running (see docker-compose.yml)
- Run: docker-compose up -d

Run with: uv run python example_remote_streaming.py
"""

import asyncio
import json
import tempfile
from pathlib import Path

from ftl2.ssh import SSHHost
from ftl2.module_loading.bundle import build_bundle
from ftl2.module_loading.executor import (
    execute_remote_streaming,
    execute_remote_with_staging_streaming,
    stage_bundle_remote,
)
from ftl2.progress import EventProgressDisplay, SimpleEventDisplay


# Docker container SSH settings
SSH_HOST = "localhost"
SSH_PORT = 2222
SSH_USER = "testuser"
SSH_PASS = "testpass"


async def create_ssh_host() -> SSHHost:
    """Create SSH connection to test container."""
    return SSHHost(
        hostname=SSH_HOST,
        port=SSH_PORT,
        username=SSH_USER,
        password=SSH_PASS,
        known_hosts=None,  # Disable host key checking for testing
    )


async def example_ssh_streaming():
    """Basic SSH streaming with events."""
    print("\n" + "=" * 60)
    print("Example 1: SSH Command Streaming")
    print("=" * 60)

    host = await create_ssh_host()

    try:
        async with host:
            print(f"\nConnected to {host.name}")
            print("Running command with event streaming...\n")

            events_received = []

            def on_event(event):
                events_received.append(event)
                print(f"  Event: {event}")

            # run_streaming returns (stdout, stderr, rc, events)
            stdout, stderr, rc, events = await host.run_streaming(
                'echo \'{"event": "log", "level": "info", "message": "Hello from remote!"}\' >&2; '
                'echo \'{"event": "progress", "percent": 100, "message": "Complete"}\' >&2; '
                'echo "Command output"',
                event_callback=on_event,
            )

            print(f"\nStdout: {stdout.strip()}")
            print(f"Return code: {rc}")
            print(f"Events received: {len(events)}")

    except Exception as e:
        print(f"Error: {e}")
        print("Make sure the Docker container is running: docker-compose up -d")


async def example_remote_bundle_streaming():
    """Remote bundle execution with streaming progress."""
    print("\n" + "=" * 60)
    print("Example 2: Remote Bundle Execution with Progress")
    print("=" * 60)

    # Create a module that emits progress events
    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "remote_progress.py"
        module_path.write_text('''
from ansible.module_utils.basic import AnsibleModule

# Import FTL2 events (available in bundle)
from ftl2.events import emit_progress, emit_log

def main():
    module = AnsibleModule(
        argument_spec={
            "steps": {"type": "int", "default": 5},
        }
    )

    steps = module.params["steps"]

    emit_log("Starting remote operation", level="info")

    for i in range(steps + 1):
        percent = int(i * 100 / steps)
        emit_progress(
            percent=percent,
            message=f"Remote step {i}/{steps}",
            current=i,
            total=steps,
        )
        import time
        time.sleep(0.2)

    emit_log("Remote operation complete", level="info")
    module.exit_json(changed=True, steps=steps)

if __name__ == "__main__":
    main()
''')

        # Build the bundle
        bundle = build_bundle(module_path, dependencies=[])
        print(f"Built bundle: {bundle.info.content_hash} ({bundle.info.size} bytes)")

        host = await create_ssh_host()

        try:
            async with host:
                print(f"Connected to {host.name}")

                # Stage bundle to remote host
                bundle_path = await stage_bundle_remote(host, bundle)
                print(f"Staged bundle at: {bundle_path}")

                print("\nExecuting with Rich progress display...")
                print("(Watch the progress bar)\n")

                display = EventProgressDisplay()
                with display:
                    result = await execute_remote_streaming(
                        host,
                        bundle_path,
                        {"steps": 5},
                        event_callback=display.handle_event,
                    )

                print(f"\nResult: success={result.success}")
                print(f"Output: {result.output}")
                print(f"Events captured: {len(result.events)}")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure the Docker container is running: docker-compose up -d")


async def example_multi_host_streaming():
    """Multi-host execution with per-host progress."""
    print("\n" + "=" * 60)
    print("Example 3: Multi-Host Progress Tracking")
    print("=" * 60)

    # Create module that simulates work
    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "work_module.py"
        module_path.write_text('''
from ansible.module_utils.basic import AnsibleModule
from ftl2.events import emit_progress
import time
import random

def main():
    module = AnsibleModule(
        argument_spec={
            "task_name": {"type": "str", "default": "Working"},
        }
    )

    task_name = module.params["task_name"]
    duration = random.uniform(0.5, 1.5)
    steps = 10

    for i in range(steps + 1):
        percent = int(i * 100 / steps)
        emit_progress(percent=percent, message=task_name)
        time.sleep(duration / steps)

    module.exit_json(changed=True, task=task_name, duration=round(duration, 2))

if __name__ == "__main__":
    main()
''')

        bundle = build_bundle(module_path, dependencies=[])

        # Simulate multiple hosts (using same container with different "names")
        host_names = ["web-01", "web-02", "db-01"]

        print(f"Simulating execution on {len(host_names)} hosts...")
        print("(Each host gets its own progress bar)\n")

        display = EventProgressDisplay()

        async def run_on_host(host_name: str) -> dict:
            host = await create_ssh_host()
            try:
                async with host:
                    bundle_path = await stage_bundle_remote(host, bundle)
                    callback = display.make_callback(host_name)
                    result = await execute_remote_streaming(
                        host,
                        bundle_path,
                        {"task_name": f"Deploying to {host_name}"},
                        event_callback=callback,
                    )
                    return {"host": host_name, "success": result.success, "output": result.output}
            except Exception as e:
                return {"host": host_name, "success": False, "error": str(e)}

        try:
            with display:
                results = await asyncio.gather(*[run_on_host(name) for name in host_names])

            print(f"\nResults:")
            for r in results:
                status = "OK" if r.get("success") else "FAILED"
                print(f"  {r['host']}: {status}")
                if "output" in r:
                    print(f"    Duration: {r['output'].get('duration')}s")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure the Docker container is running: docker-compose up -d")


async def example_with_staging():
    """Automatic staging and execution with streaming."""
    print("\n" + "=" * 60)
    print("Example 4: Auto-Staging with Streaming")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        module_path = Path(tmpdir) / "auto_stage.py"
        module_path.write_text('''
from ansible.module_utils.basic import AnsibleModule
from ftl2.events import emit_progress, emit_log

def main():
    module = AnsibleModule(argument_spec={})

    emit_log("Module starting on remote host", level="info")

    for i in range(5):
        emit_progress(percent=(i + 1) * 20, message="Processing")
        import time
        time.sleep(0.1)

    emit_log("Module complete", level="info")
    module.exit_json(changed=True, msg="Auto-staged and executed!")

if __name__ == "__main__":
    main()
''')

        bundle = build_bundle(module_path, dependencies=[])
        host = await create_ssh_host()

        try:
            async with host:
                print(f"Connected to {host.name}")
                print("Using execute_remote_with_staging_streaming...")
                print("(Automatically stages bundle if needed)\n")

                display = EventProgressDisplay()
                with display:
                    result = await execute_remote_with_staging_streaming(
                        host,
                        bundle,
                        {},
                        event_callback=display.handle_event,
                    )

                print(f"\nResult: {result.output.get('msg')}")

        except Exception as e:
            print(f"Error: {e}")
            print("Make sure the Docker container is running: docker-compose up -d")


async def main():
    """Run all examples."""
    print("FTL2 Remote Event Streaming Examples")
    print("=" * 60)
    print("These examples demonstrate remote SSH execution with")
    print("real-time event streaming and progress display.")
    print()
    print("Prerequisites:")
    print("  docker-compose up -d")
    print()

    await example_ssh_streaming()
    await example_remote_bundle_streaming()
    await example_multi_host_streaming()
    await example_with_staging()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
