#!/usr/bin/env python3
"""
Copy module - Copy files to remote locations.

Copies a file from the source to destination. For remote execution,
the source file is base64-encoded in the arguments.

Arguments:
  src (str, required): Source file path (on controller)
  dest (str, required): Destination file path (on target)
  content (str, optional): Base64-encoded content to write (for remote execution)
  mode (str, optional): File permissions in octal (e.g., "0644")

Returns:
  changed (bool): Whether the file was copied/changed
  dest (str): Destination path
  src (str): Source path

Idempotent: Yes
Backup-Capable: Yes
Backup-Paths: dest
Backup-Trigger: modify
"""

import base64
import json
import os
import shutil
import stat
import sys


def main():
    """Execute copy module."""
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to parse JSON arguments: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)

    # Get arguments
    src = args.get("src")
    dest = args.get("dest")
    content = args.get("content")  # Base64-encoded content for remote execution
    mode = args.get("mode")

    if not dest:
        result = {
            "failed": True,
            "msg": "Missing required argument: dest",
        }
        print(json.dumps(result))
        sys.exit(1)

    if not src and not content:
        result = {
            "failed": True,
            "msg": "Either 'src' or 'content' must be provided",
        }
        print(json.dumps(result))
        sys.exit(1)

    changed = False

    try:
        # Determine if we should copy
        should_copy = True

        if content:
            # Remote execution: decode content and write to dest
            try:
                file_content = base64.b64decode(content)
            except Exception as e:
                result = {
                    "failed": True,
                    "msg": f"Failed to decode content: {e}",
                }
                print(json.dumps(result))
                sys.exit(1)

            # Check if file exists and has same content
            if os.path.exists(dest):
                with open(dest, "rb") as f:
                    existing_content = f.read()
                if existing_content == file_content:
                    should_copy = False

            if should_copy:
                with open(dest, "wb") as f:
                    f.write(file_content)
                changed = True

        else:
            # Local execution: copy from src to dest
            if not os.path.exists(src):
                result = {
                    "failed": True,
                    "msg": f"Source file not found: {src}",
                }
                print(json.dumps(result))
                sys.exit(1)

            # Check if files are different
            if os.path.exists(dest):
                import filecmp
                if filecmp.cmp(src, dest, shallow=False):
                    should_copy = False

            if should_copy:
                shutil.copy2(src, dest)
                changed = True

        # Set permissions if specified
        if mode and os.path.exists(dest):
            mode_int = int(mode, 8)
            current_mode = stat.S_IMODE(os.stat(dest).st_mode)

            if current_mode != mode_int:
                os.chmod(dest, mode_int)
                changed = True

        result = {
            "changed": changed,
            "dest": dest,
            "src": src if src else "<content>",
        }

        print(json.dumps(result))
        sys.exit(0)

    except Exception as e:
        result = {
            "failed": True,
            "msg": f"Failed to copy file: {e}",
        }
        print(json.dumps(result))
        sys.exit(1)


if __name__ == "__main__":
    main()
