#!/usr/bin/env python3
"""Example: FQCN Module Access.

This example demonstrates using Fully Qualified Collection Name (FQCN)
modules with the automation context manager:

    await ftl.amazon.aws.ec2_instance(instance_type="t3.micro")

The nested attribute access pattern enables clean, readable syntax
for collection modules.

Run with: uv run python example_fqcn_modules.py

Note: Actual AWS modules require the amazon.aws collection installed.
"""

import asyncio

from ftl2 import automation, AutomationContext
from ftl2.automation import NamespaceProxy


async def example_namespace_proxy_basics():
    """Understanding the NamespaceProxy."""
    print("\n" + "=" * 60)
    print("Example 1: NamespaceProxy Basics")
    print("=" * 60)

    context = AutomationContext()

    # When you access an unknown attribute, you get a NamespaceProxy
    amazon = context.amazon
    print(f"ftl.amazon -> {amazon}")
    print(f"Type: {type(amazon).__name__}")

    # Chain further into the namespace
    aws = context.amazon.aws
    print(f"ftl.amazon.aws -> {aws}")

    # Final module name
    ec2 = context.amazon.aws.ec2_instance
    print(f"ftl.amazon.aws.ec2_instance -> {ec2}")

    # The proxy is callable
    print(f"Is callable: {callable(ec2)}")


async def example_fqcn_syntax():
    """Demonstrate FQCN syntax patterns."""
    print("\n" + "=" * 60)
    print("Example 2: FQCN Syntax Patterns")
    print("=" * 60)

    async with automation() as ftl:
        # Simple modules work as before
        print("Simple module access:")
        print(f"  ftl.file -> {type(ftl.file).__name__}")
        print(f"  ftl.command -> {type(ftl.command).__name__}")

        # FQCN modules use namespace proxies
        print("\nFQCN module access:")
        print(f"  ftl.amazon -> {type(ftl.amazon).__name__}")
        print(f"  ftl.amazon.aws -> {type(ftl.amazon.aws).__name__}")
        print(f"  ftl.amazon.aws.ec2_instance -> {type(ftl.amazon.aws.ec2_instance).__name__}")

        # ansible.builtin modules
        print(f"  ftl.ansible.builtin.debug -> {type(ftl.ansible.builtin.debug).__name__}")


async def example_fqcn_execution():
    """Show how FQCN modules would be executed."""
    print("\n" + "=" * 60)
    print("Example 3: FQCN Module Execution Pattern")
    print("=" * 60)

    print("""
The syntax for executing FQCN modules:

    async with automation() as ftl:
        # Simple module
        await ftl.file(path="/tmp/test", state="touch")

        # FQCN module (e.g., AWS EC2)
        result = await ftl.amazon.aws.ec2_instance(
            name="my-instance",
            instance_type="t3.micro",
            image_id="ami-12345678",
            wait=True,
        )

        # ansible.builtin module
        await ftl.ansible.builtin.debug(msg="Hello from FQCN!")

        # community collection
        await ftl.community.general.slack(
            token=ftl.secrets["SLACK_TOKEN"],
            channel="#deployments",
            msg="Deployment complete!",
        )
    """)


async def example_mixed_usage():
    """Mix simple and FQCN modules in same script."""
    print("\n" + "=" * 60)
    print("Example 4: Mixed Simple and FQCN Usage")
    print("=" * 60)

    print("""
Example script mixing both styles:

    async with automation(
        secrets=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    ) as ftl:
        # Simple local modules (FTL native, 250x faster)
        await ftl.file(path="/tmp/deploy", state="directory")
        await ftl.copy(src="app.tar.gz", dest="/tmp/deploy/")

        # FQCN modules (Ansible collections)
        await ftl.amazon.aws.ec2_instance(
            name="web-server",
            instance_type="t3.micro",
        )

        await ftl.amazon.aws.ec2_security_group(
            name="web-sg",
            rules=[{"proto": "tcp", "from_port": 80, "to_port": 80}],
        )

        # Check for any errors
        if ftl.failed:
            for e in ftl.errors:
                print(f"Error: {e.error}")
    """)


async def example_namespace_paths():
    """Show various namespace path patterns."""
    print("\n" + "=" * 60)
    print("Example 5: Common Collection Paths")
    print("=" * 60)

    context = AutomationContext()

    # Common collection namespaces
    namespaces = [
        ("ansible.builtin.file", "Core Ansible file module"),
        ("ansible.builtin.command", "Core Ansible command module"),
        ("amazon.aws.ec2_instance", "AWS EC2 instance management"),
        ("amazon.aws.s3_bucket", "AWS S3 bucket management"),
        ("community.general.slack", "Slack notifications"),
        ("community.docker.docker_container", "Docker container management"),
        ("kubernetes.core.k8s", "Kubernetes resources"),
    ]

    print("FQCN paths and their purposes:")
    for fqcn, description in namespaces:
        parts = fqcn.split(".")
        proxy = context
        for part in parts:
            proxy = getattr(proxy, part)
        print(f"  ftl.{fqcn}")
        print(f"    -> {description}")
        print(f"    -> {proxy}")
        print()


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - FQCN Module Access")
    print("=" * 60)
    print("Demonstrates Fully Qualified Collection Name support")

    await example_namespace_proxy_basics()
    await example_fqcn_syntax()
    await example_fqcn_execution()
    await example_mixed_usage()
    await example_namespace_paths()

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print("""
FQCN Module Access:
  - Simple modules: ftl.file, ftl.command, ftl.copy
  - FQCN modules: ftl.amazon.aws.ec2_instance

How it works:
  1. ftl.amazon returns a NamespaceProxy("amazon")
  2. .aws returns NamespaceProxy("amazon.aws")
  3. .ec2_instance returns NamespaceProxy("amazon.aws.ec2_instance")
  4. Calling it: (...) executes the module via context.execute()

Benefits:
  - Clean, readable syntax
  - Works with any Ansible collection
  - Same async/await pattern as simple modules
  - Full IDE autocomplete support for known modules
""")


if __name__ == "__main__":
    asyncio.run(main())
