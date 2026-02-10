"""Tests for excluded Ansible modules in FTL2."""

import pytest

from ftl2 import automation
from ftl2.exceptions import ExcludedModuleError
from ftl2.module_loading.excluded import (
    EXCLUDED_MODULES,
    ExcludedModule,
    get_excluded,
    is_excluded,
)


class TestExcludedModulesRegistry:
    """Tests for the excluded modules registry."""

    def test_excluded_modules_has_expected_modules(self):
        """Test that common excluded modules are in the registry."""
        # Note: wait_for_connection and ping are now SHADOWED, not excluded
        # Note: wait_for was removed from excluded — FTL2 now has a native implementation
        expected = [
            "debug",
            "fail",
            "pause",
            "set_fact",
            "meta",
            "include_tasks",
            "import_tasks",
        ]
        for module in expected:
            assert module in EXCLUDED_MODULES, f"{module} should be excluded"

    def test_fqcn_modules_are_excluded(self):
        """Test that FQCN versions are also excluded."""
        # Note: wait_for_connection is now SHADOWED, not excluded
        assert "ansible.builtin.debug" in EXCLUDED_MODULES
        assert "ansible.builtin.set_fact" in EXCLUDED_MODULES

    def test_short_names_added(self):
        """Test that short names are added for ansible.builtin modules."""
        # Both FQCN and short name should exist
        assert "ansible.builtin.debug" in EXCLUDED_MODULES
        assert "debug" in EXCLUDED_MODULES

        # They should refer to the same module info
        fqcn_module = EXCLUDED_MODULES["ansible.builtin.debug"]
        short_module = EXCLUDED_MODULES["debug"]
        assert fqcn_module.name == short_module.name

    def test_is_excluded_returns_true_for_excluded(self):
        """Test is_excluded() returns True for excluded modules."""
        # Note: wait_for_connection is now SHADOWED, not excluded
        # Note: wait_for was removed from excluded — FTL2 now has a native implementation
        assert is_excluded("debug") is True
        assert is_excluded("ansible.builtin.debug") is True

    def test_is_excluded_returns_false_for_valid(self):
        """Test is_excluded() returns False for valid modules."""
        assert is_excluded("file") is False
        assert is_excluded("copy") is False
        assert is_excluded("ansible.builtin.file") is False

    def test_get_excluded_returns_module_info(self):
        """Test get_excluded() returns ExcludedModule for excluded modules."""
        excluded = get_excluded("debug")
        assert excluded is not None
        assert isinstance(excluded, ExcludedModule)
        assert excluded.name == "debug"
        assert "print()" in excluded.alternative

    def test_get_excluded_returns_none_for_valid(self):
        """Test get_excluded() returns None for valid modules."""
        assert get_excluded("file") is None
        assert get_excluded("copy") is None

    def test_excluded_module_has_reason(self):
        """Test that excluded modules have a reason."""
        for name, module in EXCLUDED_MODULES.items():
            assert module.reason, f"{name} should have a reason"

    def test_excluded_module_has_alternative(self):
        """Test that excluded modules have an alternative."""
        for name, module in EXCLUDED_MODULES.items():
            assert module.alternative, f"{name} should have an alternative"


class TestExcludedModuleError:
    """Tests for ExcludedModuleError exception."""

    def test_error_message_includes_module_name(self):
        """Test error message includes the module name."""
        module = EXCLUDED_MODULES["debug"]
        error = ExcludedModuleError(module)
        assert "debug" in str(error)

    def test_error_message_includes_reason(self):
        """Test error message includes the reason."""
        module = EXCLUDED_MODULES["debug"]
        error = ExcludedModuleError(module)
        assert module.reason in str(error)

    def test_error_message_includes_alternative(self):
        """Test error message includes the alternative."""
        module = EXCLUDED_MODULES["debug"]
        error = ExcludedModuleError(module)
        assert module.alternative in str(error)

    def test_error_message_includes_example(self):
        """Test error message includes example if present."""
        module = EXCLUDED_MODULES["debug"]
        error = ExcludedModuleError(module)
        if module.example:
            assert "print(" in str(error)


