#!/usr/bin/env python3
"""Example: GCP infrastructure provisioning with FTL2.

Demonstrates:
1. Creating a VPC network and subnet
2. Creating firewall rules
3. Provisioning a Compute Engine instance
4. Adding the instance as a dynamic host
5. Configuring the instance remotely

Prerequisites:
    # Install the google.cloud collection
    ansible-galaxy collection install google.cloud

    # Install Python dependencies
    uv pip install google-auth google-cloud-compute google-api-python-client

    # Authenticate (one of):
    export GCP_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
    # or
    gcloud auth application-default login

Usage:
    export GCP_PROJECT=my-project-id
    export GCP_AUTH_KIND=serviceaccount  # or application
    export GCP_SERVICE_ACCOUNT_FILE=/path/to/sa.json  # if serviceaccount
    uv run python example_gcp_provision.py
"""

import asyncio

from ftl2.automation import automation


async def main():
    bindings = {
        "google.cloud.*": {
            "project": "GCP_PROJECT",
            "auth_kind": "GCP_AUTH_KIND",
            "service_account_file": "GCP_SERVICE_ACCOUNT_FILE",
        },
    }

    async with automation(
        secret_bindings=bindings,
        state_file=".ftl2-gcp-state.json",
        verbose=True,
    ) as ftl:
        # ===========================================
        # 1. Create a VPC network
        # ===========================================
        print("\n=== Creating VPC network ===")
        network = await ftl.google.cloud.gcp_compute_network(
            name="ftl2-example-network",
            auto_create_subnetworks=False,
            state="present",
        )

        # ===========================================
        # 2. Create a subnet
        # ===========================================
        print("\n=== Creating subnet ===")
        subnet = await ftl.google.cloud.gcp_compute_subnetwork(
            name="ftl2-example-subnet",
            network=network.output["selfLink"],
            ip_cidr_range="10.0.1.0/24",
            region="us-central1",
            state="present",
        )

        # ===========================================
        # 3. Create firewall rules
        # ===========================================
        print("\n=== Creating firewall rules ===")
        await ftl.google.cloud.gcp_compute_firewall(
            name="ftl2-allow-ssh",
            network=network.output["selfLink"],
            allowed=[{"ip_protocol": "tcp", "ports": ["22"]}],
            source_ranges=["0.0.0.0/0"],
            target_tags=["ftl2-ssh"],
            state="present",
        )

        await ftl.google.cloud.gcp_compute_firewall(
            name="ftl2-allow-http",
            network=network.output["selfLink"],
            allowed=[{"ip_protocol": "tcp", "ports": ["80", "443"]}],
            source_ranges=["0.0.0.0/0"],
            target_tags=["ftl2-web"],
            state="present",
        )

        # ===========================================
        # 4. Create a Compute Engine instance
        # ===========================================
        print("\n=== Provisioning GCE instance ===")
        instance = await ftl.google.cloud.gcp_compute_instance(
            name="ftl2-web01",
            machine_type="e2-micro",
            zone="us-central1-a",
            disks=[
                {
                    "auto_delete": True,
                    "boot": True,
                    "initialize_params": {
                        "source_image": "projects/ubuntu-os-cloud/global/images/family/ubuntu-2404-lts-amd64",
                        "disk_size_gb": 20,
                    },
                }
            ],
            network_interfaces=[
                {
                    "network": network.output["selfLink"],
                    "subnetwork": subnet.output["selfLink"],
                    "access_configs": [
                        {"name": "External NAT", "type": "ONE_TO_ONE_NAT"},
                    ],
                }
            ],
            tags={"items": ["ftl2-ssh", "ftl2-web"]},
            metadata={"items": [{"key": "enable-oslogin", "value": "TRUE"}]},
            state="present",
        )

        # ===========================================
        # 5. Register as dynamic host and configure
        # ===========================================
        external_ip = instance.output["networkInterfaces"][0]["accessConfigs"][0]["natIP"]
        print(f"\n=== Instance IP: {external_ip} ===")

        ftl.add_host(
            hostname="web01",
            ansible_host=external_ip,
            ansible_user="ubuntu",
            groups=["webservers"],
        )

        # Configure the instance
        print("\n=== Configuring instance ===")
        await ftl.web01.apt(name="nginx", state="present", update_cache=True)
        await ftl.web01.service(name="nginx", state="started", enabled=True)
        await ftl.web01.copy(
            content="<h1>Deployed with FTL2</h1>\n",
            dest="/var/www/html/index.html",
        )

        print(f"\n=== Done! Visit http://{external_ip} ===")
        print(f"Total results: {len(ftl.results)}")
        print(f"All succeeded: {not ftl.failed}")


if __name__ == "__main__":
    asyncio.run(main())
