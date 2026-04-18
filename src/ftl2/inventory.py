"""Inventory management system for FTL2 automation framework.

This module provides typed inventory management with dataclasses, replacing
dictionary-based inventory structures with strongly-typed classes.
"""

import ast
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .types import HostConfig


@dataclass
class HostGroup:
    """A group of hosts in the inventory with shared variables.

    Attributes:
        name: Group name (e.g., "webservers", "databases")
        hosts: Dictionary mapping host names to HostConfig objects
        vars: Group-level variables inherited by all hosts
        children: Child group names for hierarchical structures

    Example:
        >>> group = HostGroup(
        ...     name="webservers",
        ...     hosts={"web01": HostConfig(name="web01", ansible_host="192.168.1.10")},
        ...     vars={"http_port": 80}
        ... )
    """

    name: str
    hosts: dict[str, HostConfig] = field(default_factory=dict)
    vars: dict[str, Any] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)

    def add_host(self, host: HostConfig) -> None:
        """Add a host to this group."""
        self.hosts[host.name] = host

    def get_host(self, name: str) -> HostConfig | None:
        """Get a host by name."""
        return self.hosts.get(name)

    def list_hosts(self) -> list[HostConfig]:
        """Get all hosts in this group."""
        return list(self.hosts.values())


@dataclass
class Inventory:
    """Typed inventory structure for FTL2 automation.

    Replaces dictionary-based inventory with strongly-typed structure
    for better type safety and validation.

    Attributes:
        groups: Dictionary mapping group names to HostGroup objects
        all_hosts: Cached dictionary of all unique hosts across groups

    Example:
        >>> inventory = Inventory()
        >>> web_group = HostGroup(name="webservers")
        >>> web_group.add_host(HostConfig(name="web01", ansible_host="192.168.1.10"))
        >>> inventory.add_group(web_group)
    """

    groups: dict[str, HostGroup] = field(default_factory=dict)

    def add_group(self, group: HostGroup) -> None:
        """Add a group to the inventory."""
        self.groups[group.name] = group

    def get_group(self, name: str) -> HostGroup | None:
        """Get a group by name."""
        return self.groups.get(name)

    def list_groups(self) -> list[HostGroup]:
        """Get all groups."""
        return list(self.groups.values())

    def get_all_hosts(self) -> dict[str, HostConfig]:
        """Get all unique hosts across all groups."""
        result: dict[str, HostConfig] = {}
        for group in self.groups.values():
            for host_name, host in group.hosts.items():
                if host_name not in result:
                    result[host_name] = host
        return result


def load_inventory(inventory_file: str | Path, require_hosts: bool = True) -> Inventory:
    """Load inventory from a file, auto-detecting the format.

    Supports four formats:
    - Executable scripts: run with --list, parse JSON output
    - JSON files: Ansible --list format (groups with host lists + _meta.hostvars)
    - INI files: Ansible INI format (detected by .ini/.cfg extension or [section] headers)
    - YAML files: Ansible inventory format (groups with host dicts)

    Args:
        inventory_file: Path to inventory file or executable script
        require_hosts: If True (default), raise ValueError when no hosts are
            loaded. Set to False for provisioning workflows where hosts are
            added dynamically via add_host().

    Returns:
        Inventory object with typed groups and hosts

    Raises:
        ValueError: If require_hosts is True and no hosts are loaded

    Example:
        >>> inventory = load_inventory("hosts.yml")
        >>> inventory = load_inventory("inventory.json")
        >>> inventory = load_inventory("./ec2_inventory.py")
    """
    path = Path(inventory_file) if isinstance(inventory_file, str) else inventory_file

    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")

    # Executable script — run with --list
    if os.access(path, os.X_OK) and path.suffix not in (".yml", ".yaml", ".json", ".ini", ".cfg"):
        return load_inventory_script(path, require_hosts=require_hosts)

    content = path.read_text()

    # JSON — detect by content
    stripped = content.lstrip()
    if stripped.startswith("{"):
        data = json.loads(content)
        return load_inventory_json(data, require_hosts=require_hosts)

    # INI — detect by extension or content heuristic (skip YAML extensions)
    if path.suffix in (".ini", ".cfg") or (
        path.suffix not in (".yml", ".yaml") and _is_ini_content(stripped)
    ):
        return load_inventory_ini(content, require_hosts=require_hosts)

    # YAML — existing format
    data = yaml.safe_load(content) or {}
    return _load_inventory_yaml(data, require_hosts=require_hosts)


