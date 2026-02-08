"""State management for FTL2.

Provides persistent state tracking for dynamically provisioned infrastructure.
State file keeps inventory.yml clean (static, version-controlled) while tracking
runtime infrastructure (dynamic, gitignored).

Example:
    async with automation(state_file=".ftl2-state.json") as ftl:
        # add_host persists to state file
        ftl.add_host("minecraft-9", ansible_host="69.164.211.253")

        # State file now contains the host
        # Re-runs will see it in ftl.hosts
"""

from ftl2.state.state import State
from ftl2.state.file import read_state_file, write_state_file
from ftl2.state.merge import merge_state_into_inventory
from ftl2.state.execution import (
    ExecutionState,
    HostState,
    load_state,
    save_state,
    create_state_from_results,
    filter_hosts_for_resume,
    format_state_json,
)

__all__ = [
    "State",
    "read_state_file",
    "write_state_file",
    "merge_state_into_inventory",
    "ExecutionState",
    "HostState",
    "load_state",
    "save_state",
    "create_state_from_results",
    "filter_hosts_for_resume",
    "format_state_json",
]
