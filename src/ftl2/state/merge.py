"""Merge state hosts into inventory.

Combines static inventory (from inventory.yml) with dynamic hosts
(from state file) into a unified inventory view.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ftl2.inventory import Inventory
    from ftl2.state.state import State


def merge_state_into_inventory(state: "State", inventory: "Inventory") -> None:
    """Merge hosts from state into inventory.

    Adds all hosts from the state file into the inventory, creating
    groups as needed. If a host already exists in the inventory
    (same name), the state version takes precedence.

    Args:
        state: The State object with dynamic hosts
        inventory: The Inventory to merge into (modified in place)
    """
    from ftl2.inventory import HostGroup
    from ftl2.types import HostConfig

    for host_name in state.hosts():
        host_data = state.get_host(host_name)
        if host_data is None:
            continue

        # Create HostConfig from state data
        host = HostConfig(
            name=host_name,
            ansible_host=host_data.get("ansible_host", host_name),
            ansible_port=host_data.get("ansible_port", 22),
            ansible_user=host_data.get("ansible_user", ""),
            ansible_connection=host_data.get("ansible_connection", "ssh"),
            vars={
                k: v
                for k, v in host_data.items()
                if k not in ("ansible_host", "ansible_port", "ansible_user",
                            "ansible_connection", "groups", "added_at")
            },
        )

        # Add to specified groups
        groups = host_data.get("groups", [])
        if not groups:
            groups = ["ungrouped"]

        for group_name in groups:
            group = inventory.get_group(group_name)
            if group is None:
                group = HostGroup(name=group_name)
                inventory.add_group(group)

            # Check if host already in group (avoid duplicates)
            existing_hosts = group.list_hosts()
            existing_names = {h.name for h in existing_hosts}
            if host_name not in existing_names:
                group.add_host(host)
