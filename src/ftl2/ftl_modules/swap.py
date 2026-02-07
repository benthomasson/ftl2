"""FTL swap module - manages swap files on remote hosts.

This module is designed to run remotely via the FTL gate. It executes
on the target host and uses local commands to manage swap files.

Usage via gate:
    FTLModule message with module_args:
    {
        "path": "/swapfile",
        "size": "1G",
        "state": "present"
    }

Example from automation script:
    # Once user module registration is implemented:
    await ftl.minecraft.swap(path="/swapfile", size="1G", state="present")
"""

import asyncio
import os
import re
from typing import Any


async def run(cmd: str) -> tuple[str, str, int]:
    """Run a shell command and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode(), proc.returncode or 0


def parse_size(size: str) -> int:
    """Parse size string like '1G', '512M' to megabytes."""
    units = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([KMGT])?B?$", size.upper())
    if not match:
        raise ValueError(f"Invalid size format: {size}")
    value = float(match.group(1))
    unit = match.group(2) or "M"
    return int(value * units[unit])


async def is_swap_active(path: str) -> bool:
    """Check if swap file is currently active."""
    stdout, _, _ = await run("cat /proc/swaps")
    return path in stdout


async def has_swap_signature(path: str) -> bool:
    """Check if file has swap magic bytes."""
    # Swap signature is at offset 4086, should be "SWAPSPACE2"
    stdout, _, rc = await run(
        f"dd if={path} bs=1 skip=4086 count=10 2>/dev/null"
    )
    return "SWAPSPACE2" in stdout


async def ensure_fstab_entry(path: str, priority: int | None = None) -> bool:
    """Add swap entry to /etc/fstab if not present. Returns True if changed."""
    try:
        with open("/etc/fstab", "r") as f:
            fstab = f.read()
    except FileNotFoundError:
        return False

    if path in fstab:
        return False

    opts = "sw"
    if priority is not None:
        opts += f",pri={priority}"

    entry = f"{path} none swap {opts} 0 0\n"

    with open("/etc/fstab", "a") as f:
        f.write(entry)

    return True


async def remove_fstab_entry(path: str) -> bool:
    """Remove swap entry from /etc/fstab. Returns True if changed."""
    try:
        with open("/etc/fstab", "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False

    new_lines = [line for line in lines if path not in line]

    if len(new_lines) == len(lines):
        return False

    with open("/etc/fstab", "w") as f:
        f.writelines(new_lines)

    return True


async def swap_present(
    path: str,
    size: str,
    priority: int | None = None,
    fstab: bool = True,
) -> dict[str, Any]:
    """Ensure swap file exists and is active."""
    size_mb = parse_size(size)
    changed = False

    # Create file if missing
    if not os.path.exists(path):
        _, stderr, rc = await run(
            f"dd if=/dev/zero of={path} bs=1M count={size_mb} status=none"
        )
        if rc != 0:
            return {"failed": True, "msg": f"Failed to create swap file: {stderr}"}

        await run(f"chmod 600 {path}")
        changed = True

    # Format if needed
    if not await has_swap_signature(path):
        _, stderr, rc = await run(f"mkswap {path}")
        if rc != 0:
            return {"failed": True, "msg": f"mkswap failed: {stderr}"}
        changed = True

    # Activate if needed
    if not await is_swap_active(path):
        priority_opt = f"-p {priority}" if priority is not None else ""
        _, stderr, rc = await run(f"swapon {priority_opt} {path}")
        if rc != 0:
            return {"failed": True, "msg": f"swapon failed: {stderr}"}
        changed = True

    # Handle fstab
    if fstab:
        fstab_changed = await ensure_fstab_entry(path, priority)
        changed = changed or fstab_changed

    return {
        "changed": changed,
        "path": path,
        "state": "present",
        "active": True,
        "size_mb": size_mb,
    }


async def swap_absent(path: str, fstab: bool = True) -> dict[str, Any]:
    """Ensure swap file does not exist."""
    changed = False

    # Deactivate if active
    if await is_swap_active(path):
        _, stderr, rc = await run(f"swapoff {path}")
        if rc != 0:
            return {"failed": True, "msg": f"swapoff failed: {stderr}"}
        changed = True

    # Remove file if exists
    if os.path.exists(path):
        os.remove(path)
        changed = True

    # Remove from fstab
    if fstab:
        fstab_changed = await remove_fstab_entry(path)
        changed = changed or fstab_changed

    return {
        "changed": changed,
        "path": path,
        "state": "absent",
        "active": False,
    }


async def main(args: dict[str, Any]) -> dict[str, Any]:
    """Main entry point for FTL module execution.

    Args:
        args: Module arguments dict containing:
            - path: Path to swap file (required)
            - size: Size like "1G", "512M" (required for state=present)
            - state: "present" or "absent" (default: "present")
            - priority: Swap priority -1 to 32767 (optional)
            - fstab: Add/remove from /etc/fstab (default: True)

    Returns:
        Result dict with 'changed', 'path', 'state', etc.
    """
    path = args.get("path")
    if not path:
        return {"failed": True, "msg": "path is required"}

    state = args.get("state", "present")
    size = args.get("size")
    priority = args.get("priority")
    fstab = args.get("fstab", True)

    if state == "present":
        if not size:
            return {"failed": True, "msg": "size is required when state=present"}
        return await swap_present(path, size, priority, fstab)

    elif state == "absent":
        return await swap_absent(path, fstab)

    else:
        return {"failed": True, "msg": f"Invalid state: {state}"}


# Allow running directly for testing
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            args = json.load(f)
    else:
        args = {"path": "/swapfile", "size": "1G", "state": "present"}

    result = asyncio.run(main(args))
    print(json.dumps(result, indent=2))
