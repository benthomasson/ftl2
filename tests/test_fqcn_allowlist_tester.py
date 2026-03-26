"""Comprehensive tests for FQCN modules respecting the _enabled_modules allowlist.

Tests that fully-qualified collection names (e.g. ansible.builtin.shell)
are checked against the allowlist, not just short names.

Covers the fix for issue #34: FQCN modules bypass _enabled_modules allowlist.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from ftl2.automation.context import AutomationContext
from ftl2.automation.proxy import NamespaceProxy, ModuleProxy, ModuleAccessProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(modules=None):
    """Create an AutomationContext with the given module allowlist.

    Patches _get_module to avoid the missing systemd import, and
    patches _check_name_collisions to avoid inventory-related setup.
    """
    with patch.object(AutomationContext, '_check_name_collisions'):
        ctx = AutomationContext(
            modules=modules,
            print_summary=False,
            quiet=True,
            state_file=None,
        )
    return ctx


# ===========================================================================
# 1. Unit tests for _check_module_allowed
# ===========================================================================

class TestCheckModuleAllowed:
    """Direct unit tests for AutomationContext._check_module_allowed."""

    def setup_method(self):
        self.ctx = _make_context(modules=["file", "copy"])

    def test_short_name_allowed(self):
        """Short name in allowlist passes."""
        self.ctx._check_module_allowed("file")  # should not raise

    def test_short_name_blocked(self):
        """Short name NOT in allowlist raises AttributeError."""
        with pytest.raises(AttributeError, match="not enabled"):
            self.ctx._check_module_allowed("shell")

    def test_fqcn_allowed_via_short_name(self):
        """FQCN is allowed when its short name is in the allowlist."""
        self.ctx._check_module_allowed("ansible.builtin.file")  # should not raise

    def test_fqcn_blocked_when_short_name_missing(self):
        """FQCN is blocked when its short name is NOT in the allowlist."""
        with pytest.raises(AttributeError, match="not enabled"):
            self.ctx._check_module_allowed("ansible.builtin.shell")

    def test_fqcn_allowed_via_full_name_in_list(self):
        """FQCN is allowed when the full FQCN is explicitly in the allowlist."""
        ctx = _make_context(modules=["ansible.builtin.shell"])
        ctx._check_module_allowed("ansible.builtin.shell")  # should not raise

    def test_no_restriction_allows_everything(self):
        """When _enabled_modules is None, everything passes."""
        ctx = _make_context(modules=None)
        ctx._check_module_allowed("anything")
        ctx._check_module_allowed("ansible.builtin.shell")
        ctx._check_module_allowed("community.general.foo")

    def test_empty_allowlist_blocks_everything(self):
        """An empty allowlist blocks all modules."""
        ctx = _make_context(modules=[])
        with pytest.raises(AttributeError, match="not enabled"):
            ctx._check_module_allowed("file")
        with pytest.raises(AttributeError, match="not enabled"):
            ctx._check_module_allowed("ansible.builtin.file")

    def test_error_message_includes_module_name(self):
        """Error message includes the blocked module name."""
        with pytest.raises(AttributeError, match="ansible.builtin.shell"):
            self.ctx._check_module_allowed("ansible.builtin.shell")

    def test_error_message_lists_enabled_modules(self):
        """Error message lists which modules ARE enabled."""
        with pytest.raises(AttributeError, match="file"):
            self.ctx._check_module_allowed("shell")

    def test_deeply_nested_fqcn(self):
        """Deeply nested FQCNs (3+ dots) extract correct short name."""
        ctx = _make_context(modules=["ec2_instance"])
        ctx._check_module_allowed("amazon.aws.ec2_instance")  # should pass
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("amazon.aws.s3_bucket")

    def test_single_component_name_no_dots(self):
        """A name with no dots is treated as a short name."""
        self.ctx._check_module_allowed("file")
        with pytest.raises(AttributeError):
            self.ctx._check_module_allowed("shell")

    def test_mixed_allowlist_short_and_fqcn(self):
        """Allowlist with both short names and FQCNs works correctly."""
        ctx = _make_context(modules=["file", "ansible.builtin.command"])
        ctx._check_module_allowed("file")  # short match
        ctx._check_module_allowed("ansible.builtin.file")  # short name of FQCN matches "file"
        ctx._check_module_allowed("ansible.builtin.command")  # full FQCN match
        # Symmetric matching: "command" matches because the allowlist entry
        # "ansible.builtin.command" has short name "command"
        ctx._check_module_allowed("command")  # symmetric match
        # But unrelated modules are still blocked
        with pytest.raises(AttributeError, match="not enabled"):
            ctx._check_module_allowed("shell")

    def test_fqcn_in_allowlist_matches_short_lookup_symmetrically(self):
        """When allowlist has FQCN 'a.b.command', short name 'command' IS matched.

        Symmetric matching: the check extracts short names from both the input
        AND the allowlist entries. So 'command' matches 'ansible.builtin.command'
        because the allowlist entry's short name is 'command'.
        """
        ctx = _make_context(modules=["ansible.builtin.command"])
        ctx._check_module_allowed("command")  # should pass via symmetric matching


# ===========================================================================
# 2. Integration tests: NamespaceProxy chain
# ===========================================================================

class TestNamespaceProxyAllowlist:
    """Test that NamespaceProxy.__call__ enforces the allowlist."""

    @pytest.mark.asyncio
    async def test_namespace_proxy_blocks_disallowed_fqcn(self):
        """NamespaceProxy.__call__ raises for modules not in the allowlist."""
        ctx = _make_context(modules=["file", "copy"])
        proxy = NamespaceProxy(ctx, "ansible.builtin.shell")

        with pytest.raises(AttributeError, match="not enabled"):
            await proxy(cmd="echo pwned")

    @pytest.mark.asyncio
    async def test_namespace_proxy_allows_permitted_fqcn(self):
        """NamespaceProxy.__call__ does NOT raise for allowed modules.

        It will fail later (missing module executor etc.) but the allowlist
        check itself passes.
        """
        ctx = _make_context(modules=["file"])
        proxy = NamespaceProxy(ctx, "ansible.builtin.file")
        # The allowlist check passes; the call will fail at execute() level
        # due to missing module, but that's not what we're testing
        try:
            await proxy(path="/tmp/test", state="touch")
        except AttributeError as e:
            if "not enabled" in str(e):
                pytest.fail("Allowlist check should have passed for 'ansible.builtin.file'")
        except Exception:
            pass  # Other errors (module execution) are expected

    def test_namespace_proxy_chain_builds_correct_path(self):
        """Chaining __getattr__ on NamespaceProxy builds the correct path."""
        ctx = _make_context(modules=["file"])
        proxy = NamespaceProxy(ctx, "ansible")
        proxy2 = proxy.__getattr__("builtin")
        proxy3 = proxy2.__getattr__("shell")
        assert proxy3._path == "ansible.builtin.shell"

    def test_namespace_proxy_repr(self):
        """NamespaceProxy repr shows the path."""
        ctx = _make_context(modules=["file"])
        proxy = NamespaceProxy(ctx, "ansible.builtin.file")
        assert "ansible.builtin.file" in repr(proxy)


# ===========================================================================
# 3. Integration tests: execute() defense-in-depth
# ===========================================================================

class TestExecuteAllowlist:
    """Test that execute() enforces the allowlist as defense-in-depth."""

    @pytest.mark.asyncio
    async def test_execute_blocks_disallowed_module(self):
        """Direct execute() call blocks modules not in the allowlist."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError, match="not enabled"):
            await ctx.execute("ansible.builtin.shell", {"cmd": "echo pwned"})

    @pytest.mark.asyncio
    async def test_execute_blocks_disallowed_short_name(self):
        """Direct execute() call blocks short names not in the allowlist."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError, match="not enabled"):
            await ctx.execute("shell", {"cmd": "echo pwned"})

    @pytest.mark.asyncio
    async def test_execute_allows_permitted_module(self):
        """Direct execute() call passes the allowlist for permitted modules.

        Will fail at module execution level, not at allowlist.
        """
        ctx = _make_context(modules=["file"])
        try:
            await ctx.execute("file", {"path": "/tmp/test", "state": "touch"})
        except AttributeError as e:
            if "not enabled" in str(e):
                pytest.fail("Allowlist should have allowed 'file'")
        except Exception:
            pass  # Other errors are expected (module executor)

    @pytest.mark.asyncio
    async def test_execute_allows_everything_when_unrestricted(self):
        """Direct execute() call passes when no restriction is set."""
        ctx = _make_context(modules=None)
        try:
            await ctx.execute("ansible.builtin.shell", {"cmd": "echo hello"})
        except AttributeError as e:
            if "not enabled" in str(e):
                pytest.fail("Should not block when modules=None")
        except Exception:
            pass  # Other errors are expected


# ===========================================================================
# 4. Integration tests: run_on() defense-in-depth
# ===========================================================================

class TestRunOnAllowlist:
    """Test that run_on() enforces the allowlist."""

    @pytest.mark.asyncio
    async def test_run_on_blocks_disallowed_module(self):
        """run_on() blocks modules not in the allowlist."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError, match="not enabled"):
            await ctx.run_on("localhost", "shell", cmd="echo pwned")

    @pytest.mark.asyncio
    async def test_run_on_blocks_fqcn_not_in_allowlist(self):
        """run_on() blocks FQCN modules not in the allowlist."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError, match="not enabled"):
            await ctx.run_on("localhost", "ansible.builtin.shell", cmd="echo pwned")


# ===========================================================================
# 5. Integration tests: __getattr__ proxy chain
# ===========================================================================

class TestGetAttrAllowlist:
    """Test the attribute access chain respects the allowlist."""

    def test_getattr_blocks_disallowed_short_module(self):
        """Accessing a disallowed short module name raises AttributeError."""
        ctx = _make_context(modules=["file"])
        # Only modules in list_modules() get checked at __getattr__ level
        from ftl2.ftl_modules import list_modules
        if "shell" in list_modules():
            with pytest.raises(AttributeError, match="not enabled"):
                getattr(ctx, "shell")

    def test_getattr_allows_permitted_short_module(self):
        """Accessing a permitted short module name does not raise."""
        ctx = _make_context(modules=["file"])
        from ftl2.ftl_modules import list_modules
        if "file" in list_modules():
            result = getattr(ctx, "file")
            # Should return a callable wrapper, not raise
            assert callable(result)


# ===========================================================================
# 6. Edge cases and regression tests
# ===========================================================================

class TestEdgeCases:
    """Edge cases for the FQCN allowlist fix."""

    def test_case_sensitivity(self):
        """Module names are case-sensitive."""
        ctx = _make_context(modules=["File"])
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("file")
        ctx._check_module_allowed("File")  # exact match

    def test_partial_name_no_match(self):
        """Substring matches don't count — must be exact."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("file2")
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("myfile")

    def test_allowlist_with_single_module(self):
        """Allowlist with exactly one module works."""
        ctx = _make_context(modules=["copy"])
        ctx._check_module_allowed("copy")
        ctx._check_module_allowed("ansible.builtin.copy")
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("file")

    def test_multiple_fqcns_all_blocked(self):
        """Multiple FQCN bypass attempts are all blocked."""
        ctx = _make_context(modules=["file"])
        blocked = [
            "ansible.builtin.shell",
            "ansible.builtin.command",
            "ansible.builtin.raw",
            "community.general.slack",
            "amazon.aws.ec2_instance",
        ]
        for fqcn in blocked:
            with pytest.raises(AttributeError, match="not enabled"):
                ctx._check_module_allowed(fqcn)

    def test_dot_only_name(self):
        """Edge case: name that is just dots."""
        ctx = _make_context(modules=["file"])
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("...")

    def test_trailing_dot(self):
        """Edge case: FQCN with trailing dot."""
        ctx = _make_context(modules=["file"])
        # "ansible.builtin.file." -> rsplit gives "" as short name
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("ansible.builtin.file.")

    @pytest.mark.asyncio
    async def test_bypass_via_full_proxy_chain(self):
        """The original bug: ftl.ansible.builtin.shell() bypasses allowlist.

        This is the exact scenario from issue #34.
        """
        ctx = _make_context(modules=["file", "copy"])
        # Simulate the proxy chain: ftl.ansible → ModuleProxy → NamespaceProxy chain
        # Then calling the final proxy should be blocked.
        ns = NamespaceProxy(ctx, "ansible.builtin.shell")
        with pytest.raises(AttributeError, match="not enabled"):
            await ns(cmd="echo pwned")

    @pytest.mark.asyncio
    async def test_bypass_via_execute_with_fqcn(self):
        """Direct execute() with FQCN is also blocked."""
        ctx = _make_context(modules=["file", "copy"])
        with pytest.raises(AttributeError, match="not enabled"):
            await ctx.execute("ansible.builtin.shell", {"cmd": "echo pwned"})


