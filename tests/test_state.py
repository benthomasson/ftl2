"""Tests for the FTL2 state file feature."""

import json
import tempfile
from pathlib import Path

import pytest

from ftl2 import automation
from ftl2.state import State, read_state_file, write_state_file
from ftl2.state.merge import merge_state_into_inventory
from ftl2.inventory import Inventory, HostGroup


class TestStateFile:
    """Tests for state file read/write operations."""

    def test_read_nonexistent_file_returns_empty_state(self):
        """Test that reading a nonexistent file returns empty state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.json"
            state = read_state_file(path)

            assert state["version"] == 1
            assert "created_at" in state
            assert "updated_at" in state
            assert state["hosts"] == {}
            assert state["resources"] == {}

    def test_write_and_read_state(self):
        """Test writing and reading state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"

            state = {
                "version": 1,
                "hosts": {"web01": {"ansible_host": "1.2.3.4"}},
                "resources": {},
            }

            write_state_file(path, state)
            assert path.exists()

            loaded = read_state_file(path)
            assert loaded["hosts"]["web01"]["ansible_host"] == "1.2.3.4"

    def test_atomic_write(self):
        """Test that write is atomic (no partial writes)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"

            # Write initial state
            state = {"version": 1, "hosts": {}, "resources": {}}
            write_state_file(path, state)

            # Write new state
            state["hosts"]["web01"] = {"ansible_host": "1.2.3.4"}
            write_state_file(path, state)

            # Verify no temp files left behind
            files = list(Path(tmpdir).iterdir())
            assert len(files) == 1
            assert files[0].name == "state.json"


class TestStateClass:
    """Tests for the State class operations."""

    def test_has_host(self):
        """Test checking if host exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            assert not state.has_host("web01")

            state.add_host("web01", ansible_host="1.2.3.4")
            assert state.has_host("web01")

    def test_add_and_get_host(self):
        """Test adding and retrieving host."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_host(
                "web01",
                ansible_host="1.2.3.4",
                ansible_user="admin",
                ansible_port=2222,
                groups=["webservers", "production"],
            )

            host = state.get_host("web01")
            assert host is not None
            assert host["ansible_host"] == "1.2.3.4"
            assert host["ansible_user"] == "admin"
            assert host["ansible_port"] == 2222
            assert host["groups"] == ["webservers", "production"]
            assert "added_at" in host

    def test_remove_host(self):
        """Test removing host."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_host("web01", ansible_host="1.2.3.4")
            assert state.has_host("web01")

            result = state.remove_host("web01")
            assert result is True
            assert not state.has_host("web01")

            # Removing non-existent host returns False
            result = state.remove_host("web01")
            assert result is False

    def test_hosts_list(self):
        """Test listing all hosts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_host("web01", ansible_host="1.2.3.4")
            state.add_host("web02", ansible_host="1.2.3.5")
            state.add_host("db01", ansible_host="1.2.3.6")

            hosts = state.hosts()
            assert len(hosts) == 3
            assert "web01" in hosts
            assert "web02" in hosts
            assert "db01" in hosts

    def test_add_and_get_resource(self):
        """Test adding and retrieving resource."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_resource("minecraft-9", {
                "provider": "linode",
                "id": 12345,
                "ipv4": ["69.164.211.253"],
            })

            resource = state.get_resource("minecraft-9")
            assert resource is not None
            assert resource["provider"] == "linode"
            assert resource["id"] == 12345
            assert resource["ipv4"] == ["69.164.211.253"]
            assert "created_at" in resource

    def test_resources_filter_by_provider(self):
        """Test filtering resources by provider."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_resource("linode-1", {"provider": "linode", "id": 1})
            state.add_resource("linode-2", {"provider": "linode", "id": 2})
            state.add_resource("aws-1", {"provider": "aws", "id": "i-123"})

            all_resources = state.resources()
            assert len(all_resources) == 3

            linode_resources = state.resources(provider="linode")
            assert len(linode_resources) == 2
            assert "linode-1" in linode_resources
            assert "linode-2" in linode_resources

            aws_resources = state.resources(provider="aws")
            assert len(aws_resources) == 1
            assert "aws-1" in aws_resources

    def test_convenience_has_checks_both(self):
        """Test that has() checks both hosts and resources."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)

            state.add_host("web01", ansible_host="1.2.3.4")
            state.add_resource("db01", {"provider": "aws"})

            assert state.has("web01")  # Host
            assert state.has("db01")   # Resource
            assert not state.has("unknown")

    def test_persistence_across_instances(self):
        """Test that state persists across State instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"

            # First instance
            state1 = State(path)
            state1.add_host("web01", ansible_host="1.2.3.4")
            state1.add_resource("server-1", {"provider": "linode"})

            # Second instance loads the same file
            state2 = State(path)
            assert state2.has_host("web01")
            assert state2.has_resource("server-1")
            assert state2.get_host("web01")["ansible_host"] == "1.2.3.4"


class TestMergeStateIntoInventory:
    """Tests for merging state hosts into inventory."""

    def test_merge_adds_hosts_to_inventory(self):
        """Test that state hosts are added to inventory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)
            state.add_host("web01", ansible_host="1.2.3.4", groups=["webservers"])

            inventory = Inventory()
            merge_state_into_inventory(state, inventory)

            # Host should be in inventory
            all_hosts = inventory.get_all_hosts()
            assert "web01" in all_hosts
            assert all_hosts["web01"].ansible_host == "1.2.3.4"

            # Group should be created
            group = inventory.get_group("webservers")
            assert group is not None
            hosts = group.list_hosts()
            assert len(hosts) == 1
            assert hosts[0].name == "web01"

    def test_merge_creates_multiple_groups(self):
        """Test that host is added to all specified groups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "state.json"
            state = State(path)
            state.add_host("web01", ansible_host="1.2.3.4", groups=["webservers", "production"])

            inventory = Inventory()
            merge_state_into_inventory(state, inventory)

            # Both groups should exist with the host
            webservers = inventory.get_group("webservers")
            production = inventory.get_group("production")

            assert webservers is not None
            assert production is not None
            assert any(h.name == "web01" for h in webservers.list_hosts())
            assert any(h.name == "web01" for h in production.list_hosts())


class TestAutomationStateIntegration:
    """Integration tests for state with automation context."""

    @pytest.mark.asyncio
    async def test_add_host_persists_to_state(self):
        """Test that add_host writes to state file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            async with automation(
                state_file=state_path,
                print_summary=False,
                quiet=True,
            ) as ftl:
                ftl.add_host(
                    "minecraft-9",
                    ansible_host="69.164.211.253",
                    ansible_user="root",
                    groups=["minecraft"],
                )

            # Verify state file was written
            assert state_path.exists()
            state_data = json.loads(state_path.read_text())
            assert "minecraft-9" in state_data["hosts"]
            assert state_data["hosts"]["minecraft-9"]["ansible_host"] == "69.164.211.253"

    @pytest.mark.asyncio
    async def test_state_hosts_loaded_on_enter(self):
        """Test that state hosts are available in ftl.hosts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            # Pre-populate state file
            state_data = {
                "version": 1,
                "created_at": "2026-02-07T00:00:00Z",
                "updated_at": "2026-02-07T00:00:00Z",
                "hosts": {
                    "minecraft-9": {
                        "ansible_host": "69.164.211.253",
                        "ansible_user": "root",
                        "ansible_port": 22,
                        "groups": ["minecraft"],
                        "added_at": "2026-02-07T00:00:00Z",
                    }
                },
                "resources": {},
            }
            state_path.write_text(json.dumps(state_data))

            async with automation(
                state_file=state_path,
                print_summary=False,
                quiet=True,
            ) as ftl:
                # Host should be visible in ftl.hosts
                assert "minecraft-9" in ftl.hosts.keys()
                hosts = ftl.hosts["minecraft-9"]
                assert len(hosts) == 1
                assert hosts[0].ansible_host == "69.164.211.253"

    @pytest.mark.asyncio
    async def test_state_property_accessible(self):
        """Test that ftl.state is accessible when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            async with automation(
                state_file=state_path,
                print_summary=False,
                quiet=True,
            ) as ftl:
                # state should be accessible
                assert ftl.state is not None
                assert not ftl.state.has("anything")

                # Can add resources
                ftl.state.add("server-1", {"provider": "linode", "id": 123})
                assert ftl.state.has("server-1")

    @pytest.mark.asyncio
    async def test_state_property_raises_when_disabled(self):
        """Test that ftl.state raises when state_file not provided."""
        async with automation(
            print_summary=False,
            quiet=True,
            state_file=None,
        ) as ftl:
            with pytest.raises(RuntimeError, match="State not available"):
                _ = ftl.state

    @pytest.mark.asyncio
    async def test_idempotent_provisioning_pattern(self):
        """Test the idempotent provisioning pattern with state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            provision_count = 0

            async def ensure_server(ftl, name: str):
                nonlocal provision_count
                if ftl.state.has(name):
                    return ftl.state.get(name)

                # Simulate provisioning
                provision_count += 1
                ftl.state.add(name, {"provider": "linode", "id": provision_count})
                ftl.add_host(name, ansible_host=f"192.168.1.{provision_count}")
                return ftl.state.get(name)

            # First run - provisions
            async with automation(state_file=state_path, quiet=True, print_summary=False) as ftl:
                await ensure_server(ftl, "server-1")
                await ensure_server(ftl, "server-1")  # Should not provision again
                await ensure_server(ftl, "server-2")

            assert provision_count == 2  # Only 2 unique servers

            # Second run - should not provision anything
            provision_count = 0
            async with automation(state_file=state_path, quiet=True, print_summary=False) as ftl:
                await ensure_server(ftl, "server-1")
                await ensure_server(ftl, "server-2")

            assert provision_count == 0  # No new provisions
