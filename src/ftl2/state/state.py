"""State class for tracking hosts and resources.

Provides operations for managing state: has, get, add, remove, update.
State is persisted to a JSON file for crash recovery and idempotent operations.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ftl2.state.file import read_state_file, write_state_file


class State:
    """Manages persistent state for FTL2 automation.

    Tracks dynamically added hosts and provisioned resources.
    State is persisted to a JSON file immediately on mutation.

    Attributes:
        path: Path to the state file
        data: The state data dictionary

    Example:
        state = State(".ftl2-state.json")

        # Check existence
        if not state.has("minecraft-9"):
            # Add resource
            state.add("minecraft-9", {"provider": "linode", "ipv4": ["1.2.3.4"]})

        # Get data
        resource = state.get("minecraft-9")
    """

    def __init__(self, path: str | Path):
        """Initialize state from a file.

        Args:
            path: Path to the state file. Created if doesn't exist.
        """
        self.path = Path(path)
        self.data = read_state_file(self.path)

    def _save(self) -> None:
        """Save state to file."""
        self.data["updated_at"] = datetime.now(UTC).isoformat()
        write_state_file(self.path, self.data)

    def _now(self) -> str:
        """Get current timestamp as ISO string."""
        return datetime.now(UTC).isoformat()

    # Host operations

    def has_host(self, name: str) -> bool:
        """Check if a host exists in state.

        Args:
            name: Host name

        Returns:
            True if host exists
        """
        return name in self.data.get("hosts", {})

    def get_host(self, name: str) -> dict[str, Any] | None:
        """Get host data from state.

        Args:
            name: Host name

        Returns:
            Host data dict, or None if not found
        """
        return self.data.get("hosts", {}).get(name)

    def add_host(
        self,
        name: str,
        ansible_host: str | None = None,
        ansible_user: str | None = None,
        ansible_port: int = 22,
        groups: list[str] | None = None,
        **extra: Any,
    ) -> None:
        """Add a host to state.

        Args:
            name: Host name
            ansible_host: IP address or hostname to connect to
            ansible_user: SSH username
            ansible_port: SSH port (default 22)
            groups: List of group names
            **extra: Additional host variables
        """
        if "hosts" not in self.data:
            self.data["hosts"] = {}

        host_data: dict[str, Any] = {
            "ansible_host": ansible_host or name,
            "ansible_port": ansible_port,
            "groups": groups or [],
            "added_at": self._now(),
        }

        if ansible_user:
            host_data["ansible_user"] = ansible_user

        # Add any extra variables
        host_data.update(extra)

        self.data["hosts"][name] = host_data
        self._save()

    def remove_host(self, name: str) -> bool:
        """Remove a host from state.

        Args:
            name: Host name

        Returns:
            True if host was removed, False if not found
        """
        if name in self.data.get("hosts", {}):
            del self.data["hosts"][name]
            self._save()
            return True
        return False

    def hosts(self) -> list[str]:
        """Get list of host names in state.

        Returns:
            List of host names
        """
        return list(self.data.get("hosts", {}).keys())

    # Resource operations

    def has_resource(self, name: str) -> bool:
        """Check if a resource exists in state.

        Args:
            name: Resource name

        Returns:
            True if resource exists
        """
        return name in self.data.get("resources", {})

    def get_resource(self, name: str) -> dict[str, Any] | None:
        """Get resource data from state.

        Args:
            name: Resource name

        Returns:
            Resource data dict, or None if not found
        """
        return self.data.get("resources", {}).get(name)

    def add_resource(self, name: str, data: dict[str, Any]) -> None:
        """Add a resource to state.

        Args:
            name: Resource name (usually matches host name)
            data: Resource data (provider, id, ipv4, etc.)
        """
        if "resources" not in self.data:
            self.data["resources"] = {}

        resource_data = {
            "created_at": self._now(),
            **data,
        }

        self.data["resources"][name] = resource_data
        self._save()

    def update_resource(self, name: str, data: dict[str, Any]) -> bool:
        """Update an existing resource in state.

        Args:
            name: Resource name
            data: Data to merge into existing resource

        Returns:
            True if resource was updated, False if not found
        """
        if name not in self.data.get("resources", {}):
            return False

        self.data["resources"][name].update(data)
        self.data["resources"][name]["last_seen"] = self._now()
        self._save()
        return True

    def remove_resource(self, name: str) -> bool:
        """Remove a resource from state.

        Args:
            name: Resource name

        Returns:
            True if resource was removed, False if not found
        """
        if name in self.data.get("resources", {}):
            del self.data["resources"][name]
            self._save()
            return True
        return False

    def resources(self, provider: str | None = None) -> dict[str, dict[str, Any]]:
        """Get all resources, optionally filtered by provider.

        Args:
            provider: Optional provider name to filter by

        Returns:
            Dict of resource name -> resource data
        """
        all_resources = self.data.get("resources", {})
        if provider is None:
            return dict(all_resources)
        return {
            name: data
            for name, data in all_resources.items()
            if data.get("provider") == provider
        }

    # Convenience methods (unified interface)

    def has(self, name: str) -> bool:
        """Check if a host or resource exists.

        Args:
            name: Name to check

        Returns:
            True if exists as either host or resource
        """
        return self.has_host(name) or self.has_resource(name)

    def get(self, name: str) -> dict[str, Any] | None:
        """Get host or resource data.

        Checks resources first, then hosts.

        Args:
            name: Name to look up

        Returns:
            Data dict, or None if not found
        """
        return self.get_resource(name) or self.get_host(name)

    def add(self, name: str, data: dict[str, Any]) -> None:
        """Add a resource to state.

        This is an alias for add_resource(). For adding hosts,
        use add_host() which has a more specific signature.

        Args:
            name: Resource name
            data: Resource data
        """
        self.add_resource(name, data)

    def remove(self, name: str) -> bool:
        """Remove a host or resource from state.

        Removes from both hosts and resources if present in both.

        Args:
            name: Name to remove

        Returns:
            True if anything was removed
        """
        removed_host = self.remove_host(name)
        removed_resource = self.remove_resource(name)
        return removed_host or removed_resource

    def __repr__(self) -> str:
        host_count = len(self.data.get("hosts", {}))
        resource_count = len(self.data.get("resources", {}))
        return f"State({self.path}, hosts={host_count}, resources={resource_count})"
