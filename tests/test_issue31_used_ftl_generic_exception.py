"""Tests for issue #31: used_ftl unconditionally set to False on generic exceptions.

Verifies that the generic exception handler in execute() correctly sets
used_ftl based on whether an FTL module was dispatched, rather than
hardcoding False.
"""

from unittest.mock import MagicMock, patch

import pytest

from ftl2.ftl_modules.exceptions import FTLModuleError
from ftl2.ftl_modules.executor import (
    ExecuteResult,
    LocalHost,
    execute,
)

# ---------------------------------------------------------------------------
# ExecuteResult.from_error() unit tests
# ---------------------------------------------------------------------------


class TestFromErrorUsedFtl:
    """Tests for the used_ftl parameter on ExecuteResult.from_error()."""

    def test_from_error_default_used_ftl_is_false(self):
        """from_error() defaults used_ftl to False for backward compat."""
        result = ExecuteResult.from_error("err", "mymod", "host1")
        assert result.used_ftl is False

    def test_from_error_explicit_used_ftl_true(self):
        """from_error() accepts used_ftl=True."""
        result = ExecuteResult.from_error("err", "mymod", "host1", used_ftl=True)
        assert result.used_ftl is True

    def test_from_error_explicit_used_ftl_false(self):
        """from_error() accepts used_ftl=False explicitly."""
        result = ExecuteResult.from_error("err", "mymod", "host1", used_ftl=False)
        assert result.used_ftl is False

    def test_from_error_used_ftl_is_keyword_only(self):
        """used_ftl must be passed as keyword arg, not positional."""
        with pytest.raises(TypeError):
            ExecuteResult.from_error("err", "mymod", "host1", True)

    def test_from_error_preserves_other_fields(self):
        """from_error() still sets success=False, error message, etc."""
        result = ExecuteResult.from_error("boom", "mod", "h", used_ftl=True)
        assert result.success is False
        assert result.error == "boom"
        assert result.output == {"failed": True, "msg": "boom"}
        assert result.module == "mod"
        assert result.host == "h"
        assert result.changed is False


# ---------------------------------------------------------------------------
# execute() generic exception handler tests
# ---------------------------------------------------------------------------


class TestExecuteGenericExceptionUsedFtl:
    """Tests that execute() sets used_ftl correctly when a generic Exception
    is raised (not FTLModuleError)."""

    @pytest.mark.asyncio
    async def test_ftl_module_generic_exception_sets_used_ftl_true(self):
        """When an FTL module exists but raises a generic Exception,
        used_ftl should be True."""
        fake_module = MagicMock()

        with patch("ftl2.ftl_modules.executor._get_module", return_value=fake_module), \
             patch("ftl2.ftl_modules.executor._execute_ftl_module",
                   side_effect=Exception("unexpected crash")):
            result = await execute("some_ftl_mod", {"arg": "val"})

        assert result.success is False
        assert result.used_ftl is True
        assert "unexpected crash" in result.error

    @pytest.mark.asyncio
    async def test_no_ftl_module_generic_exception_sets_used_ftl_false(self):
        """When no FTL module exists and a generic Exception is raised,
        used_ftl should be False."""
        with patch("ftl2.ftl_modules.executor._get_module", return_value=None), \
             patch("ftl2.ftl_modules.executor._execute_ansible_module_local",
                   side_effect=Exception("ansible blew up")):
            result = await execute("ansible_only_mod", {"arg": "val"})

        assert result.success is False
        assert result.used_ftl is False
        assert "ansible blew up" in result.error

    @pytest.mark.asyncio
    async def test_ftl_module_ftl_module_error_sets_used_ftl_true(self):
        """FTLModuleError handler also sets used_ftl correctly (regression check)."""
        fake_module = MagicMock()

        with patch("ftl2.ftl_modules.executor._get_module", return_value=fake_module), \
             patch("ftl2.ftl_modules.executor._execute_ftl_module",
                   side_effect=FTLModuleError("module failed", changed=True)):
            result = await execute("ftl_mod", {"arg": "val"})

        assert result.success is False
        assert result.used_ftl is True

    @pytest.mark.asyncio
    async def test_no_ftl_module_ftl_module_error_sets_used_ftl_false(self):
        """FTLModuleError with no FTL module (e.g., raised during fallback path)
        should set used_ftl=False."""
        with patch("ftl2.ftl_modules.executor._get_module", return_value=None), \
             patch("ftl2.ftl_modules.executor._execute_ansible_module_local",
                   side_effect=FTLModuleError("fallback failed")):
            result = await execute("ansible_mod", {})

        assert result.success is False
        assert result.used_ftl is False

    @pytest.mark.asyncio
    async def test_generic_exception_preserves_module_and_host(self):
        """Generic exception result should carry correct module name and host."""
        fake_module = MagicMock()
        host = LocalHost(name="myhost")

        with patch("ftl2.ftl_modules.executor._get_module", return_value=fake_module), \
             patch("ftl2.ftl_modules.executor._execute_ftl_module",
                   side_effect=RuntimeError("runtime failure")):
            result = await execute("mymod", {}, host=host)

        assert result.module == "mymod"
        assert result.host == "myhost"
        assert result.used_ftl is True

    @pytest.mark.asyncio
    async def test_generic_exception_with_remote_host(self):
        """Generic exception on remote path should set used_ftl=False
        (remote always has used_ftl=False since FTL modules run locally)."""
        mock_host = MagicMock()
        mock_host.name = "remote-server"
        mock_host.is_local = False

        with patch("ftl2.ftl_modules.executor._get_module", return_value=MagicMock()), \
             patch("ftl2.ftl_modules.executor._execute_remote",
                   side_effect=ConnectionError("ssh failed")):
            result = await execute("command", {"cmd": "ls"}, host=mock_host)

        assert result.success is False
        # ftl_module is not None, so used_ftl should be True even on remote
        # (the fix uses ftl_module is not None, not the execution path)
        assert result.used_ftl is True
        assert "ssh failed" in result.error


