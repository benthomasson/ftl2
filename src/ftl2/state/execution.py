"""State tracking and resume functionality for FTL2.

Provides functionality to save execution state to a file and resume
from a previous run, skipping hosts that already succeeded.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HostState:
    """State for a single host from a previous run.

    Attributes:
        host_name: Name of the host
        success: Whether execution succeeded
        changed: Whether changes were made
        timestamp: When this host was executed
        error: Error message if failed
        attempts: Number of attempts made
    """

    host_name: str
    success: bool
    changed: bool = False
    timestamp: str = ""
    error: str = ""
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "host_name": self.host_name,
            "success": self.success,
            "changed": self.changed,
            "timestamp": self.timestamp,
            "attempts": self.attempts,
        }
        if self.error:
            result["error"] = self.error
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HostState":
        """Create from dictionary."""
        return cls(
            host_name=data["host_name"],
            success=data["success"],
            changed=data.get("changed", False),
            timestamp=data.get("timestamp", ""),
            error=data.get("error", ""),
            attempts=data.get("attempts", 1),
        )


@dataclass
class ExecutionState:
    """State from a previous execution run.

    Attributes:
        module: Module that was executed
        args: Arguments passed to the module
        inventory_file: Path to inventory file used
        timestamp: When the run started
        completed: Whether the run completed (vs interrupted)
        hosts: Per-host state information
        total_hosts: Total number of hosts
        successful: Number of successful hosts
        failed: Number of failed hosts
    """

    module: str
    args: dict[str, Any] = field(default_factory=dict)
    inventory_file: str = ""
    timestamp: str = ""
    completed: bool = False
    hosts: dict[str, HostState] = field(default_factory=dict)
    total_hosts: int = 0
    successful: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "module": self.module,
            "args": self.args,
            "inventory_file": self.inventory_file,
            "timestamp": self.timestamp,
            "completed": self.completed,
            "total_hosts": self.total_hosts,
            "successful": self.successful,
            "failed": self.failed,
            "hosts": {name: state.to_dict() for name, state in self.hosts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionState":
        """Create from dictionary."""
        hosts = {
            name: HostState.from_dict(host_data)
            for name, host_data in data.get("hosts", {}).items()
        }
        return cls(
            module=data["module"],
            args=data.get("args", {}),
            inventory_file=data.get("inventory_file", ""),
            timestamp=data.get("timestamp", ""),
            completed=data.get("completed", False),
            total_hosts=data.get("total_hosts", 0),
            successful=data.get("successful", 0),
            failed=data.get("failed", 0),
            hosts=hosts,
        )

    def get_succeeded_hosts(self) -> set[str]:
        """Get names of hosts that succeeded."""
        return {name for name, state in self.hosts.items() if state.success}

    def get_failed_hosts(self) -> set[str]:
        """Get names of hosts that failed."""
        return {name for name, state in self.hosts.items() if not state.success}

    def get_pending_hosts(self, all_hosts: set[str]) -> set[str]:
        """Get names of hosts that haven't been attempted yet.

        Args:
            all_hosts: Set of all host names in current inventory

        Returns:
            Set of host names not in previous state
        """
        return all_hosts - set(self.hosts.keys())

    def format_resume_summary(self, all_hosts: set[str]) -> str:
        """Format a summary for resume mode.

        Args:
            all_hosts: Set of all host names in current inventory

        Returns:
            Formatted summary string
        """
        succeeded = self.get_succeeded_hosts()
        failed = self.get_failed_hosts()
        pending = self.get_pending_hosts(all_hosts)

        lines = [
            "",
            f"Resuming from previous run ({self.timestamp}):",
            f"  Module: {self.module}",
            "",
        ]

        if succeeded:
            lines.append(f"  Skipping {len(succeeded)} succeeded host(s):")
            for name in sorted(succeeded):
                lines.append(f"    ✓ {name}")

        if failed:
            lines.append(f"  Retrying {len(failed)} failed host(s):")
            for name in sorted(failed):
                state = self.hosts[name]
                lines.append(f"    ⟳ {name}: {state.error or 'unknown error'}")

        if pending:
            lines.append(f"  Starting {len(pending)} new host(s):")
            for name in sorted(pending):
                lines.append(f"    • {name}")

        lines.append("")
        return "\n".join(lines)


def save_state(
    state: ExecutionState,
    state_file: Path | str,
) -> None:
    """Save execution state to a file.

    Args:
        state: Execution state to save
        state_file: Path to save state to
    """
    path = Path(state_file) if isinstance(state_file, str) else state_file

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as f:
        json.dump(state.to_dict(), f, indent=2)

    logger.info(f"State saved to {path}")


def load_state(state_file: Path | str) -> ExecutionState | None:
    """Load execution state from a file.

    Args:
        state_file: Path to load state from

    Returns:
        ExecutionState if file exists, None otherwise
    """
    path = Path(state_file) if isinstance(state_file, str) else state_file

    if not path.exists():
        logger.debug(f"State file not found: {path}")
        return None

    try:
        with path.open() as f:
            data = json.load(f)
        state = ExecutionState.from_dict(data)
        logger.info(f"Loaded state from {path}: {state.successful}/{state.total_hosts} succeeded")
        return state
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load state file {path}: {e}")
        return None


def create_state_from_results(
    results: Any,  # ExecutionResults
    module: str,
    args: dict[str, Any],
    inventory_file: str,
) -> ExecutionState:
    """Create execution state from results.

    Args:
        results: ExecutionResults from a run
        module: Module that was executed
        args: Arguments passed to the module
        inventory_file: Path to inventory file

    Returns:
        ExecutionState with per-host results
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    hosts: dict[str, HostState] = {}
    for host_name, result in results.results.items():
        hosts[host_name] = HostState(
            host_name=host_name,
            success=result.success,
            changed=result.changed,
            timestamp=timestamp,
            error=result.error or "",
            attempts=1,  # Could be enhanced to track retry attempts
        )

    return ExecutionState(
        module=module,
        args=args,
        inventory_file=inventory_file,
        timestamp=timestamp,
        completed=True,
        hosts=hosts,
        total_hosts=results.total_hosts,
        successful=results.successful,
        failed=results.failed,
    )


def filter_hosts_for_resume(
    all_host_names: set[str],
    previous_state: ExecutionState,
) -> tuple[set[str], set[str], set[str]]:
    """Determine which hosts to run based on previous state.

    Args:
        all_host_names: All host names in current inventory
        previous_state: State from previous run

    Returns:
        Tuple of (hosts_to_run, skipped_hosts, new_hosts)
    """
    succeeded = previous_state.get_succeeded_hosts()
    failed = previous_state.get_failed_hosts()
    pending = previous_state.get_pending_hosts(all_host_names)

    # Skip succeeded hosts, run failed and pending
    skipped = succeeded & all_host_names  # Only skip if still in inventory
    to_run = (failed | pending) & all_host_names

    return to_run, skipped, pending


def format_state_json(state: ExecutionState) -> str:
    """Format execution state as JSON string.

    Args:
        state: Execution state to format

    Returns:
        JSON string
    """
    return json.dumps(state.to_dict(), indent=2)
