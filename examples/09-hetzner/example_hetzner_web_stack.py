#!/usr/bin/env python3
"""Example: Provision and configure a web application stack on Hetzner Cloud.

Demonstrates using FTL2 with the hetzner.hcloud Ansible collection to:
1. Create an SSH key
2. Create a network with a subnet
3. Create a firewall with HTTP/SSH rules
4. Provision a cloud server
5. Attach the server to the network
6. Configure the server with nginx via SSH

Prerequisites:
    # Install Hetzner collection
    ansible-galaxy collection install hetzner.hcloud

    # Set Hetzner API token
    export HCLOUD_TOKEN="your-api-token"

    # SSH key for server access
    export HETZNER_SSH_PUBKEY_FILE="~/.ssh/id_ed25519.pub"

Usage:
    uv run python example_hetzner_web_stack.py
    uv run python example_hetzner_web_stack.py --check    # Dry run
    uv run python example_hetzner_web_stack.py --teardown  # Delete everything
"""

import asyncio
import os
import sys

from ftl2 import automation


# Configuration
SERVER_NAME = "ftl2-demo-web"
SERVER_TYPE = "cx22"  # 2 vCPU, 4GB RAM, 40GB SSD (shared)
IMAGE = "ubuntu-24.04"
LOCATION = "nbg1"  # Nuremberg
NETWORK_NAME = "ftl2-demo-net"
NETWORK_CIDR = "10.0.0.0/16"
SUBNET_CIDR = "10.0.1.0/24"
FIREWALL_NAME = "ftl2-demo-fw"
SSH_KEY_NAME = "ftl2-demo-key"


async def provision(check_mode: bool = False):
    """Provision the full Hetzner web stack."""

    async with automation(
        auto_install_deps=True,
        check_mode=check_mode,
        verbose=True,
        secret_bindings={
            "hetzner.hcloud.*": {
                "api_token": "HCLOUD_TOKEN",
            },
        },
        state_file=".ftl2-state-hetzner.json",
    ) as ftl:

        # ── 1. SSH Key ──────────────────────────────────────────
        print("\n=== Creating SSH Key ===")
        pubkey_file = os.path.expanduser(
            os.environ.get("HETZNER_SSH_PUBKEY_FILE", "~/.ssh/id_ed25519.pub")
        )
        with open(pubkey_file) as f:
            pubkey = f.read().strip()

        await ftl.hetzner.hcloud.ssh_key(
            name=SSH_KEY_NAME,
            public_key=pubkey,
            state="present",
            labels={"managed_by": "ftl2"},
        )

        # ── 2. Network + Subnet ─────────────────────────────────
        print("\n=== Creating Network ===")
        await ftl.hetzner.hcloud.network(
            name=NETWORK_NAME,
            ip_range=NETWORK_CIDR,
            state="present",
            labels={"managed_by": "ftl2"},
        )

        await ftl.hetzner.hcloud.subnetwork(
            network=NETWORK_NAME,
            ip_range=SUBNET_CIDR,
            network_zone="eu-central",
            type="cloud",
            state="present",
        )

        # ── 3. Firewall ─────────────────────────────────────────
        print("\n=== Creating Firewall ===")
        await ftl.hetzner.hcloud.firewall(
            name=FIREWALL_NAME,
            rules=[
                {
                    "description": "Allow SSH",
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "22",
                    "source_ips": ["0.0.0.0/0", "::/0"],
                },
                {
                    "description": "Allow HTTP",
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "80",
                    "source_ips": ["0.0.0.0/0", "::/0"],
                },
                {
                    "description": "Allow HTTPS",
                    "direction": "in",
                    "protocol": "tcp",
                    "port": "443",
                    "source_ips": ["0.0.0.0/0", "::/0"],
                },
                {
                    "description": "Allow ICMP",
                    "direction": "in",
                    "protocol": "icmp",
                    "source_ips": ["0.0.0.0/0", "::/0"],
                },
            ],
            state="present",
            labels={"managed_by": "ftl2"},
        )

        # ── 4. Cloud Server ─────────────────────────────────────
        print("\n=== Creating Server ===")
        server_result = await ftl.hetzner.hcloud.server(
            name=SERVER_NAME,
            server_type=SERVER_TYPE,
            image=IMAGE,
            location=LOCATION,
            ssh_keys=[SSH_KEY_NAME],
            firewalls=[FIREWALL_NAME],
            state="present",
            labels={"managed_by": "ftl2", "role": "webserver"},
        )

        # ── 5. Attach Server to Network ─────────────────────────
        print("\n=== Attaching Server to Network ===")
        await ftl.hetzner.hcloud.server_network(
            server=SERVER_NAME,
            network=NETWORK_NAME,
            state="present",
        )

        # ── 6. Get Server Info & Configure ──────────────────────
        if not check_mode and not ftl.failed:
            server_info = await ftl.hetzner.hcloud.server_info(
                name=SERVER_NAME,
            )

            if server_info and "hcloud_server_info" in server_info:
                server = server_info["hcloud_server_info"][0]
                public_ip = server["ipv4_address"]
                print(f"\nServer public IP: {public_ip}")

                # Save to state for future runs
                ftl.state.add(SERVER_NAME, {
                    "provider": "hetzner",
                    "server_type": SERVER_TYPE,
                    "location": LOCATION,
                    "public_ip": public_ip,
                    "network": NETWORK_NAME,
                })

                # Register host for immediate configuration
                ftl.add_host(
                    hostname=SERVER_NAME,
                    ansible_host=public_ip,
                    ansible_user="root",
                    groups=["webservers"],
                )

                # Wait for SSH to become available
                print("\n=== Waiting for SSH ===")
                await ftl.wait_for_ssh(SERVER_NAME)

                # ── 7. Configure the Server ──────────────────────
                print("\n=== Configuring Server ===")
                await ftl[SERVER_NAME].command(cmd="apt-get update -qq")
                await ftl[SERVER_NAME].command(cmd="apt-get install -y -qq nginx")
                await ftl[SERVER_NAME].ansible.builtin.service(
                    name="nginx",
                    state="started",
                    enabled=True,
                )

                print(f"\nWeb server ready at http://{public_ip}/")

        # ── Summary ─────────────────────────────────────────────
        print("\n=== Summary ===")
        success_count = sum(1 for r in ftl.results if r.success)
        changed_count = sum(1 for r in ftl.results if r.changed)
        print(f"Operations: {len(ftl.results)} total, {success_count} succeeded, {changed_count} changed")

        if ftl.failed:
            print("\nErrors:")
            for error in ftl.errors:
                print(f"  [{error.module}] {error.error}")
            return False

        return True


