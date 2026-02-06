#!/usr/bin/env python3
"""
File module - Manage files and directories.

Creates, modifies, or removes files and directories.

Arguments:
  path (str, required): Path to the file or directory
  state (str, required): Desired state
    - file: Ensure a file exists (fail if it doesn't)
    - directory: Ensure a directory exists
    - touch: Create an empty file if it doesn't exist
    - absent: Ensure file/directory doesn't exist
  mode (str, optional): File permissions in octal (e.g., "0644", "0755")

Returns:
  changed (bool): Whether any changes were made
  path (str): The path that was operated on
  state (str): The final state

Idempotent: Yes
Backup-Capable: Yes
Backup-Paths: path
Backup-Trigger: delete
"""

import json
import os
import stat
import sys
import shutil


def main():
    """Execute file module."""
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to parse JSON arguments: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Get required arguments
    path = args.get("path")
    state_arg = args.get("state")
    mode = args.get("mode")

    if not path:
        result = {
            "failed": True,
            "msg": "Missing required argument: path",
        }
        print(json.dumps(result))
        sys.exit(1)

    if not state_arg:
        result = {
            "failed": True,
            "msg": "Missing required argument: state",
        }
        print(json.dumps(result))
        sys.exit(1)

    changed = False

    try:
        # Handle different states
        if state_arg == "touch":
            # Create file if it doesn't exist
            if not os.path.exists(path):
                open(path, "a").close()
                changed = True
            elif os.path.isdir(path):
                result = {
                    "failed": True,
                    "msg": f"Path exists but is a directory: {path}",
                }
                print(json.dumps(result))
                sys.exit(1)

        elif state_arg == "file":
            # Ensure file exists
            if not os.path.exists(path):
                result = {
                    "failed": True,
                    "msg": f"File does not exist: {path}",
                }
                print(json.dumps(result))
                sys.exit(1)
            elif os.path.isdir(path):
                result = {
                    "failed": True,
                    "msg": f"Path exists but is a directory: {path}",
                }
                print(json.dumps(result))
                sys.exit(1)

        elif state_arg == "directory":
            # Create directory if it doesn't exist
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
                changed = True
            elif not os.path.isdir(path):
                result = {
                    "failed": True,
                    "msg": f"Path exists but is not a directory: {path}",
                }
                print(json.dumps(result))
                sys.exit(1)

        elif state_arg == "absent":
            # Remove file or directory if it exists
            if os.path.exists(path):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                changed = True

        else:
            result = {
                "failed": True,
                "msg": f"Invalid state: {state_arg}. Must be one of: file, directory, touch, absent",
            }
            print(json.dumps(result))
            sys.exit(1)

        # Set permissions if specified
        if mode and os.path.exists(path):
            # Convert mode string to integer
            mode_int = int(mode, 8)
            current_mode = stat.S_IMODE(os.stat(path).st_mode)

            if current_mode != mode_int:
                os.chmod(path, mode_int)
                changed = True

        result = {
            "changed": changed,
            "path": path,
            "state": state_arg,
        }

        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to manage file: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