# ===========================================================================
# 7. Symmetric matching tests (iteration 2)
# ===========================================================================

class TestSymmetricMatching:
    """Tests for symmetric FQCN matching in the allowlist.

    When the allowlist contains FQCNs like 'ansible.builtin.command',
    both the FQCN and the short name 'command' should be allowed.
    """

    def test_fqcn_allowlist_allows_short_name(self):
        """modules=['ansible.builtin.command'] allows 'command'."""
        ctx = _make_context(modules=["ansible.builtin.command"])
        ctx._check_module_allowed("command")

    def test_fqcn_allowlist_allows_fqcn(self):
        """modules=['ansible.builtin.command'] allows 'ansible.builtin.command'."""
        ctx = _make_context(modules=["ansible.builtin.command"])
        ctx._check_module_allowed("ansible.builtin.command")

    def test_fqcn_allowlist_blocks_other_short_names(self):
        """modules=['ansible.builtin.command'] blocks 'shell'."""
        ctx = _make_context(modules=["ansible.builtin.command"])
        with pytest.raises(AttributeError, match="not enabled"):
            ctx._check_module_allowed("shell")

    def test_fqcn_allowlist_blocks_other_fqcns(self):
        """modules=['ansible.builtin.command'] blocks 'ansible.builtin.shell'."""
        ctx = _make_context(modules=["ansible.builtin.command"])
        with pytest.raises(AttributeError, match="not enabled"):
            ctx._check_module_allowed("ansible.builtin.shell")

    def test_multiple_fqcn_entries_symmetric(self):
        """Multiple FQCN entries all allow their short names."""
        ctx = _make_context(modules=["ansible.builtin.file", "ansible.builtin.copy"])
        ctx._check_module_allowed("file")
        ctx._check_module_allowed("copy")
        ctx._check_module_allowed("ansible.builtin.file")
        ctx._check_module_allowed("ansible.builtin.copy")
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("shell")

    def test_mixed_short_and_fqcn_entries(self):
        """Mix of short names and FQCNs in the allowlist all work symmetrically."""
        ctx = _make_context(modules=["file", "ansible.builtin.command"])
        # "file" short name
        ctx._check_module_allowed("file")
        ctx._check_module_allowed("ansible.builtin.file")
        # "ansible.builtin.command" FQCN entry
        ctx._check_module_allowed("command")
        ctx._check_module_allowed("ansible.builtin.command")
        # Blocked
        with pytest.raises(AttributeError):
            ctx._check_module_allowed("shell")

    @pytest.mark.asyncio
    async def test_symmetric_via_namespace_proxy(self):
        """NamespaceProxy allows FQCN when its short name matches an allowlist FQCN entry."""
        ctx = _make_context(modules=["ansible.builtin.file"])
        proxy = NamespaceProxy(ctx, "ansible.builtin.file")
        # Should pass the allowlist check (exact match)
        try:
            await proxy(path="/tmp/test", state="touch")
        except AttributeError as e:
            if "not enabled" in str(e):
                pytest.fail("Allowlist should allow 'ansible.builtin.file'")
        except Exception:
            pass


