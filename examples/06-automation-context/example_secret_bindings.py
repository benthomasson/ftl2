#!/usr/bin/env python3
"""Example: Secret bindings for automatic secret injection.

Secret bindings let you inject secrets into modules without the script
ever seeing the actual values. This is a security best practice - the
script only specifies WHICH secret to use, not the secret itself.

Usage:
    export SLACK_TOKEN="xoxb-your-token"
    export AWS_ACCESS_KEY_ID="AKIA..."
    export AWS_SECRET_ACCESS_KEY="..."
    python example_secret_bindings.py
"""

import asyncio

from ftl2.automation import automation


async def main():
    # Define which secrets go to which modules
    # Pattern: module_name or glob pattern -> {param: env_var}
    bindings = {
        # Exact module match
        "community.general.slack": {
            "token": "SLACK_TOKEN",
        },
        # Glob pattern for all AWS modules
        "amazon.aws.*": {
            "aws_access_key_id": "AWS_ACCESS_KEY_ID",
            "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
        },
        # Multiple modules with same credentials
        "community.postgresql.*": {
            "login_password": "POSTGRES_PASSWORD",
        },
    }

    async with automation(secret_bindings=bindings, verbose=True) as ftl:
        # The slack module gets token injected automatically
        # The script never sees the actual token value!
        await ftl.community.general.slack(
            channel="#deployments",
            msg="Deployment starting...",
        )

        # AWS modules get credentials injected automatically
        await ftl.amazon.aws.ec2_instance(
            instance_id="i-1234567890abcdef0",
            state="running",
            # aws_access_key_id and aws_secret_access_key are
            # injected automatically - not in the script!
        )

        # You can still use manual secrets when needed
        # (e.g., for modules not covered by bindings)
        # await ftl.some_module(
        #     api_key=ftl.secrets["OTHER_API_KEY"],
        # )


async def comparison_example():
    """Compare old vs new approach."""

    # OLD APPROACH (secrets in script - bad!)
    # -----------------------------------------
    # async with automation(secrets=["SLACK_TOKEN"]) as ftl:
    #     await ftl.community.general.slack(
    #         channel="#test",
    #         msg="Hello",
    #         token=ftl.secrets["SLACK_TOKEN"],  # <-- Secret in script!
    #     )

    # NEW APPROACH (secrets injected - good!)
    # -----------------------------------------
    async with automation(
        secret_bindings={
            "community.general.slack": {"token": "SLACK_TOKEN"},
        }
    ) as ftl:
        await ftl.community.general.slack(
            channel="#test",
            msg="Hello",
            # token injected automatically - script never sees it!
        )


if __name__ == "__main__":
    asyncio.run(main())
