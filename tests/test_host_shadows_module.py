"""Tests for host-name-shadows-module-name fix (Issue #33).

Validates that:
- Warnings fire when host/group names shadow module names
- Warnings are deduplicated (only once per name)
- ftl.module.<name>() escape hatch resolves to modules
- Escape hatch respects _enabled_modules and _check_excluded
- Init-time collision detection works
"""

import warnings
from unittest.mock import MagicMock, patch

import pytest

from ftl2.automation.proxy import ModuleAccessProxy, ModuleProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHostsProxy:
    """Minimal HostsProxy stub with controllable groups and host keys."""

    def __init__(self, groups=None, host_keys=None):
        self._groups = groups or []
        self._host_keys = host_keys or []

    @property
    def groups(self):
        return self._groups

    def keys(self):
        return self._host_keys


class _FakeContext:
    """Minimal AutomationContext stub for proxy testing."""

    def __init__(self, hosts_proxy=None, enabled_modules=None):
        self._hosts_proxy = hosts_proxy or _FakeHostsProxy()
        self._enabled_modules = enabled_modules

    @property
    def hosts(self):
        return self._hosts_proxy

    async def execute(self, module_name, kwargs):
        return {"module": module_name, **kwargs}


def _make_context(groups=None, host_keys=None, enabled_modules=None):
    hp = _FakeHostsProxy(groups=groups, host_keys=host_keys)
    return _FakeContext(hosts_proxy=hp, enabled_modules=enabled_modules)


# ---------------------------------------------------------------------------
# 1. Access-time shadow warning
# ---------------------------------------------------------------------------