def _process_group(
    group_name: str,
    group_data: dict[str, Any] | None,
    inventory: Inventory,
    ancestors: frozenset[str] = frozenset(),
) -> None:
    """Recursively process a group and its children into the inventory.

    Uses path-based cycle detection: ``ancestors`` tracks the chain from
    root to the current node.  A group that has already been visited via
    a *different* parent is legal (DAG) and will be merged, not rejected.
    """
    if group_name in ancestors:
        raise ValueError(f"Circular group: {group_name}")

    # Merge into an existing group if one was already created (DAG or
    # split-definition case).
    group = inventory.get_group(group_name) or HostGroup(name=group_name)

    if isinstance(group_data, dict):
        if "hosts" in group_data and isinstance(group_data["hosts"], dict):
            for host_name, host_data in group_data["hosts"].items():
                if not isinstance(host_data, dict):
                    host_data = {}
                for expanded in expand_host_range(host_name):
                    group.add_host(_host_from_vars(expanded, host_data))

        if "vars" in group_data and isinstance(group_data["vars"], dict):
            group.vars.update(group_data["vars"])

        if "children" in group_data:
            if isinstance(group_data["children"], list):
                for child in group_data["children"]:
                    if child not in group.children:
                        group.children.append(child)
            elif isinstance(group_data["children"], dict):
                child_ancestors = ancestors | {group_name}
                for child_name in group_data["children"]:
                    if child_name not in group.children:
                        group.children.append(child_name)
                for child_name, child_data in group_data["children"].items():
                    _process_group(
                        child_name, child_data, inventory, child_ancestors
                    )

    inventory.add_group(group)


def _load_inventory_yaml(
    data: dict[str, Any] | None, require_hosts: bool = True
) -> Inventory:
    """Load inventory from parsed YAML data, supporting nested all.children hierarchy.

    Args:
        data: Parsed YAML inventory data
        require_hosts: If True (default), raise ValueError when no hosts are loaded.

    Returns:
        Inventory object with typed groups and hosts
    """
    inventory = Inventory()

    if data:
        for group_name, group_data in data.items():
            _process_group(group_name, group_data, inventory)

    if require_hosts and not inventory.get_all_hosts():
        raise ValueError("No hosts loaded from inventory")

    return inventory


def _is_ini_content(content: str) -> bool:
    """Return True if content looks like an INI inventory (has [section] headers)."""
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if _INI_SECTION_RE.match(line):
            return True
    return False


def _parse_ini_host_value(value: str) -> Any:
    """Parse a host-line value using ast.literal_eval, falling back to string."""
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


_INI_SECTION_RE = re.compile(r"^\[([^\]]+)\]$")


def load_inventory_ini(content: str, require_hosts: bool = True) -> Inventory:
    """Parse an Ansible INI inventory string into an Inventory."""
    inventory = Inventory()
    current_group_name: str | None = None
    section_type = "hosts"

    def _ensure_group(name: str) -> HostGroup:
        group = inventory.get_group(name)
        if group is None:
            group = HostGroup(name=name)
            inventory.add_group(group)
        return group

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        section_match = _INI_SECTION_RE.match(line)
        if section_match:
            header = section_match.group(1)
            if header.endswith(":vars"):
                current_group_name = header[: -len(":vars")]
                section_type = "vars"
                _ensure_group(current_group_name)
            elif header.endswith(":children"):
                current_group_name = header[: -len(":children")]
                section_type = "children"
                _ensure_group(current_group_name)
            else:
                current_group_name = header
                section_type = "hosts"
                _ensure_group(current_group_name)
            continue

        group_name = current_group_name if current_group_name is not None else "ungrouped"
        group = _ensure_group(group_name)

        if section_type == "vars":
            key, _, value = line.partition("=")
            group.vars[key.strip()] = value.strip()
        elif section_type == "children":
            child_name = line.strip()
            if child_name not in group.children:
                group.children.append(child_name)
            _ensure_group(child_name)
        else:
            tokens = shlex.split(line)
            hostname = tokens[0]
            host_vars: dict[str, Any] = {}
            for token in tokens[1:]:
                key, sep, value = token.partition("=")
                if value:
                    host_vars[key] = _parse_ini_host_value(value)
                elif sep:
                    host_vars[key] = ""
            for expanded in expand_host_range(hostname):
                group.add_host(_host_from_vars(expanded, host_vars))

    if require_hosts and not inventory.get_all_hosts():
        raise ValueError("No hosts loaded from inventory")

    return inventory