# ---------------------------------------------------------------------------
# Integration-style tests (mock _get_module to avoid missing systemd import)
# ---------------------------------------------------------------------------


class TestExecuteUsedFtlIntegration:
    """Integration tests confirming used_ftl accuracy with _get_module mocked."""

    @pytest.mark.asyncio
    async def test_ftl_module_success_used_ftl_true(self):
        """An FTL module that succeeds should have used_ftl=True."""
        async def fake_command(**kwargs):
            return {"changed": True, "stdout": "hello", "rc": 0}

        with patch("ftl2.ftl_modules.executor._get_module", return_value=fake_command):
            result = await execute("command", {"cmd": "echo hello"})

        assert result.success is True
        assert result.used_ftl is True

    @pytest.mark.asyncio
    async def test_ftl_module_failure_used_ftl_true(self):
        """An FTL module that raises FTLModuleError should have used_ftl=True."""
        async def failing_file(**kwargs):
            raise FTLModuleError("does not exist", path="/nonexistent")

        with patch("ftl2.ftl_modules.executor._get_module", return_value=failing_file):
            result = await execute("file", {"path": "/nonexistent", "state": "file"})

        assert result.success is False
        assert result.used_ftl is True
        assert "does not exist" in result.error

    @pytest.mark.asyncio
    async def test_nonexistent_module_used_ftl_false(self):
        """A module with no FTL impl should have used_ftl=False."""
        with patch("ftl2.ftl_modules.executor._get_module", return_value=None), \
             patch("ftl2.ftl_modules.executor._execute_ansible_module_local",
                   side_effect=Exception("module not found")):
            result = await execute("totally_fake_module_xyz", {})

        assert result.success is False
        assert result.used_ftl is False

    @pytest.mark.asyncio
    async def test_ftl_module_generic_exception_error_message_preserved(self):
        """Error message from generic exception should be preserved in result."""
        async def crashing_module(**kwargs):
            raise ValueError("invalid argument: foo")

        with patch("ftl2.ftl_modules.executor._get_module", return_value=crashing_module):
            result = await execute("mymod", {})

        assert result.success is False
        assert result.used_ftl is True
        assert result.error == "invalid argument: foo"
        assert result.output["failed"] is True
        assert result.output["msg"] == "invalid argument: foo"