class TestExcludedModuleIntegration:
    """Integration tests for excluded module detection during execution."""

    @pytest.mark.asyncio
    async def test_excluded_module_via_simple_name_raises(self):
        """Test that calling excluded module via simple name raises."""
        async with automation(print_summary=False, quiet=True) as ftl:
            with pytest.raises(ExcludedModuleError) as exc_info:
                await ftl.debug(msg="test")

            assert "debug" in str(exc_info.value)
            assert "print()" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_excluded_module_via_fqcn_raises(self):
        """Test that calling excluded module via FQCN raises."""
        async with automation(print_summary=False, quiet=True) as ftl:
            with pytest.raises(ExcludedModuleError) as exc_info:
                await ftl.ansible.builtin.debug(msg="test")

            assert "debug" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_excluded_module_via_host_scoped_raises(self):
        """Test that calling excluded module via host-scoped proxy raises."""
        async with automation(print_summary=False, quiet=True) as ftl:
            with pytest.raises(ExcludedModuleError) as exc_info:
                await ftl.local.debug(msg="test")

            assert "debug" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_set_fact_suggests_python_variables(self):
        """Test that set_fact error suggests Python variables."""
        async with automation(print_summary=False, quiet=True) as ftl:
            with pytest.raises(ExcludedModuleError) as exc_info:
                await ftl.local.set_fact(my_var="value")

            error_msg = str(exc_info.value)
            assert "set_fact" in error_msg
            assert "Python variables" in error_msg

    @pytest.mark.asyncio
    async def test_pause_suggests_asyncio_sleep(self):
        """Test that pause error suggests asyncio.sleep."""
        async with automation(print_summary=False, quiet=True) as ftl:
            with pytest.raises(ExcludedModuleError) as exc_info:
                await ftl.local.pause(seconds=5)

            error_msg = str(exc_info.value)
            assert "pause" in error_msg
            assert "asyncio.sleep" in error_msg

    @pytest.mark.asyncio
    async def test_valid_modules_still_work(self):
        """Test that valid modules are not blocked."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(print_summary=False, quiet=True) as ftl:
                # These should work fine
                result = await ftl.file(path=str(test_file), state="touch")
                assert result["changed"] is True

                result = await ftl.command(cmd="echo hello")
                assert result["stdout"].strip() == "hello"


class TestWaitForSSH:
    """Tests for the wait_for_ssh native method."""

    @pytest.mark.asyncio
    async def test_wait_for_ssh_localhost(self):
        """Test wait_for_ssh on localhost (should succeed quickly)."""
        async with automation(print_summary=False, quiet=True) as ftl:
            # localhost should have SSH available (or this test should be skipped)
            try:
                await ftl.local.wait_for_ssh(timeout=5)
            except TimeoutError:
                pytest.skip("SSH not running on localhost")

    @pytest.mark.asyncio
    async def test_wait_for_ssh_timeout(self):
        """Test wait_for_ssh raises TimeoutError on unreachable host."""
        async with automation(
            inventory={"test": {"hosts": {"unreachable": {"ansible_host": "192.0.2.1"}}}},
            print_summary=False,
            quiet=True,
        ) as ftl:
            with pytest.raises(TimeoutError) as exc_info:
                # Use sleep parameter (matches Ansible's wait_for_connection)
                await ftl.unreachable.wait_for_ssh(timeout=2, sleep=1)

            assert "192.0.2.1" in str(exc_info.value)
            assert "2 seconds" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_wait_for_ssh_exists_on_host_proxy(self):
        """Test that wait_for_ssh method exists on HostScopedProxy."""
        async with automation(print_summary=False, quiet=True) as ftl:
            # Check method exists
            assert hasattr(ftl.local, "wait_for_ssh")
            assert callable(ftl.local.wait_for_ssh)
