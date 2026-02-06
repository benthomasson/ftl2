#!/usr/bin/env python3
"""
Setup module - Gather facts about the system.

Collects basic system information similar to Ansible's setup module.

Arguments:
  None

Returns:
  changed (bool): Always False (setup doesn't modify anything)
  ansible_facts (dict): Dictionary of gathered system facts

Idempotent: Yes
"""

import json
import os
import platform
import sys


def gather_facts():
    """Gather system facts."""
    import socket
    import getpass

    facts = {
        "system": platform.system(),
        "node": platform.node(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "hostname": socket.gethostname(),
        "user": getpass.getuser(),
        "cwd": os.getcwd(),
        "env": dict(os.environ),
    }

    return facts


def main():
    """Execute setup module."""
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to parse JSON arguments: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Gather facts
    try:
        facts = gather_facts()
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to gather facts: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Return success with facts
    result = {
        "changed": False,
        "ansible_facts": facts,
    }

    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
