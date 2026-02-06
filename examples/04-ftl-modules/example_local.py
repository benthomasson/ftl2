#!/usr/bin/env python3
"""FTL Modules - Local Execution Examples.

Demonstrates FTL modules running locally with 250x+ speedup
over traditional subprocess-based Ansible modules.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ftl2.ftl_modules import (
    # Direct module functions
    ftl_file,
    ftl_copy,
    ftl_command,
    ftl_shell,
    # Executor API
    execute,
    execute_batch,
    execute_on_hosts,
    run,
    LocalHost,
)


def example_file_operations():
    """Demonstrate file module operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: File Operations (ftl_file)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create a directory
        print("\n1. Creating directory...")
        result = ftl_file(path=str(base / "mydir"), state="directory")
        print(f"   Changed: {result['changed']}")
        print(f"   Path: {result['path']}")

        # Touch a file
        print("\n2. Touching file...")
        result = ftl_file(path=str(base / "mydir" / "test.txt"), state="touch")
        print(f"   Changed: {result['changed']}")
        print(f"   Path: {result['path']}")

        # Touch again (idempotent - no change)
        print("\n3. Touching same file again (idempotent)...")
        result = ftl_file(path=str(base / "mydir" / "test.txt"), state="touch")
        print(f"   Changed: {result['changed']} (should be False)")

        # Check file state
        print("\n4. Checking file state...")
        result = ftl_file(path=str(base / "mydir" / "test.txt"), state="file")
        print(f"   Exists: {result['state'] == 'file'}")
        print(f"   Mode: {result.get('mode', 'N/A')}")

        # Delete file
        print("\n5. Deleting file...")
        result = ftl_file(path=str(base / "mydir" / "test.txt"), state="absent")
        print(f"   Changed: {result['changed']}")

        # Delete directory
        print("\n6. Deleting directory...")
        result = ftl_file(path=str(base / "mydir"), state="absent")
        print(f"   Changed: {result['changed']}")


def example_copy_operations():
    """Demonstrate copy module operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Copy Operations (ftl_copy)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Create source file
        source = base / "source.txt"
        source.write_text("Hello, FTL Modules!")

        # Copy file
        print("\n1. Copying file...")
        result = ftl_copy(src=str(source), dest=str(base / "dest.txt"))
        print(f"   Changed: {result['changed']}")
        print(f"   Source: {result['src']}")
        print(f"   Dest: {result['dest']}")

        # Verify content
        dest_content = (base / "dest.txt").read_text()
        print(f"   Content matches: {dest_content == 'Hello, FTL Modules!'}")

        # Copy again (idempotent)
        print("\n2. Copying same file again (idempotent)...")
        result = ftl_copy(src=str(source), dest=str(base / "dest.txt"))
        print(f"   Changed: {result['changed']} (should be False)")

        # Copy with backup
        print("\n3. Copying with backup...")
        source.write_text("Updated content!")
        result = ftl_copy(src=str(source), dest=str(base / "dest.txt"), backup=True)
        print(f"   Changed: {result['changed']}")
        print(f"   Backup: {result.get('backup_file', 'N/A')}")


def example_command_operations():
    """Demonstrate command module operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Command Operations (ftl_command)")
    print("=" * 60)

    # Simple command
    print("\n1. Running simple command...")
    result = ftl_command(cmd="echo 'Hello from FTL!'")
    print(f"   stdout: {result['stdout'].strip()}")
    print(f"   rc: {result['rc']}")
    print(f"   changed: {result['changed']}")

    # Command with working directory
    print("\n2. Running command in /tmp...")
    result = ftl_command(cmd="pwd", chdir="/tmp")
    print(f"   stdout: {result['stdout'].strip()}")

    # Command with creates (idempotency)
    with tempfile.TemporaryDirectory() as tmpdir:
        marker = Path(tmpdir) / "marker.txt"

        print("\n3. Command with 'creates' (runs because file doesn't exist)...")
        result = ftl_command(
            cmd=f"touch {marker}",
            creates=str(marker),
        )
        print(f"   changed: {result['changed']} (should be True)")

        print("\n4. Same command with 'creates' (skipped because file exists)...")
        result = ftl_command(
            cmd=f"touch {marker}",
            creates=str(marker),
        )
        print(f"   changed: {result['changed']} (should be False)")