async def teardown():
    """Delete all Hetzner resources in reverse order."""

    async with automation(
        auto_install_deps=True,
        verbose=True,
        secret_bindings={
            "hetzner.hcloud.*": {
                "api_token": "HCLOUD_TOKEN",
            },
        },
        state_file=".ftl2-state-hetzner.json",
    ) as ftl:
        print("\n=== Tearing Down Hetzner Resources ===")

        # Detach from network first
        print("Detaching server from network...")
        await ftl.hetzner.hcloud.server_network(
            server=SERVER_NAME,
            network=NETWORK_NAME,
            state="absent",
        )

        # Delete server
        print("Deleting server...")
        await ftl.hetzner.hcloud.server(
            name=SERVER_NAME,
            state="absent",
        )

        # Delete firewall
        print("Deleting firewall...")
        await ftl.hetzner.hcloud.firewall(
            name=FIREWALL_NAME,
            state="absent",
        )

        # Delete subnet then network
        print("Deleting network...")
        await ftl.hetzner.hcloud.subnetwork(
            network=NETWORK_NAME,
            ip_range=SUBNET_CIDR,
            network_zone="eu-central",
            type="cloud",
            state="absent",
        )
        await ftl.hetzner.hcloud.network(
            name=NETWORK_NAME,
            state="absent",
        )

        # Delete SSH key
        print("Deleting SSH key...")
        await ftl.hetzner.hcloud.ssh_key(
            name=SSH_KEY_NAME,
            state="absent",
        )

        ftl.state.remove(SERVER_NAME)
        print("\nDone. All resources deleted.")


if __name__ == "__main__":
    check = "--check" in sys.argv
    tear = "--teardown" in sys.argv

    if tear:
        asyncio.run(teardown())
    else:
        asyncio.run(provision(check_mode=check))
