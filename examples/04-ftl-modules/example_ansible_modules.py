#!/usr/bin/env python3
"""Ansible Builtin Modules - Examples using module_loading.

Demonstrates executing actual Ansible modules from ftl_builtin_modules
using the module_loading system. This shows the traditional Ansible
module execution path (subprocess-based) for comparison with FTL modules.

Requirements:
    - ftl_builtin_modules installed: pip install -e ../ftl_builtin_modules
    - ftl_module_utils installed: pip install -e ../ftl_module_utils

Note: Some Ansible modules may fail due to name shadowing issues
(e.g., tempfile.py shadows the standard library). This is a known
limitation when running Ansible modules outside of the full Ansible
runtime environment. The examples demonstrate the API regardless.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ftl2.module_loading.executor import (
    execute_local_fqcn,
    execute_bundle_local,
    ExecutionResult,
)
from ftl2.module_loading.bundle import build_bundle_from_fqcn
from ftl2.module_loading.fqcn import resolve_fqcn, find_ansible_builtin_path


def check_ansible_available():
    """Check if Ansible builtin modules are available."""
    path = find_ansible_builtin_path()
    if not path:
        print("ERROR: Ansible builtin modules not found.")
        print("\nPlease install ftl_builtin_modules:")
        print("  pip install -e ../ftl_builtin_modules")
        print("  pip install -e ../ftl_module_utils")
        return False
    print(f"Found Ansible modules at: {path}")
    return True


def example_resolve_fqcn():
    """Demonstrate FQCN resolution."""
    print("\n" + "=" * 60)
    print("EXAMPLE: FQCN Resolution")
    print("=" * 60)

    modules_to_resolve = [
        "ansible.builtin.command",
        "ansible.builtin.file",
        "ansible.builtin.copy",
        "ansible.builtin.ping",
        "ansible.builtin.stat",
        "ansible.builtin.tempfile",
    ]

    for fqcn in modules_to_resolve:
        try:
            path = resolve_fqcn(fqcn)
            print(f"\n  {fqcn}")
            print(f"    -> {path}")
        except Exception as e:
            print(f"\n  {fqcn}")
            print(f"    -> ERROR: {e}")


def example_execute_ping():
    """Execute the ping module (simplest Ansible module)."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Execute ansible.builtin.ping")
    print("=" * 60)

    print("\n  Running: ansible.builtin.ping")
    result = execute_local_fqcn("ansible.builtin.ping", {})

    print(f"  Success: {result.success}")
    print(f"  Output: {result.output}")
    if result.error:
        print(f"  Error: {result.error}")


def example_execute_command():
    """Execute the command module."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Execute ansible.builtin.command")
    print("=" * 60)

    # Simple command
    print("\n  Running: echo 'Hello from Ansible!'")
    result = execute_local_fqcn(
        "ansible.builtin.command",
        {"_raw_params": "echo 'Hello from Ansible!'"},
    )

    print(f"  Success: {result.success}")
    print(f"  Changed: {result.changed}")
    if result.success:
        print(f"  stdout: {result.output.get('stdout', '').strip()}")
        print(f"  rc: {result.output.get('rc', 'N/A')}")
    else:
        print(f"  Error: {result.error}")


def example_execute_stat():
    """Execute the stat module to get file information."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Execute ansible.builtin.stat")
    print("=" * 60)

    # Stat /etc/hosts
    print("\n  Running: stat /etc/hosts")
    result = execute_local_fqcn(
        "ansible.builtin.stat",
        {"path": "/etc/hosts"},
    )

    print(f"  Success: {result.success}")
    if result.success:
        stat_result = result.output.get("stat", {})
        print(f"  exists: {stat_result.get('exists', False)}")
        print(f"  size: {stat_result.get('size', 'N/A')} bytes")
        print(f"  mode: {stat_result.get('mode', 'N/A')}")
        print(f"  isreg: {stat_result.get('isreg', False)}")
    else:
        print(f"  Error: {result.error}")


def example_execute_tempfile():
    """Execute the tempfile module."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Execute ansible.builtin.tempfile")
    print("=" * 60)

    print("\n  Running: create temp file")
    result = execute_local_fqcn(
        "ansible.builtin.tempfile",
        {"state": "file", "prefix": "ftl2_example_"},
    )

    print(f"  Success: {result.success}")
    print(f"  Changed: {result.changed}")
    if result.success:
        temp_path = result.output.get("path", "")
        print(f"  path: {temp_path}")
        # Clean up
        if temp_path and Path(temp_path).exists():
            Path(temp_path).unlink()
            print("  (cleaned up)")
    else:
        print(f"  Error: {result.error}")


def example_execute_file():
    """Execute the file module."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Execute ansible.builtin.file")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "test.txt"

        # Touch file
        print(f"\n  Running: touch {test_file}")
        result = execute_local_fqcn(
            "ansible.builtin.file",
            {"path": str(test_file), "state": "touch"},
        )

        print(f"  Success: {result.success}")
        print(f"  Changed: {result.changed}")
        print(f"  File exists: {test_file.exists()}")
        if result.error:
            print(f"  Error: {result.error}")

        # Check state
        print(f"\n  Running: stat {test_file}")
        result = execute_local_fqcn(
            "ansible.builtin.file",
            {"path": str(test_file), "state": "file"},
        )
        print(f"  Success: {result.success}")
        if result.success:
            print(f"  mode: {result.output.get('mode', 'N/A')}")

        # Delete file
        print(f"\n  Running: absent {test_file}")
        result = execute_local_fqcn(
            "ansible.builtin.file",
            {"path": str(test_file), "state": "absent"},
        )
        print(f"  Success: {result.success}")
        print(f"  Changed: {result.changed}")
        print(f"  File exists: {test_file.exists()}")