def _host_from_vars(host_name: str, host_data: dict[str, Any]) -> HostConfig:
    """Create a HostConfig from a host variables dictionary.

    Args:
        host_name: The host name
        host_data: Dictionary of host variables (ansible_host, ansible_port, etc.)

    Returns:
        HostConfig with standard fields extracted and remainder in vars
    """
    standard_fields = {
        "ansible_host",
        "ansible_port",
        "ansible_user",
        "ansible_connection",
        "ansible_python_interpreter",
    }

    return HostConfig(
        name=host_name,
        ansible_host=host_data.get("ansible_host", host_name),
        ansible_port=host_data.get("ansible_port", 22),
        ansible_user=host_data.get("ansible_user", ""),
        ansible_connection=host_data.get("ansible_connection", "ssh"),
        ansible_python_interpreter=host_data.get(
            "ansible_python_interpreter", "python3"
        ),
        vars={k: v for k, v in host_data.items() if k not in standard_fields},
    )


_RANGE_RE = re.compile(
    r"\[([0-9]+):([0-9]+)(?::([0-9]+))?\]"
    r"|"
    r"\[([a-zA-Z]):([a-zA-Z])(?::([0-9]+))?\]"
)


def expand_host_range(pattern: str) -> list[str]:
    """Expand Ansible host range patterns like ``www[01:50].example.com``.

    Supports numeric ranges (``[01:50]``), alphabetic ranges (``[a:f]``),
    and stride (``[01:50:2]``).  Multiple bracket groups produce a cartesian
    product.  Brackets that don't match valid range syntax are left as
    literal characters.
    """
    matches = list(_RANGE_RE.finditer(pattern))
    if not matches:
        return [pattern]

    segments: list[list[str]] = []
    last_end = 0
    for m in matches:
        segments.append([pattern[last_end : m.start()]])

        if m.group(1) is not None:
            # Numeric range
            start, end = m.group(1), m.group(2)
            stride = int(m.group(3)) if m.group(3) else 1
            if stride == 0:
                raise ValueError(f"Invalid host range stride of 0 in '{pattern}'")
            width = max(len(start), len(end))
            vals = [str(i).zfill(width) for i in range(int(start), int(end) + 1, stride)]
        else:
            # Alpha range
            start, end = m.group(4), m.group(5)
            stride = int(m.group(6)) if m.group(6) else 1
            if stride == 0:
                raise ValueError(f"Invalid host range stride of 0 in '{pattern}'")
            vals = [chr(c) for c in range(ord(start), ord(end) + 1, stride)]

        segments.append(vals)
        last_end = m.end()

    segments.append([pattern[last_end:]])
    return ["".join(combo) for combo in itertools.product(*segments)]


