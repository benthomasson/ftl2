#!/usr/bin/env python3
"""Convert an FTL2 state JSON file to a YAML inventory file.

Usage:
    python tools/ftl2_state_to_inventory.py .ftl2-state.json
    python tools/ftl2_state_to_inventory.py .ftl2-state.json -o inventory.yml

Reads the hosts section from the state file and produces a YAML
inventory grouped the same way as the original add_host() calls.
"""

import argparse
import json
import sys


def state_to_inventory(state: dict) -> str:
    """Convert state dict to YAML inventory string."""
    hosts = state.get("hosts", {})
    if not hosts:
        return "all:\n  hosts: {}\n"

    # Group hosts by their groups
    groups: dict[str, dict[str, dict]] = {}
    for host_name, host_data in hosts.items():
        host_groups = host_data.get("groups", ["ungrouped"])
        host_vars = {}
        for key in ("ansible_host", "ansible_port", "ansible_user",
                     "ansible_connection", "ansible_python_interpreter"):
            if key in host_data:
                val = host_data[key]
                # Skip defaults
                if key == "ansible_port" and val == 22:
                    continue
                if key == "ansible_connection" and val == "ssh":
                    continue
                if val:
                    host_vars[key] = val

        for group in host_groups:
            groups.setdefault(group, {})[host_name] = host_vars

    # Build YAML
    lines = ["all:"]

    # If there's only one group, put hosts directly under all
    if len(groups) == 1:
        group_name, group_hosts = next(iter(groups.items()))
        if group_name == "ungrouped":
            lines.append("  hosts:")
            for host_name, host_vars in sorted(group_hosts.items()):
                _append_host(lines, host_name, host_vars, indent=4)
            return "\n".join(lines) + "\n"

    # Multiple groups or single named group — use children
    lines.append("  children:")
    for group_name in sorted(groups):
        group_hosts = groups[group_name]
        lines.append(f"    {group_name}:")
        lines.append(f"      hosts:")
        for host_name in sorted(group_hosts):
            _append_host(lines, host_name, group_hosts[host_name], indent=8)

    return "\n".join(lines) + "\n"


def _append_host(lines: list[str], name: str, vars: dict, indent: int) -> None:
    prefix = " " * indent
    if not vars:
        lines.append(f"{prefix}{name}: {{}}")
        return
    lines.append(f"{prefix}{name}:")
    for key, val in sorted(vars.items()):
        lines.append(f"{prefix}  {key}: {val}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert FTL2 state JSON to YAML inventory"
    )
    parser.add_argument("state_file", help="Path to .ftl2-state.json")
    parser.add_argument(
        "-o", "--output", help="Output file (default: stdout)"
    )
    args = parser.parse_args()

    with open(args.state_file) as f:
        state = json.load(f)

    yaml_out = state_to_inventory(state)

    if args.output:
        with open(args.output, "w") as f:
            f.write(yaml_out)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(yaml_out, end="")


if __name__ == "__main__":
    main()
