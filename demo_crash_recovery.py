#!/usr/bin/env python3
"""Demonstration of crash recovery using audit replay.

This simulates a multi-step deployment that crashes partway through,
then recovers by replaying the successful steps.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ftl2.automation import automation, AutomationError


async def deployment_script(simulate_crash: bool = False):
    """Multi-step deployment that may crash."""

    async with automation(
        record="deployment_audit.json",
        replay="deployment_audit.json" if not simulate_crash else None,
        fail_fast=True,
        quiet=False,
    ) as ftl:
        print("\n📦 Step 1: Update system packages")
        result = await ftl.command(cmd="echo 'dnf update -y' # simulated")

        print("\n📦 Step 2: Install Java 21")
        result = await ftl.command(cmd="echo 'dnf install java-21 -y' # simulated")

        print("\n📦 Step 3: Create application directory")
        result = await ftl.file(path="/tmp/minecraft_demo", state="directory")

        if simulate_crash:
            print("\n💥 CRASH: Network timeout during file copy!")
            raise AutomationError("Simulated network failure")

        print("\n📦 Step 4: Copy server JAR")
        result = await ftl.file(path="/tmp/minecraft_demo/server.jar", state="touch")

        print("\n📦 Step 5: Set up systemd service")
        result = await ftl.command(cmd="echo 'systemctl enable minecraft' # simulated")

        print("\n📦 Step 6: Start service")
        result = await ftl.command(cmd="echo 'systemctl start minecraft' # simulated")


async def main():
    """Demonstrate crash recovery."""

    print("=" * 70)
    print("🚀 CRASH RECOVERY DEMONSTRATION")
    print("=" * 70)

    # Clean up any existing audit file
    audit_path = Path("deployment_audit.json")
    if audit_path.exists():
        audit_path.unlink()
        print("🧹 Cleaned up previous audit file\n")

    # First attempt - will crash at step 3
    print("\n" + "=" * 70)
    print("ATTEMPT 1: Running deployment (will crash at step 3)")
    print("=" * 70)

    try:
        await deployment_script(simulate_crash=True)
    except AutomationError as e:
        print(f"\n❌ Deployment failed: {e}")
        print("✓ Audit log saved with successful steps 1-3")

    input("\n⏸️  Press Enter to retry with replay enabled...")

    # Second attempt - will replay steps 1-3 and complete
    print("\n" + "=" * 70)
    print("ATTEMPT 2: Retrying with replay (will skip steps 1-3)")
    print("=" * 70)

    try:
        await deployment_script(simulate_crash=False)
        print("\n✅ Deployment completed successfully!")
        print("✓ Steps 1-3 were replayed (instant)")
        print("✓ Steps 4-6 executed normally")
    except AutomationError as e:
        print(f"\n❌ Deployment failed again: {e}")
        return 1

    # Show the audit log
    print("\n" + "=" * 70)
    print("📋 FINAL AUDIT LOG")
    print("=" * 70)

    import json
    audit_data = json.loads(audit_path.read_text())

    print(f"\nTotal actions: {len(audit_data['actions'])}")
    print(f"Success: {audit_data['success']}")

    for i, action in enumerate(audit_data['actions']):
        status = "↩ REPLAYED" if action.get('replayed') else "▶ EXECUTED"
        print(f"\n  {i+1}. {status}")
        print(f"     Module: {action['module']}")
        print(f"     Success: {action['success']}")
        print(f"     Duration: {action['duration']:.3f}s")

    # Clean up
    audit_path.unlink()

    print("\n" + "=" * 70)
    print("✅ Demo completed successfully!")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Demo interrupted")
        sys.exit(1)