# ===========================================================================
# 8. available_modules property tests
# ===========================================================================

class TestAvailableModules:
    """Tests for the available_modules property with FQCN entries."""

    def test_available_modules_unrestricted(self):
        """With no restriction, available_modules returns all known modules."""
        ctx = _make_context(modules=None)
        available = ctx.available_modules
        assert len(available) > 0

    def test_available_modules_short_names(self):
        """Short names in allowlist appear in available_modules if they are known."""
        from ftl2.ftl_modules import list_modules
        known = list_modules()
        ctx = _make_context(modules=["file", "copy"])
        available = ctx.available_modules
        if "file" in known:
            assert "file" in available
        if "copy" in known:
            assert "copy" in available

    def test_available_modules_fqcn_entries(self):
        """FQCN entries in allowlist appear in available_modules when their short name is known."""
        from ftl2.ftl_modules import list_modules
        known = list_modules()
        if "file" in known:
            ctx = _make_context(modules=["ansible.builtin.file"])
            available = ctx.available_modules
            assert "ansible.builtin.file" in available

    def test_available_modules_unknown_module_excluded(self):
        """Unknown module names are excluded from available_modules."""
        ctx = _make_context(modules=["nonexistent_module_xyz"])
        available = ctx.available_modules
        assert "nonexistent_module_xyz" not in available
