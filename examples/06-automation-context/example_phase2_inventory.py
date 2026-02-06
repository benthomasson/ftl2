#!/usr/bin/env python3
"""Example: Phase 2 - Inventory Integration.

This example demonstrates inventory integration in the automation context:
- Loading inventory from YAML files
- Accessing hosts and groups via ftl.hosts
- Remote execution with ftl.run_on()
- Multi-host concurrent execution

Run with: uv run python example_phase2_inventory.py

Note: Remote examples require Docker. See docker-compose.yml
"""

import asyncio
import tempfile
from pathlib import Path

from ftl2 import automation, AutomationContext


async def example_default_localhost():
    """Default behavior with localhost."""
    print("\n" + "=" * 60)
    print("Example 1: Default Localhost Inventory")
    print("=" * 60)

    async with automation() as ftl:
        # Default inventory includes localhost
        print(f"Hosts: {list(ftl.hosts)}")
        print(f"Groups: {ftl.hosts.groups}")
        print(f"All hosts: {[h.name for h in ftl.hosts.all]}")

        # Check if localhost exists
        if "localhost" in ftl.hosts:
            print("localhost is available")
            hosts = ftl.hosts["localhost"]
            print(f"  Host: {hosts[0].name}")
            print(f"  Connection: {hosts[0].ansible_connection}")


async def example_inventory_from_dict():
    """Create inventory from a dictionary."""
    print("\n" + "=" * 60)
    print("Example 2: Inventory from Dictionary")
    print("=" * 60)

    # Define inventory as a dict (useful for dynamic inventories)
    inventory = {
        "webservers": {
            "hosts": {
                "web01": {"ansible_host": "192.168.1.10", "ansible_port": 22},
                "web02": {"ansible_host": "192.168.1.11", "ansible_port": 22},
            }
        },
        "databases": {
            "hosts": {
                "db01": {"ansible_host": "192.168.1.20"},
                "db02": {"ansible_host": "192.168.1.21"},
            }
        },
        "loadbalancers": {
            "hosts": {
                "lb01": {"ansible_host": "192.168.1.5"},
            }
        },
    }

    context = AutomationContext(inventory=inventory)

    print(f"Groups: {context.hosts.groups}")
    print(f"Total hosts: {len(context.hosts)}")

    # Access by group
    print("\nWebservers:")
    for host in context.hosts["webservers"]:
        print(f"  {host.name}: {host.ansible_host}:{host.ansible_port}")

    print("\nDatabases:")
    for host in context.hosts["databases"]:
        print(f"  {host.name}: {host.ansible_host}")


async def example_inventory_from_file():
    """Load inventory from a YAML file."""
    print("\n" + "=" * 60)
    print("Example 3: Inventory from YAML File")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create an inventory file
        inv_file = Path(tmpdir) / "inventory.yml"
        inv_file.write_text("""
# Example inventory file
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_port: 22
      ansible_user: deploy
    web02:
      ansible_host: 192.168.1.11
      ansible_port: 22
      ansible_user: deploy
  vars:
    http_port: 80

databases:
  hosts:
    db01:
      ansible_host: 192.168.1.20
      ansible_user: postgres
""")

        print(f"Loading inventory from: {inv_file}")

        async with automation(inventory=str(inv_file)) as ftl:
            print(f"Groups: {ftl.hosts.groups}")
            print(f"All hosts: {list(ftl.hosts)}")

            # Access specific group
            print("\nWebservers:")
            for host in ftl.hosts["webservers"]:
                print(f"  {host.name}: {host.ansible_host} (user: {host.ansible_user})")


async def example_run_on_localhost():
    """Using run_on with localhost."""
    print("\n" + "=" * 60)
    print("Example 4: run_on with Localhost")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "run_on_test.txt"

        async with automation() as ftl:
            # run_on returns a list of results (one per host)
            results = await ftl.run_on(
                "localhost",
                "file",
                path=str(test_file),
                state="touch",
            )

            print(f"Executed on {len(results)} host(s)")
            for result in results:
                status = "OK" if result.success else "FAILED"
                changed = " (changed)" if result.changed else ""
                print(f"  [{result.host}] {status}{changed}")

            # Verify file was created
            print(f"\nFile exists: {test_file.exists()}")


async def example_run_on_host_list():
    """Using run_on with a list of hosts."""
    print("\n" + "=" * 60)
    print("Example 5: run_on with Host List")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        async with automation() as ftl:
            # Get hosts as a list
            hosts = ftl.hosts["localhost"]
            print(f"Running on {len(hosts)} host(s): {[h.name for h in hosts]}")

            results = await ftl.run_on(
                hosts,
                "command",
                cmd="echo 'Hello from run_on!'",
            )

            for result in results:
                print(f"  [{result.host}] stdout: {result.output.get('stdout', '').strip()}")


async def example_hosts_iteration():
    """Iterating over hosts."""
    print("\n" + "=" * 60)
    print("Example 6: Iterating Over Hosts")
    print("=" * 60)

    inventory = {
        "all": {
            "hosts": {
                "server1": {"ansible_host": "10.0.0.1"},
                "server2": {"ansible_host": "10.0.0.2"},
                "server3": {"ansible_host": "10.0.0.3"},
            }
        }
    }

    context = AutomationContext(inventory=inventory)

    print("All hosts:")
    for host_name in context.hosts:
        hosts = context.hosts[host_name]
        for host in hosts:
            print(f"  {host.name}: {host.ansible_host}")

    print(f"\nTotal: {len(context.hosts)} hosts")
    print(f"Host names: {context.hosts.keys()}")


async def example_mixed_local_and_remote():
    """Mixing local and remote execution."""
    print("\n" + "=" * 60)
    print("Example 7: Mixed Local and Remote Execution")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "mixed_test.txt"

        async with automation() as ftl:
            # Local execution (direct module call)
            print("Local execution:")
            result = await ftl.file(path=str(test_file), state="touch")
            print(f"  ftl.file() -> changed: {result['changed']}")

            # Remote execution (run_on)
            print("\nRemote execution (localhost):")
            results = await ftl.run_on("localhost", "command", cmd="hostname")
            for r in results:
                print(f"  run_on({r.host}) -> {r.output.get('stdout', '').strip()}")


async def example_run_on_group():
    """Running on a group of hosts."""
    print("\n" + "=" * 60)
    print("Example 8: run_on with Group Name")
    print("=" * 60)

    # Create inventory with localhost in a group
    inventory = {
        "local": {
            "hosts": {
                "localhost": {
                    "ansible_host": "127.0.0.1",
                    "ansible_connection": "local",
                }
            }
        }
    }

    async with AutomationContext(inventory=inventory) as ftl:
        print(f"Groups: {ftl.hosts.groups}")

        # Run on group by name
        results = await ftl.run_on("local", "command", cmd="echo 'Hello from group!'")

        for r in results:
            print(f"  [{r.host}] {r.output.get('stdout', '').strip()}")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - Phase 2: Inventory Integration")
    print("=" * 60)
    print("Demonstrates inventory loading and remote execution")

    await example_default_localhost()
    await example_inventory_from_dict()
    await example_inventory_from_file()
    await example_run_on_localhost()
    await example_run_on_host_list()
    await example_hosts_iteration()
    await example_mixed_local_and_remote()
    await example_run_on_group()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nNote: For real remote execution, see docker-compose.yml")
    print("and configure inventory with actual SSH hosts.")


if __name__ == "__main__":
    asyncio.run(main())
