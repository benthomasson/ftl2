#!/usr/bin/env python3
# FTL_MODULE
"""
Ping module - Test connectivity and basic module execution.

This is a simple connectivity test that always succeeds if it can execute.
Similar to Ansible's ping module.

Arguments:
  data (str, optional): Data to return in the ping response. Default: "pong"

Returns:
  changed (bool): Always False (ping doesn't modify anything)
  ping (str): The response data (echoes back the input data)

Idempotent: Yes
"""

import json
import sys


def main():
    """Execute ping module."""
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to parse JSON arguments: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Get the data argument, default to "pong"
    data = args.get("data", "pong")

    # Return success with ping response
    result = {
        "changed": False,
        "ping": data,
    }

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