def load_inventory_json(data: dict[str, Any], require_hosts: bool = True) -> Inventory:
    """Load inventory from Ansible JSON inventory format.

    Parses the JSON format produced by `ansible-inventory --list` and
    dynamic inventory scripts.

    Args:
        data: Parsed JSON inventory data
        require_hosts: If True (default), raise ValueError when no hosts are
            loaded. Set to False for provisioning workflows.

    Returns:
        Inventory object with typed groups and hosts

    Raises:
        ValueError: If require_hosts is True and no hosts are loaded

    Example:
        >>> data = {
        ...     "webservers": {"hosts": ["web01"]},
        ...     "_meta": {"hostvars": {"web01": {"ansible_host": "10.0.0.1"}}}
        ... }
        >>> inventory = load_inventory_json(data)

    Note:
        Expected format (Ansible --list output):

            {
              "webservers": {"hosts": ["web01", "web02"]},
              "databases": {"hosts": ["db01"], "vars": {"db_port": 5432}},
              "_meta": {
                "hostvars": {
                  "web01": {"ansible_host": "10.0.0.1"},
                  "web02": {"ansible_host": "10.0.0.2"},
                  "db01": {"ansible_host": "10.0.0.3"}
                }
              }
            }
    """
    hostvars = data.get("_meta", {}).get("hostvars", {})
    inventory = Inventory()

    for group_name, group_data in data.items():
        if group_name == "_meta":
            continue

        if not isinstance(group_data, dict):
            continue

        group = HostGroup(name=group_name)

        # JSON format uses hosts as a list of names (not a dict like YAML)
        hosts_list = group_data.get("hosts", [])
        if isinstance(hosts_list, list):
            for host_name in hosts_list:
                host_data = hostvars.get(host_name, {})
                if not isinstance(host_data, dict):
                    host_data = {}
                group.add_host(_host_from_vars(host_name, host_data))

        # Group vars
        if "vars" in group_data and isinstance(group_data["vars"], dict):
            group.vars = group_data["vars"]

        # Children
        if "children" in group_data:
            if isinstance(group_data["children"], list):
                group.children = group_data["children"]
            elif isinstance(group_data["children"], dict):
                group.children = list(group_data["children"].keys())

        inventory.add_group(group)

    if require_hosts and not inventory.get_all_hosts():
        raise ValueError("No hosts loaded from inventory")

    return inventory


def load_inventory_script(
    script_path: str | Path, require_hosts: bool = True
) -> Inventory:
    """Run an inventory script and load its JSON output.

    Executes the script with --list and parses the resulting JSON into
    an Inventory object.

    Args:
        script_path: Path to an executable inventory script
        require_hosts: If True (default), raise ValueError when no hosts are
            loaded.

    Returns:
        Inventory object with typed groups and hosts

    Raises:
        ValueError: If require_hosts is True and no hosts are loaded
        subprocess.CalledProcessError: If the script exits with a non-zero status

    Example:
        >>> inventory = load_inventory_script("./ec2_inventory.py")
    """
    path = Path(script_path)
    result = subprocess.run(
        [str(path), "--list"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)

    if "_meta" not in data:
        all_hosts: set[str] = set()
        for group_data in data.values():
            if isinstance(group_data, dict) and isinstance(group_data.get("hosts"), list):
                all_hosts.update(group_data["hosts"])

        hostvars: dict[str, Any] = {}
        for hostname in all_hosts:
            host_result = subprocess.run(
                [str(path), "--host", hostname],
                capture_output=True,
                text=True,
                check=True,
            )
            hostvars[hostname] = json.loads(host_result.stdout)

        data["_meta"] = {"hostvars": hostvars}

    return load_inventory_json(data, require_hosts=require_hosts)


def load_localhost(interpreter: str | None = None) -> Inventory:
    """Generate a localhost-only inventory for local execution.

    Creates an inventory with a single localhost host configured for
    local (non-SSH) execution.

    Args:
        interpreter: Python interpreter path (default: sys.executable)

    Returns:
        Inventory with localhost configured for local execution

    Example:
        >>> inventory = load_localhost()
        >>> hosts = inventory.get_all_hosts()
        >>> localhost = hosts["localhost"]
        >>> localhost.is_local
        True
    """
    if interpreter is None:
        interpreter = sys.executable

    localhost_host = HostConfig(
        name="localhost",
        ansible_host="127.0.0.1",
        ansible_connection="local",
        ansible_python_interpreter=interpreter,
    )

    all_group = HostGroup(name="all")
    all_group.add_host(localhost_host)

    inventory = Inventory()
    inventory.add_group(all_group)

    return inventory


def unique_hosts(inventory: Inventory) -> dict[str, HostConfig]:
    """Get all unique hosts from an inventory.

    Returns a dictionary mapping host names to HostConfig objects,
    ensuring each host appears only once even if it's in multiple groups.

    Args:
        inventory: Inventory object to extract hosts from

    Returns:
        Dictionary mapping host names to HostConfig objects

    Example:
        >>> inventory = load_inventory("hosts.yml")
        >>> hosts = unique_hosts(inventory)
        >>> for name, host in hosts.items():
        ...     print(f"{name}: {host.ansible_host}")
    """
    return inventory.get_all_hosts()
