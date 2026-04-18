"""Tests for ModuleProxy.__getattr__ inventory lookup error logging (Issue #32).

Validates that inventory lookup failures in ModuleProxy.__getattr__ are logged
at DEBUG level instead of being silently swallowed, while preserving the
fallthrough to module/namespace lookup.
"""

import logging
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from ftl2.automation.proxy import HostScopedProxy, ModuleProxy, NamespaceProxy


class _FakeContext:
    """Minimal context stub that lets us control hosts behavior.

    Using a real class instead of MagicMock because MagicMock's __getattr__
    intercepts AttributeError from PropertyMock and auto-creates attributes.
    """

    def __init__(self, hosts_side_effect=None, hosts_return=None):
        self._enabled_modules = None
        self._hosts_side_effect = hosts_side_effect
        self._hosts_return = hosts_return

    @property
    def hosts(self):
        if self._hosts_side_effect is not None:
            raise self._hosts_side_effect
        return self._hosts_return


def _make_proxy(hosts_side_effect=None, hosts_return=None):
    """Create a ModuleProxy with a fake context.

    Args:
        hosts_side_effect: Exception to raise when accessing context.hosts
        hosts_return: Mock HostsProxy to return from context.hosts

    Returns:
        (ModuleProxy, fake_context)
    """
    if hosts_side_effect is None and hosts_return is None:
        hosts_side_effect = AttributeError("no inventory")

    ctx = _FakeContext(hosts_side_effect=hosts_side_effect, hosts_return=hosts_return)
    return ModuleProxy(ctx), ctx


def _make_hosts_proxy(groups=None, keys=None):
    """Create a mock HostsProxy with given groups and keys."""
    hp = MagicMock()
    hp.groups = groups or []
    hp.keys.return_value = keys or []
    return hp


