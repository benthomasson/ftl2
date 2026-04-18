"""Tests for issue #22: Inventory fragility fixes.

Tests three fixes:
1. Cache invalidation gap — get_all_hosts() computes fresh every call
2. Silent fallback on missing files — raises FileNotFoundError by default
3. ignore_missing parameter — opt-in fallback to localhost
"""

import tempfile
from pathlib import Path

import pytest

from ftl2 import AutomationContext
from ftl2.inventory import (
    HostGroup,
    Inventory,
    load_inventory,
)
from ftl2.types import HostConfig


class TestCacheInvalidationFix:
    """Fix 1: Hosts added to groups after construction are immediately visible."""

    def test_host_added_to_group_visible_in_get_all_hosts(self):
        """Adding a host to a group after inventory construction should be
        immediately visible via get_all_hosts() — the original bug."""
        inv = Inventory()
        group = HostGroup(name="web")
        inv.add_group(group)

        # get_all_hosts before adding any host
        assert inv.get_all_hosts() == {}

        # Mutate the group directly (the problematic pattern)
        group.add_host(HostConfig(name="web01", ansible_host="10.0.0.1"))

        # Should be immediately visible — no stale cache
        all_hosts = inv.get_all_hosts()
        assert "web01" in all_hosts
        assert all_hosts["web01"].ansible_host == "10.0.0.1"

    def test_multiple_groups_all_hosts_fresh(self):
        """get_all_hosts() returns hosts from all groups, computed fresh."""
        inv = Inventory()
        g1 = HostGroup(name="web")
        g2 = HostGroup(name="db")
        g1.add_host(HostConfig(name="web01", ansible_host="10.0.0.1"))
        inv.add_group(g1)
        inv.add_group(g2)

        assert len(inv.get_all_hosts()) == 1

        # Add host to second group after construction
        g2.add_host(HostConfig(name="db01", ansible_host="10.0.0.2"))
        assert len(inv.get_all_hosts()) == 2
        assert "db01" in inv.get_all_hosts()

    def test_host_in_multiple_groups_deduplicated(self):
        """Same host in two groups should appear once in get_all_hosts()."""
        inv = Inventory()
        host = HostConfig(name="shared", ansible_host="10.0.0.1")
        g1 = HostGroup(name="web")
        g2 = HostGroup(name="app")
        g1.add_host(host)
        g2.add_host(host)
        inv.add_group(g1)
        inv.add_group(g2)

        assert len(inv.get_all_hosts()) == 1


class TestMissingFileRaises:
    """Fix 2: load_inventory() raises on missing files instead of silent fallback."""

    def test_load_inventory_missing_file_raises(self):
        """load_inventory() must raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError, match="Inventory file not found"):
            load_inventory("/nonexistent/inventory.yml")

    def test_load_inventory_missing_file_message_includes_path(self):
        """Error message should include the missing path for debugging."""
        path = "/tmp/does_not_exist_12345.yml"
        with pytest.raises(FileNotFoundError, match=path):
            load_inventory(path)

    def test_load_inventory_existing_file_works(self):
        """Existing inventory files should still load normally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text(
                "web:\n  hosts:\n    web01:\n      ansible_host: 10.0.0.1\n"
            )
            inv = load_inventory(str(inv_file))
            assert "web01" in inv.get_all_hosts()


class TestContextMissingInventory:
    """Fix 3: AutomationContext raises on missing inventory, with opt-in fallback."""

    def test_context_missing_inventory_raises(self):
        """AutomationContext should raise FileNotFoundError for missing inventory."""
        with pytest.raises(FileNotFoundError):
            AutomationContext(inventory="/nonexistent/path.yml")

    def test_context_ignore_missing_falls_back_to_localhost(self):
        """ignore_missing_inventory=True should fall back to localhost."""
        ctx = AutomationContext(
            inventory="/nonexistent/path.yml",
            ignore_missing_inventory=True,
        )
        assert "localhost" in ctx.hosts

    def test_context_none_inventory_still_gives_localhost(self):
        """inventory=None should still default to localhost (unchanged behavior)."""
        ctx = AutomationContext(inventory=None)
        assert "localhost" in ctx.hosts

    def test_context_existing_inventory_file_works(self):
        """Existing inventory file should load normally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text(
                "db:\n  hosts:\n    db01:\n      ansible_host: 10.0.0.2\n"
            )
            ctx = AutomationContext(inventory=str(inv_file))
            assert "db01" in ctx.hosts
