#!/usr/bin/env python3
"""Example: Phase 3 - Secrets Management.

This example demonstrates secure secrets handling in the automation context:
- Loading secrets from environment variables
- Safe access via ftl.secrets
- Checking for secret existence
- Safe string representations that never expose values

Run with: uv run python example_phase3_secrets.py

Note: Set environment variables before running:
    export API_KEY="your-api-key"
    export DATABASE_URL="postgres://..."
"""

import asyncio
import os

from ftl2 import automation, AutomationContext


async def example_basic_secrets():
    """Basic secrets access."""
    print("\n" + "=" * 60)
    print("Example 1: Basic Secrets Access")
    print("=" * 60)

    # Set a test secret for demonstration
    os.environ["DEMO_API_KEY"] = "sk-demo-12345"

    async with automation(secrets=["DEMO_API_KEY"]) as ftl:
        # Access secret value
        api_key = ftl.secrets["DEMO_API_KEY"]
        print(f"API key loaded: {api_key[:8]}...")  # Only show prefix

        # Use in module call (example: would use in headers)
        print(f"Would use key in API calls")


async def example_secrets_with_defaults():
    """Using get() with default values."""
    print("\n" + "=" * 60)
    print("Example 2: Secrets with Defaults")
    print("=" * 60)

    # Set only one secret
    os.environ["CONFIG_VALUE"] = "production"

    async with automation(secrets=["CONFIG_VALUE", "OPTIONAL_VALUE"]) as ftl:
        # Existing secret
        config = ftl.secrets.get("CONFIG_VALUE")
        print(f"CONFIG_VALUE: {config}")

        # Missing secret with default
        optional = ftl.secrets.get("OPTIONAL_VALUE", "default_value")
        print(f"OPTIONAL_VALUE: {optional}")

        # Unrequested secret with default
        other = ftl.secrets.get("NOT_REQUESTED", "fallback")
        print(f"NOT_REQUESTED: {other}")


async def example_checking_secrets():
    """Checking which secrets are available."""
    print("\n" + "=" * 60)
    print("Example 3: Checking Secrets Availability")
    print("=" * 60)

    # Set some secrets
    os.environ["AWS_ACCESS_KEY_ID"] = "AKIAIOSFODNN7EXAMPLE"
    # AWS_SECRET_ACCESS_KEY intentionally not set

    async with automation(secrets=["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"]) as ftl:
        # Check which secrets exist
        print("Checking secrets:")
        for name in ftl.secrets.keys():
            if name in ftl.secrets:
                print(f"  {name}: SET")
            else:
                print(f"  {name}: NOT SET")

        # Use contains check
        if "AWS_ACCESS_KEY_ID" in ftl.secrets:
            print("\nAWS credentials partially available")

        if "AWS_SECRET_ACCESS_KEY" not in ftl.secrets:
            print("Warning: AWS_SECRET_ACCESS_KEY is missing!")


async def example_secrets_inspection():
    """Inspecting secrets metadata safely."""
    print("\n" + "=" * 60)
    print("Example 4: Safe Secrets Inspection")
    print("=" * 60)

    os.environ["DB_PASSWORD"] = "super_secret_password_123"
    os.environ["REDIS_URL"] = "redis://localhost:6379"

    async with automation(secrets=["DB_PASSWORD", "REDIS_URL", "MISSING_KEY"]) as ftl:
        # Safe string representation
        print(f"Secrets object: {ftl.secrets}")
        print(f"String form: {str(ftl.secrets)}")

        # Metadata access
        print(f"\nRequested secrets: {ftl.secrets.keys()}")
        print(f"Loaded secrets: {ftl.secrets.loaded_keys()}")
        print(f"Number loaded: {len(ftl.secrets)}")


async def example_conditional_logic():
    """Using secrets in conditional logic."""
    print("\n" + "=" * 60)
    print("Example 5: Conditional Logic with Secrets")
    print("=" * 60)

    # Simulate different environments
    os.environ["SLACK_WEBHOOK"] = "https://hooks.slack.com/services/..."

    async with automation(secrets=["SLACK_WEBHOOK", "PAGERDUTY_KEY"]) as ftl:
        # Conditional notification
        if "SLACK_WEBHOOK" in ftl.secrets:
            print("Slack notifications: ENABLED")
            # Would use: await ftl.uri(url=ftl.secrets["SLACK_WEBHOOK"], method="POST", body=...)
        else:
            print("Slack notifications: DISABLED (no webhook)")

        if "PAGERDUTY_KEY" in ftl.secrets:
            print("PagerDuty alerts: ENABLED")
        else:
            print("PagerDuty alerts: DISABLED (no key)")


async def example_secrets_with_modules():
    """Using secrets with FTL modules."""
    print("\n" + "=" * 60)
    print("Example 6: Secrets with Module Calls")
    print("=" * 60)

    os.environ["GITHUB_TOKEN"] = "ghp_xxxxxxxxxxxx"

    async with automation(secrets=["GITHUB_TOKEN"]) as ftl:
        # In a real scenario, you'd use the secret in module calls
        print("GitHub token available for API calls")

        # Example: Create a file with config (not the secret itself!)
        # This demonstrates the pattern - don't write secrets to files
        result = await ftl.command(cmd="echo 'Config loaded successfully'")
        print(f"Command output: {result.get('stdout', '').strip()}")

        # The secret would be used like:
        # await ftl.uri(
        #     url="https://api.github.com/repos/owner/repo",
        #     headers={"Authorization": f"token {ftl.secrets['GITHUB_TOKEN']}"}
        # )


async def example_error_handling():
    """Handling missing secrets gracefully."""
    print("\n" + "=" * 60)
    print("Example 7: Error Handling")
    print("=" * 60)

    async with automation(secrets=["REQUIRED_KEY"]) as ftl:
        # Try to access missing secret
        try:
            value = ftl.secrets["REQUIRED_KEY"]
            print(f"Got value: {value}")
        except KeyError as e:
            print(f"Expected error: {e}")

        # Try to access unrequested secret
        try:
            value = ftl.secrets["NOT_IN_LIST"]
        except KeyError as e:
            print(f"Expected error: {e}")


async def example_context_creation():
    """Creating context directly with secrets."""
    print("\n" + "=" * 60)
    print("Example 8: Direct Context Creation")
    print("=" * 60)

    os.environ["DIRECT_SECRET"] = "direct_value"

    # Create context directly (not as context manager)
    context = AutomationContext(secrets=["DIRECT_SECRET", "OTHER"])

    print(f"Secrets: {context.secrets}")
    print(f"DIRECT_SECRET available: {'DIRECT_SECRET' in context.secrets}")
    print(f"OTHER available: {'OTHER' in context.secrets}")


async def main():
    """Run all examples."""
    print("FTL2 Automation Context - Phase 3: Secrets Management")
    print("=" * 60)
    print("Demonstrates secure handling of sensitive configuration")

    await example_basic_secrets()
    await example_secrets_with_defaults()
    await example_checking_secrets()
    await example_secrets_inspection()
    await example_conditional_logic()
    await example_secrets_with_modules()
    await example_error_handling()
    await example_context_creation()

    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nKey takeaways:")
    print("- Declare secrets upfront: automation(secrets=['KEY1', 'KEY2'])")
    print("- Access with: ftl.secrets['KEY']")
    print("- Check existence with: 'KEY' in ftl.secrets")
    print("- Get with default: ftl.secrets.get('KEY', 'default')")
    print("- Secret values are NEVER logged or shown in repr/str")


if __name__ == "__main__":
    asyncio.run(main())