class TestWarnShadow:
    """ModuleProxy._warn_shadow emits warnings correctly."""

    def test_warns_when_host_shadows_module(self):
        """Accessing a host name that matches a module triggers a warning."""
        ctx = _make_context(host_keys=["file"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = proxy.__getattr__("file")
        assert len(w) == 1
        assert "shadows" in str(w[0].message)
        assert "ftl.module.file()" in str(w[0].message)
        assert w[0].category is UserWarning

    def test_warns_when_group_shadows_module(self):
        """A group name matching a module also triggers a warning."""
        ctx = _make_context(groups=["copy"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            proxy.__getattr__("copy")
        assert len(w) == 1
        assert "'copy'" in str(w[0].message)

    def test_warning_deduplicated(self):
        """Second access to the same shadowed name does NOT re-warn."""
        ctx = _make_context(host_keys=["file"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            proxy.__getattr__("file")
            proxy.__getattr__("file")
        assert len(w) == 1

    def test_no_warning_for_non_module_host(self):
        """A host name that is NOT a module should not trigger a warning."""
        ctx = _make_context(host_keys=["my_custom_host"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            proxy.__getattr__("my_custom_host")
        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 0

    def test_multiple_different_shadows_each_warn(self):
        """Each distinct shadowed name gets its own warning."""
        ctx = _make_context(host_keys=["file", "copy"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            proxy.__getattr__("file")
            proxy.__getattr__("copy")
        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 2


# ---------------------------------------------------------------------------
# 2. Host-first resolution priority preserved
# ---------------------------------------------------------------------------

class TestHostFirstPriority:
    """Hosts still take priority over modules (no breaking change)."""

    def test_host_returned_not_module(self):
        """ftl.file returns HostScopedProxy, not a module wrapper, when 'file' is a host."""
        ctx = _make_context(host_keys=["file"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = proxy.__getattr__("file")
        from ftl2.automation.proxy import HostScopedProxy
        assert isinstance(result, HostScopedProxy)

    def test_module_returned_when_no_host_conflict(self):
        """When no host named 'file' exists, ftl.file returns a module wrapper."""
        ctx = _make_context(host_keys=["webserver"])
        proxy = ModuleProxy(ctx)
        result = proxy.__getattr__("file")
        assert callable(result)
        assert result.__name__ == "file"


# ---------------------------------------------------------------------------
# 3. ModuleAccessProxy (escape hatch)
# ---------------------------------------------------------------------------

class TestModuleAccessProxy:
    """ftl.module.<name> always resolves to modules."""

    def test_returns_callable_for_known_module(self):
        """ftl.module.file returns a callable for the file module."""
        ctx = _make_context(host_keys=["file"])
        access = ModuleAccessProxy(ctx)
        result = access.__getattr__("file")
        assert callable(result)
        assert result.__name__ == "file"

    def test_raises_for_unknown_name(self):
        """ftl.module.nonexistent raises AttributeError."""
        ctx = _make_context()
        access = ModuleAccessProxy(ctx)
        with pytest.raises(AttributeError, match="not a known module"):
            access.__getattr__("nonexistent_xyz")

    def test_raises_for_private_attr(self):
        """ftl.module._private raises AttributeError."""
        ctx = _make_context()
        access = ModuleAccessProxy(ctx)
        with pytest.raises(AttributeError):
            access.__getattr__("_private")

    def test_respects_enabled_modules(self):
        """ftl.module.file raises AttributeError when 'file' not in enabled list."""
        ctx = _make_context(enabled_modules=["hostname"])
        access = ModuleAccessProxy(ctx)
        with pytest.raises(AttributeError, match="not enabled"):
            access.__getattr__("file")

    def test_allowed_when_in_enabled_modules(self):
        """ftl.module.file works when 'file' is in enabled list."""
        ctx = _make_context(enabled_modules=["file"])
        access = ModuleAccessProxy(ctx)
        result = access.__getattr__("file")
        assert callable(result)

    def test_respects_excluded_modules(self):
        """ftl.module.<excluded> raises ExcludedModuleError."""
        from ftl2.exceptions import ExcludedModuleError
        ctx = _make_context()
        access = ModuleAccessProxy(ctx)
        with patch("ftl2.automation.proxy.get_excluded") as mock_excl:
            mock_excl.return_value = MagicMock(reason="deprecated")
            with pytest.raises(ExcludedModuleError):
                access.__getattr__("file")

    def test_repr(self):
        ctx = _make_context()
        access = ModuleAccessProxy(ctx)
        assert repr(access) == "ModuleAccessProxy()"


# ---------------------------------------------------------------------------
# 4. ModuleProxy.module property
# ---------------------------------------------------------------------------

class TestModuleProperty:
    """The 'module' property on ModuleProxy returns ModuleAccessProxy."""

    def test_module_property_returns_access_proxy(self):
        ctx = _make_context()
        proxy = ModuleProxy(ctx)
        assert isinstance(proxy.module, ModuleAccessProxy)

    def test_module_property_bypasses_host_lookup(self):
        """ftl.module.file resolves to module even when host 'file' exists."""
        ctx = _make_context(host_keys=["file"])
        proxy = ModuleProxy(ctx)
        result = proxy.module.__getattr__("file")
        assert callable(result)
        assert result.__name__ == "file"


# ---------------------------------------------------------------------------
# 5. Init-time collision detection
# ---------------------------------------------------------------------------

class TestInitTimeCollisionWarning:
    """AutomationContext._check_name_collisions warns at setup time."""

    def test_warns_on_host_module_collision(self):
        """_check_name_collisions emits warnings for colliding names."""
        from ftl2.automation.context import AutomationContext

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Patch to avoid full context init — just test the method
            ctx = object.__new__(AutomationContext)
            ctx._enabled_modules = None
            # Create a fake hosts proxy with a colliding name
            hp = _FakeHostsProxy(host_keys=["file"])
            ctx._hosts_proxy = hp
            # Patch list_modules to return known modules
            with patch("ftl2.automation.context.list_modules", return_value=["file", "copy", "shell"]):
                ctx._check_name_collisions()

        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 1
        assert "'file'" in str(shadow_warnings[0].message)

    def test_no_warning_when_no_collisions(self):
        """No warnings when host names don't match any modules."""
        from ftl2.automation.context import AutomationContext

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ctx = object.__new__(AutomationContext)
            ctx._enabled_modules = None
            hp = _FakeHostsProxy(host_keys=["webserver", "database"])
            ctx._hosts_proxy = hp
            with patch("ftl2.automation.context.list_modules", return_value=["file", "copy", "shell"]):
                ctx._check_name_collisions()

        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 0

    def test_warns_for_group_collision(self):
        """Groups that match module names also trigger warnings."""
        from ftl2.automation.context import AutomationContext

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ctx = object.__new__(AutomationContext)
            ctx._enabled_modules = None
            hp = _FakeHostsProxy(groups=["shell"])
            ctx._hosts_proxy = hp
            with patch("ftl2.automation.context.list_modules", return_value=["file", "copy", "shell"]):
                ctx._check_name_collisions()

        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 1
        assert "'shell'" in str(shadow_warnings[0].message)

    def test_multiple_collisions(self):
        """Multiple collisions each get their own warning."""
        from ftl2.automation.context import AutomationContext

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ctx = object.__new__(AutomationContext)
            ctx._enabled_modules = None
            hp = _FakeHostsProxy(host_keys=["file", "copy"], groups=["shell"])
            ctx._hosts_proxy = hp
            with patch("ftl2.automation.context.list_modules", return_value=["file", "copy", "shell"]):
                ctx._check_name_collisions()

        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 3

    def test_no_warning_when_no_modules_loaded(self):
        """No warnings if list_modules returns empty."""
        from ftl2.automation.context import AutomationContext

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ctx = object.__new__(AutomationContext)
            ctx._enabled_modules = None
            hp = _FakeHostsProxy(host_keys=["file"])
            ctx._hosts_proxy = hp
            with patch("ftl2.automation.context.list_modules", return_value=[]):
                ctx._check_name_collisions()

        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 0


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases from reviewer notes."""

    def test_host_named_module_does_not_break_escape_hatch(self):
        """A host named 'module' should not break ftl.module property.

        ftl.module resolves via the @property, not __getattr__,
        so a host named 'module' should not interfere.
        """
        ctx = _make_context(host_keys=["module"])
        proxy = ModuleProxy(ctx)
        # The property should still work
        result = proxy.module
        assert isinstance(result, ModuleAccessProxy)

    def test_underscore_dash_normalization_no_false_shadow(self):
        """Underscore-to-dash normalization shouldn't trigger shadow warnings.

        If host is 'my-host' and user accesses ftl.my_host, the normalized
        name 'my-host' is used — this shouldn't trigger a shadow warning
        for modules since module names don't typically have dashes.
        """
        ctx = _make_context(host_keys=["my-host"])
        proxy = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Access via underscore — will normalize to my-host
            result = proxy.__getattr__("my_host")
        from ftl2.automation.proxy import HostScopedProxy
        assert isinstance(result, HostScopedProxy)
        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        assert len(shadow_warnings) == 0

    def test_warned_shadows_set_isolation(self):
        """Each ModuleProxy has its own dedup set."""
        ctx = _make_context(host_keys=["file"])
        proxy1 = ModuleProxy(ctx)
        proxy2 = ModuleProxy(ctx)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            proxy1.__getattr__("file")
            proxy2.__getattr__("file")
        shadow_warnings = [x for x in w if "shadows" in str(x.message)]
        # Each proxy warns independently
        assert len(shadow_warnings) == 2