class TestInventoryLookupLogging:
    """Tests that inventory lookup failures are logged at DEBUG level."""

    def test_attribute_error_logged_at_debug(self, caplog):
        """AttributeError from missing inventory is logged at DEBUG."""
        proxy, _ = _make_proxy(hosts_side_effect=AttributeError("_inventory is None"))

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("webserver1")

        # Should fall through to module/namespace check
        assert isinstance(result, NamespaceProxy)
        # Should have logged the failure
        assert any("Inventory lookup failed" in r.message for r in caplog.records)
        assert any(r.levelno == logging.DEBUG for r in caplog.records
                   if "Inventory lookup failed" in r.message)

    def test_runtime_error_logged_at_debug(self, caplog):
        """RuntimeError from broken inventory plugin is logged at DEBUG."""
        proxy, _ = _make_proxy(hosts_side_effect=RuntimeError("inventory plugin crashed"))

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("db_server")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message and "db_server" in r.message
                    for r in caplog.records)

    def test_file_not_found_error_logged_at_debug(self, caplog):
        """FileNotFoundError from missing inventory file is logged at DEBUG."""
        proxy, _ = _make_proxy(
            hosts_side_effect=FileNotFoundError("/etc/ansible/hosts not found")
        )

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("loadbalancer")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message for r in caplog.records)

    def test_permission_error_logged_at_debug(self, caplog):
        """PermissionError from unreadable inventory is logged at DEBUG."""
        proxy, _ = _make_proxy(
            hosts_side_effect=PermissionError("Permission denied: /etc/ansible/hosts")
        )

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("app01")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message for r in caplog.records)

    def test_io_error_logged_at_debug(self, caplog):
        """IOError from inventory access failure is logged at DEBUG."""
        proxy, _ = _make_proxy(hosts_side_effect=OSError("disk read error"))

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("cache01")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message for r in caplog.records)

    def test_log_includes_attribute_name(self, caplog):
        """Log message includes the attribute name that failed lookup."""
        proxy, _ = _make_proxy(hosts_side_effect=ValueError("bad inventory data"))

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            proxy.__getattr__("my_special_host")

        debug_msgs = [r.message for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(debug_msgs) >= 1
        assert "my_special_host" in debug_msgs[0]

    def test_log_includes_exc_info(self, caplog):
        """Log record includes exception info for traceback diagnostics."""
        proxy, _ = _make_proxy(hosts_side_effect=RuntimeError("broken"))

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            proxy.__getattr__("somehost")

        debug_records = [r for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(debug_records) >= 1
        # exc_info should be set (not None/False)
        assert debug_records[0].exc_info is not None
        assert debug_records[0].exc_info[0] is RuntimeError


class TestInventoryLookupSilentAtDefaultLevel:
    """Tests that inventory failures produce no output at default log levels."""

    def test_no_output_at_warning_level(self, caplog):
        """No log output at WARNING level when inventory lookup fails."""
        proxy, _ = _make_proxy(hosts_side_effect=RuntimeError("broken"))

        with caplog.at_level(logging.WARNING, logger="ftl2.automation.proxy"):
            proxy.__getattr__("somehost")

        inventory_msgs = [r for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(inventory_msgs) == 0

    def test_no_output_at_info_level(self, caplog):
        """No log output at INFO level when inventory lookup fails."""
        proxy, _ = _make_proxy(hosts_side_effect=AttributeError("no inventory"))

        with caplog.at_level(logging.INFO, logger="ftl2.automation.proxy"):
            proxy.__getattr__("webhost")

        inventory_msgs = [r for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(inventory_msgs) == 0


class TestFallthroughBehaviorPreserved:
    """Tests that fallthrough to module/namespace lookup still works after inventory failure."""

    def test_falls_through_to_known_module(self):
        """After inventory failure, known modules still resolve correctly."""
        proxy, ctx = _make_proxy(hosts_side_effect=AttributeError("no inventory"))

        with patch("ftl2.ftl_modules.get_module") as mock_get_module:
            mock_get_module.return_value = MagicMock()  # simulate known module
            result = proxy.__getattr__("file")

        # Should return a callable wrapper, not a NamespaceProxy
        assert callable(result)

    def test_falls_through_to_namespace_proxy(self):
        """After inventory failure, unknown names return NamespaceProxy for FQCN."""
        proxy, _ = _make_proxy(hosts_side_effect=RuntimeError("broken"))

        result = proxy.__getattr__("amazon")

        assert isinstance(result, NamespaceProxy)

    def test_private_attrs_still_raise(self):
        """Private attributes still raise AttributeError (before inventory check)."""
        proxy, _ = _make_proxy()

        with pytest.raises(AttributeError):
            proxy.__getattr__("_private")

    def test_localhost_still_works(self):
        """'localhost' shortcut still works (before inventory check)."""
        proxy, _ = _make_proxy()

        result = proxy.__getattr__("localhost")

        assert isinstance(result, HostScopedProxy)

    def test_local_still_works(self):
        """'local' shortcut still works (before inventory check)."""
        proxy, _ = _make_proxy()

        result = proxy.__getattr__("local")

        assert isinstance(result, HostScopedProxy)


class TestSuccessfulInventoryLookup:
    """Tests that successful inventory lookups still work (no regression)."""

    def test_host_match_returns_host_scoped_proxy(self):
        """Known host name returns HostScopedProxy."""
        hp = _make_hosts_proxy(keys=["web01", "web02", "db01"])
        proxy, _ = _make_proxy(hosts_return=hp)

        result = proxy.__getattr__("web01")

        assert isinstance(result, HostScopedProxy)

    def test_group_match_returns_host_scoped_proxy(self):
        """Known group name returns HostScopedProxy."""
        hp = _make_hosts_proxy(groups=["webservers", "databases"])
        proxy, _ = _make_proxy(hosts_return=hp)

        result = proxy.__getattr__("webservers")

        assert isinstance(result, HostScopedProxy)

    def test_underscore_dash_normalization(self):
        """Underscore-to-dash normalization matches hosts with dashes."""
        hp = _make_hosts_proxy(keys=["minecraft-9"])
        proxy, _ = _make_proxy(hosts_return=hp)

        result = proxy.__getattr__("minecraft_9")

        assert isinstance(result, HostScopedProxy)

    def test_no_log_on_successful_lookup(self, caplog):
        """No 'Inventory lookup failed' log when lookup succeeds."""
        hp = _make_hosts_proxy(keys=["web01"])
        proxy, _ = _make_proxy(hosts_return=hp)

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            proxy.__getattr__("web01")

        inventory_msgs = [r for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(inventory_msgs) == 0

    def test_no_log_when_host_not_in_inventory(self, caplog):
        """No error log when host simply isn't found (inventory works fine)."""
        hp = _make_hosts_proxy(keys=["web01"], groups=["webservers"])
        proxy, _ = _make_proxy(hosts_return=hp)

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            proxy.__getattr__("unknown_name")

        # Should fall through to module check without logging an error
        inventory_msgs = [r for r in caplog.records if "Inventory lookup failed" in r.message]
        assert len(inventory_msgs) == 0


class TestGroupsPropertyFailure:
    """Tests for failures during groups/keys access (not hosts property itself)."""

    def test_groups_access_error_logged(self, caplog):
        """Error during groups access is caught and logged."""
        hp = MagicMock()
        type(hp).groups = PropertyMock(side_effect=TypeError("groups broke"))
        proxy, _ = _make_proxy(hosts_return=hp)

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("webservers")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message for r in caplog.records)

    def test_keys_access_error_logged(self, caplog):
        """Error during keys() call is caught and logged."""
        hp = MagicMock()
        hp.groups = []  # groups works fine
        hp.keys.side_effect = OSError("inventory file locked")
        proxy, _ = _make_proxy(hosts_return=hp)

        with caplog.at_level(logging.DEBUG, logger="ftl2.automation.proxy"):
            result = proxy.__getattr__("web01")

        assert isinstance(result, NamespaceProxy)
        assert any("Inventory lookup failed" in r.message for r in caplog.records)
