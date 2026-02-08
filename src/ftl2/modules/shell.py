#!/usr/bin/env python3
# FTL_MODULE
"""
Shell module - Execute shell commands.

Runs a command through the shell and returns the output.

Arguments:
  cmd (str, required): The shell command to execute

Returns:
  changed (bool): Always True (commands are assumed to make changes)
  stdout (str): Standard output from the command
  stderr (str): Standard error from the command
  rc (int): Return code from the command

Idempotent: No
"""

import json
import subprocess
import sys


def main():
    """Execute shell module."""
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to parse JSON arguments: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Get the command
    cmd = args.get("cmd")
    if not cmd:
        result = {
            "failed": True,
            "msg": "Missing required argument: cmd",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Execute the command
    try:
        process = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        result = {
            "changed": True,  # Assume commands make changes
            "stdout": process.stdout,
            "stderr": process.stderr,
            "rc": process.returncode,
        }

        # If command failed, mark as failed
        if process.returncode != 0:
            result["failed"] = True
            result["msg"] = f"Command failed with return code {process.returncode}"

        print(json.dumps(result))
        sys.exit(0 if process.returncode == 0 else 1)

    except subprocess.TimeoutExpired:
        result = {
            "failed": True,
            "msg": "Command timed out after 300 seconds",
            "rc": -1,
        }
        print(json.dumps(result))
        sys.exit(1)

    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to execute command: {e}",
            "rc": -1,
        }
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
