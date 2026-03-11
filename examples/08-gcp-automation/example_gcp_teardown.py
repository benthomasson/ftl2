#!/usr/bin/env python3
"""Example: Tear down GCP resources created by example_gcp_provision.py.

Usage:
    export GCP_PROJECT=my-project-id
    export GCP_AUTH_KIND=serviceaccount
    export GCP_SERVICE_ACCOUNT_FILE=/path/to/sa.json
    uv run python example_gcp_teardown.py
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

    async with automation(secret_bindings=bindings, verbose=True) as ftl:
        # Delete in reverse dependency order
        print("=== Tearing down GCP resources ===")

        await ftl.google.cloud.gcp_compute_instance(
            name="ftl2-web01",
            zone="us-central1-a",
            state="absent",
        )

        await ftl.google.cloud.gcp_compute_firewall(
            name="ftl2-allow-http",
            state="absent",
        )

        await ftl.google.cloud.gcp_compute_firewall(
            name="ftl2-allow-ssh",
            state="absent",
        )

        await ftl.google.cloud.gcp_compute_subnetwork(
            name="ftl2-example-subnet",
            region="us-central1",
            state="absent",
        )

        await ftl.google.cloud.gcp_compute_network(
            name="ftl2-example-network",
            state="absent",
        )

        print("\n=== All resources deleted ===")
        print(f"All succeeded: {not ftl.failed}")


if __name__ == "__main__":
    asyncio.run(main())