def example_build_bundle():
    """Demonstrate bundle building for remote execution."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Build Module Bundle")
    print("=" * 60)

    fqcn = "ansible.builtin.command"
    print(f"\n  Building bundle for: {fqcn}")

    try:
        bundle = build_bundle_from_fqcn(fqcn)
        print(f"  Bundle info:")
        print(f"    FQCN: {bundle.info.fqcn}")
        print(f"    Hash: {bundle.info.content_hash}")
        print(f"    Size: {bundle.info.size} bytes")
        print(f"    Dependencies: {bundle.info.dependency_count}")

        # Execute the bundle locally to verify it works
        print(f"\n  Executing bundle locally...")
        result = execute_bundle_local(
            bundle,
            {"_raw_params": "echo 'Hello from bundle!'"},
        )
        print(f"  Success: {result.success}")
        if result.success:
            print(f"  stdout: {result.output.get('stdout', '').strip()}")

    except Exception as e:
        print(f"  Error building bundle: {e}")


def example_compare_ftl_vs_ansible():
    """Compare FTL module vs Ansible module execution."""
    print("\n" + "=" * 60)
    print("COMPARISON: FTL Module vs Ansible Module")
    print("=" * 60)

    import time
    from ftl2.ftl_modules import ftl_command

    iterations = 10

    # Time Ansible module
    print(f"\n  Timing {iterations} iterations of ansible.builtin.command...")
    ansible_times = []
    for _ in range(iterations):
        start = time.perf_counter()
        execute_local_fqcn("ansible.builtin.command", {"_raw_params": "true"})
        ansible_times.append(time.perf_counter() - start)
    ansible_avg = sum(ansible_times) / len(ansible_times) * 1000
    print(f"  Ansible average: {ansible_avg:.2f}ms")

    # Time FTL module
    print(f"\n  Timing {iterations} iterations of ftl_command...")
    ftl_times = []
    for _ in range(iterations):
        start = time.perf_counter()
        ftl_command(cmd="true")
        ftl_times.append(time.perf_counter() - start)
    ftl_avg = sum(ftl_times) / len(ftl_times) * 1000
    print(f"  FTL average: {ftl_avg:.2f}ms")

    # Comparison
    speedup = ansible_avg / ftl_avg
    print(f"\n  FTL is {speedup:.1f}x faster")
    print("  (Both execute 'true' command, difference is wrapper overhead)")


async def example_remote_execution():
    """Demonstrate remote execution with bundles (requires Docker)."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Remote Execution with Bundles")
    print("=" * 60)

    from ftl2.ssh import SSHHost
    from ftl2.module_loading.executor import execute_remote_with_staging

    SSH_CONFIG = {
        "hostname": "localhost",
        "port": 2222,
        "username": "testuser",
        "password": "testpass",
        "disable_host_key_checking": True,  # Only for test containers
    }

    # Check if Docker is running
    try:
        host = SSHHost(**SSH_CONFIG)
        host.config.connect_timeout = 2.0
        await host.connect()
    except Exception as e:
        print(f"\n  Docker container not running: {e}")
        print("  Start with: docker-compose up -d")
        return

    try:
        # Build bundle
        print("\n  Building bundle for ansible.builtin.command...")
        bundle = build_bundle_from_fqcn("ansible.builtin.command")
        print(f"  Bundle size: {bundle.info.size} bytes")

        # Execute on remote
        print("\n  Executing on remote host...")
        result = await execute_remote_with_staging(
            host,
            bundle,
            {"_raw_params": "hostname"},
        )

        print(f"  Success: {result.success}")
        if result.success:
            print(f"  stdout: {result.output.get('stdout', '').strip()}")
        else:
            print(f"  Error: {result.error}")

    finally:
        await host.disconnect()


def main():
    """Run all examples."""
    print("=" * 60)
    print("ANSIBLE BUILTIN MODULES - EXAMPLES")
    print("=" * 60)

    if not check_ansible_available():
        return

    # Basic examples
    example_resolve_fqcn()
    example_execute_ping()
    example_execute_command()
    example_execute_stat()
    example_execute_tempfile()
    example_execute_file()

    # Bundle building
    example_build_bundle()

    # Comparison
    example_compare_ftl_vs_ansible()

    # Remote execution (optional)
    print("\n" + "=" * 60)
    print("REMOTE EXECUTION (requires Docker)")
    print("=" * 60)
    asyncio.run(example_remote_execution())

    print("\n" + "=" * 60)
    print("ALL EXAMPLES COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