def example_shell_operations():
    """Demonstrate shell module operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Shell Operations (ftl_shell)")
    print("=" * 60)

    # Shell with pipes
    print("\n1. Shell command with pipes...")
    result = ftl_shell(cmd="echo 'one two three' | wc -w")
    print(f"   stdout: {result['stdout'].strip()}")

    # Shell with environment variable expansion
    print("\n2. Shell with environment variables...")
    result = ftl_shell(cmd="echo $HOME")
    print(f"   stdout: {result['stdout'].strip()}")


async def example_executor_api():
    """Demonstrate the async executor API."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Async Executor API")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Using execute()
        print("\n1. Using execute()...")
        result = await execute("file", {"path": str(base / "test.txt"), "state": "touch"})
        print(f"   success: {result.success}")
        print(f"   changed: {result.changed}")
        print(f"   module: {result.module}")
        print(f"   used_ftl: {result.used_ftl}")

        # Using run() convenience function
        print("\n2. Using run() convenience function...")
        result = await run("command", cmd="date")
        print(f"   stdout: {result.output['stdout'].strip()}")

        # Using execute_batch() for concurrent operations
        print("\n3. Using execute_batch() for concurrent operations...")
        tasks = [
            ("file", {"path": str(base / "file1.txt"), "state": "touch"}, None),
            ("file", {"path": str(base / "file2.txt"), "state": "touch"}, None),
            ("file", {"path": str(base / "file3.txt"), "state": "touch"}, None),
            ("command", {"cmd": "echo 'batch task'"}, None),
        ]
        results = await execute_batch(tasks)
        print(f"   Completed {len(results)} tasks")
        print(f"   All successful: {all(r.success for r in results)}")

        # Using execute_on_hosts() for parallel execution
        print("\n4. Using execute_on_hosts() for parallel execution...")
        hosts = [LocalHost(name=f"worker-{i}") for i in range(5)]
        results = await execute_on_hosts(hosts, "command", {"cmd": "echo hello"})
        print(f"   Executed on {len(results)} hosts")
        for r in results:
            print(f"   {r.host}: {r.output['stdout'].strip()}")


async def example_error_handling():
    """Demonstrate error handling."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Error Handling")
    print("=" * 60)

    # Command that fails
    print("\n1. Command that fails...")
    result = await execute("command", {"cmd": "exit 1"})
    print(f"   success: {result.success}")
    print(f"   error: {result.error or 'N/A'}")
    print(f"   rc: {result.output.get('rc', 'N/A')}")

    # File that doesn't exist
    print("\n2. Checking non-existent file...")
    result = await execute("file", {"path": "/nonexistent/path/file.txt", "state": "file"})
    print(f"   success: {result.success}")
    print(f"   error: {result.error[:50]}..." if result.error else "   error: N/A")

    # Module that doesn't exist
    print("\n3. Using non-existent module...")
    result = await execute("nonexistent_module", {})
    print(f"   success: {result.success}")
    print(f"   used_ftl: {result.used_ftl} (should be False)")


async def main():
    """Run all examples."""
    print("=" * 60)
    print("FTL MODULES - LOCAL EXECUTION EXAMPLES")
    print("=" * 60)

    # Synchronous examples (direct function calls)
    example_file_operations()
    example_copy_operations()
    example_command_operations()
    example_shell_operations()

    # Async examples (executor API)
    await example_executor_api()
    await example_error_handling()

    print("\n" + "=" * 60)
    print("ALL EXAMPLES COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
