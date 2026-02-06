#!/usr/bin/env python3
"""FTL Modules - Remote Execution Examples.

Demonstrates remote execution via async SSH transport.
Requires Docker container running (see docker-compose.yml).

Usage:
    docker-compose up -d
    uv run python example_remote.py
    docker-compose down
"""

import asyncio
import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from ftl2.ssh import SSHHost, SSHConnectionPool, ssh_run, ssh_run_on_hosts


# SSH connection settings for the Docker container
SSH_CONFIG = {
    "hostname": "localhost",
    "port": 2222,
    "username": "testuser",
    "password": "testpass",
    "known_hosts": None,  # Disable host key checking for testing
}


async def example_basic_ssh():
    """Demonstrate basic SSH operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Basic SSH Operations")
    print("=" * 60)

    host = SSHHost(**SSH_CONFIG)

    async with host:
        # Run a simple command
        print("\n1. Running 'uptime' on remote host...")
        stdout, stderr, rc = await host.run("uptime")
        print(f"   stdout: {stdout.strip()}")
        print(f"   rc: {rc}")

        # Run command with output
        print("\n2. Running 'hostname' on remote host...")
        stdout, stderr, rc = await host.run("hostname")
        print(f"   hostname: {stdout.strip()}")

        # Run command that produces output
        print("\n3. Listing /tmp directory...")
        stdout, stderr, rc = await host.run("ls -la /tmp | head -5")
        print(f"   output:\n{stdout}")

        # Run a command with stdin
        print("\n4. Sending data via stdin...")
        stdout, stderr, rc = await host.run("cat", stdin="Hello from FTL!")
        print(f"   echoed: {stdout}")


async def example_file_operations():
    """Demonstrate remote file operations via SFTP."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Remote File Operations (SFTP)")
    print("=" * 60)

    host = SSHHost(**SSH_CONFIG)

    async with host:
        # Write a file to remote
        print("\n1. Writing file to remote host...")
        await host.write_file("/tmp/ftl_test.txt", b"Hello from FTL2!")
        print("   Created /tmp/ftl_test.txt")

        # Check if file exists
        print("\n2. Checking if file exists...")
        exists = await host.has_file("/tmp/ftl_test.txt")
        print(f"   Exists: {exists}")

        # Read the file back
        print("\n3. Reading file from remote host...")
        content = await host.read_file("/tmp/ftl_test.txt")
        print(f"   Content: {content.decode()}")

        # Check non-existent file
        print("\n4. Checking non-existent file...")
        exists = await host.has_file("/tmp/does_not_exist.txt")
        print(f"   Exists: {exists}")

        # Cleanup
        print("\n5. Cleaning up...")
        await host.run("rm -f /tmp/ftl_test.txt")
        print("   Removed test file")


async def example_connection_pooling():
    """Demonstrate connection pooling for efficiency."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Connection Pooling")
    print("=" * 60)

    async with SSHConnectionPool() as pool:
        # Get connection (creates new)
        print("\n1. Getting first connection...")
        host1 = await pool.get(**SSH_CONFIG)
        await host1.connect()
        stdout, _, _ = await host1.run("echo 'connection 1'")
        print(f"   Result: {stdout.strip()}")

        # Get same connection (reuses)
        print("\n2. Getting same connection (should reuse)...")
        host2 = await pool.get(**SSH_CONFIG)
        print(f"   Same host object: {host1 is host2}")

        # Run multiple commands (all use same connection)
        print("\n3. Running 5 commands on pooled connection...")
        for i in range(5):
            stdout, _, _ = await host1.run(f"echo 'command {i+1}'")
            print(f"   {stdout.strip()}")


async def example_concurrent_execution():
    """Demonstrate concurrent execution on multiple hosts."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Concurrent Execution")
    print("=" * 60)

    # Note: In a real scenario, these would be different hosts
    # For this demo, we simulate multiple hosts with the same container

    print("\n1. Running command on 'multiple hosts' concurrently...")
    print("   (Using same container but demonstrating the pattern)")

    # Create multiple host objects (in real use, different hostnames)
    hosts = []
    for i in range(3):
        host = SSHHost(**SSH_CONFIG)
        hosts.append(host)

    # Run commands concurrently
    async def run_on_host(host, host_id):
        async with host:
            stdout, _, rc = await host.run(f"echo 'Response from host {host_id}'")
            return host_id, stdout.strip(), rc

    tasks = [run_on_host(h, i) for i, h in enumerate(hosts)]
    results = await asyncio.gather(*tasks)

    for host_id, stdout, rc in results:
        print(f"   Host {host_id}: {stdout} (rc={rc})")


async def example_convenience_functions():
    """Demonstrate convenience functions for one-off operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Convenience Functions")
    print("=" * 60)

    # One-off command with ssh_run
    print("\n1. Using ssh_run() for one-off command...")
    stdout, stderr, rc = await ssh_run(
        hostname=SSH_CONFIG["hostname"],
        command="date",
        port=SSH_CONFIG["port"],
        username=SSH_CONFIG["username"],
        password=SSH_CONFIG["password"],
    )
    print(f"   Date: {stdout.strip()}")

    # Run on multiple hosts with ssh_run_on_hosts
    print("\n2. Using ssh_run_on_hosts() for parallel execution...")
    # In real use, these would be different hostnames
    results = await ssh_run_on_hosts(
        hostnames=[SSH_CONFIG["hostname"]] * 3,
        command="whoami",
        port=SSH_CONFIG["port"],
        username=SSH_CONFIG["username"],
        password=SSH_CONFIG["password"],
    )
    for hostname, stdout, stderr, rc in results:
        print(f"   {hostname}: {stdout.strip()}")


async def example_error_handling():
    """Demonstrate error handling for SSH operations."""
    print("\n" + "=" * 60)
    print("EXAMPLE: Error Handling")
    print("=" * 60)

    host = SSHHost(**SSH_CONFIG)

    async with host:
        # Command that fails
        print("\n1. Running command that fails...")
        stdout, stderr, rc = await host.run("exit 42")
        print(f"   rc: {rc} (expected 42)")

        # Command that produces stderr
        print("\n2. Running command that produces stderr...")
        stdout, stderr, rc = await host.run("ls /nonexistent 2>&1 || true")
        print(f"   output: {stdout.strip()}")

        # Command with timeout
        print("\n3. Running command with short timeout...")
        stdout, stderr, rc = await host.run("sleep 0.1", timeout=5)
        print(f"   rc: {rc} (expected 0)")


async def check_docker_running():
    """Check if Docker container is running."""
    try:
        host = SSHHost(**SSH_CONFIG)
        host.config.connect_timeout = 2.0
        await host.connect()
        await host.disconnect()
        return True
    except Exception as e:
        print(f"\nError: Cannot connect to SSH container: {e}")
        print("\nPlease start the Docker container first:")
        print("  docker-compose up -d")
        print("\nThen run this script again.")
        return False


async def main():
    """Run all examples."""
    print("=" * 60)
    print("FTL MODULES - REMOTE EXECUTION EXAMPLES")
    print("=" * 60)
    print(f"\nConnecting to: {SSH_CONFIG['hostname']}:{SSH_CONFIG['port']}")

    # Check if Docker is running
    if not await check_docker_running():
        return

    # Run examples
    await example_basic_ssh()
    await example_file_operations()
    await example_connection_pooling()
    await example_concurrent_execution()
    await example_convenience_functions()
    await example_error_handling()

    print("\n" + "=" * 60)
    print("ALL REMOTE EXAMPLES COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
