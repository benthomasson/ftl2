"""Tests for FTL Module Executor (Phase 5)."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.ftl_modules.executor import (
    ExecuteResult,
    LocalHost,
    _execute_ftl_module,
    execute,
    execute_batch,
    execute_on_hosts,
    run,
    run_on,
)


class TestExecuteResult:
    """Tests for ExecuteResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = ExecuteResult(
            success=True,
            changed=True,
            output={"msg": "done"},
            module="file",
        )
        assert result.success is True
        assert result.changed is True
        assert result.used_ftl is True

    def test_from_module_output(self):
        """Test creating result from module output."""
        output = {"changed": True, "path": "/tmp/test"}
        result = ExecuteResult.from_module_output(output, "file")

        assert result.success is True
        assert result.changed is True
        assert result.output == output
        assert result.module == "file"

    def test_from_module_output_failed(self):
        """Test creating result from failed module output."""
        output = {"failed": True, "msg": "error message"}
        result = ExecuteResult.from_module_output(output, "file")

        assert result.success is False
        assert result.error == "error message"

    def test_from_error(self):
        """Test creating result from error."""
        result = ExecuteResult.from_error("Something went wrong", "file")

        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.output["failed"] is True


class TestLocalHost:
    """Tests for LocalHost."""

    def test_localhost_properties(self):
        """Test localhost default properties."""
        host = LocalHost()

        assert host.name == "localhost"
        assert host.is_local is True


class TestExecute:
    """Tests for execute function."""

    @pytest.mark.asyncio
    async def test_execute_ftl_module_locally(self):
        """Test executing FTL module locally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            result = await execute(
                "file",
                {"path": str(test_file), "state": "touch"},
            )

            assert result.success is True
            assert result.changed is True
            assert result.used_ftl is True
            assert result.module == "file"
            assert result.host == "localhost"
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_execute_command_module(self):
        """Test executing command module."""
        result = await execute("command", {"cmd": "echo hello"})

        assert result.success is True
        assert result.changed is True
        assert "hello" in result.output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_execute_with_explicit_localhost(self):
        """Test executing with explicit LocalHost."""
        host = LocalHost(name="myhost")
        result = await execute("command", {"cmd": "echo test"}, host=host)

        assert result.success is True
        assert result.host == "myhost"

    @pytest.mark.asyncio
    async def test_execute_handles_ftl_error(self):
        """Test that FTLModuleError is handled properly."""
        result = await execute(
            "file",
            {"path": "/nonexistent/path/file.txt", "state": "file"},
        )

        assert result.success is False
        assert "does not exist" in result.error

    @pytest.mark.asyncio
    async def test_execute_async_module(self):
        """Test executing async module (uri)."""
        with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.url = "https://example.com/"
            mock_response.text = "response"
            mock_response.headers = {"content-type": "text/html"}

            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            result = await execute("uri", {"url": "https://example.com/"})

            assert result.success is True
            assert result.used_ftl is True

    @pytest.mark.asyncio
    async def test_execute_by_fqcn(self):
        """Test executing by Ansible FQCN."""
        result = await execute(
            "ansible.builtin.command",
            {"cmd": "echo fqcn_test"},
        )

        assert result.success is True
        assert "fqcn_test" in result.output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_execute_nonexistent_module_falls_back(self):
        """Test that nonexistent FTL module falls back to Ansible."""
        # This will try to fall back to module_loading
        result = await execute(
            "nonexistent_module_xyz",
            {},
        )

        # Should fail because module doesn't exist anywhere
        assert result.success is False
        assert result.used_ftl is False


class TestExecuteOnHosts:
    """Tests for execute_on_hosts function."""

    @pytest.mark.asyncio
    async def test_execute_on_multiple_hosts(self):
        """Test concurrent execution on multiple hosts."""
        hosts = [LocalHost(name=f"host{i}") for i in range(3)]

        results = await execute_on_hosts(
            hosts,
            "command",
            {"cmd": "echo concurrent"},
        )

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.success is True
            assert result.host == f"host{i}"
            assert "concurrent" in result.output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_execute_on_hosts_preserves_order(self):
        """Test that results are in same order as hosts."""
        hosts = [LocalHost(name=f"host{i}") for i in range(5)]

        results = await execute_on_hosts(
            hosts,
            "command",
            {"cmd": "echo test"},
        )

        for i, result in enumerate(results):
            assert result.host == f"host{i}"


class TestExecuteBatch:
    """Tests for execute_batch function."""

    @pytest.mark.asyncio
    async def test_batch_execution(self):
        """Test executing different modules in batch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = Path(tmpdir) / "file1.txt"
            file2 = Path(tmpdir) / "file2.txt"

            tasks = [
                ("file", {"path": str(file1), "state": "touch"}, None),
                ("file", {"path": str(file2), "state": "touch"}, None),
                ("command", {"cmd": "echo batch"}, None),
            ]

            results = await execute_batch(tasks)

            assert len(results) == 3
            assert all(r.success for r in results)
            assert file1.exists()
            assert file2.exists()
            assert "batch" in results[2].output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_batch_with_different_hosts(self):
        """Test batch execution with different hosts."""
        host1 = LocalHost(name="host1")
        host2 = LocalHost(name="host2")

        tasks = [
            ("command", {"cmd": "echo one"}, host1),
            ("command", {"cmd": "echo two"}, host2),
        ]

        results = await execute_batch(tasks)

        assert results[0].host == "host1"
        assert results[1].host == "host2"


class TestConvenienceFunctions:
    """Tests for run() and run_on() convenience functions."""

    @pytest.mark.asyncio
    async def test_run(self):
        """Test run() convenience function."""
        result = await run("command", cmd="echo convenience")

        assert result.success is True
        assert "convenience" in result.output.get("stdout", "")

    @pytest.mark.asyncio
    async def test_run_on(self):
        """Test run_on() convenience function."""
        host = LocalHost(name="testhost")
        result = await run_on(host, "command", cmd="echo specific")

        assert result.success is True
        assert result.host == "testhost"


class TestExecuteFtlModule:
    """Tests for _execute_ftl_module helper."""

    @pytest.mark.asyncio
    async def test_execute_sync_module(self):
        """Test executing a sync module."""
        def sync_module(msg: str) -> dict:
            return {"changed": False, "msg": msg}

        result = await _execute_ftl_module(sync_module, {"msg": "test"})

        assert result["msg"] == "test"

    @pytest.mark.asyncio
    async def test_execute_async_module(self):
        """Test executing an async module."""
        async def async_module(msg: str) -> dict:
            return {"changed": True, "msg": msg}

        result = await _execute_ftl_module(async_module, {"msg": "async_test"})

        assert result["msg"] == "async_test"
        assert result["changed"] is True


class TestRemoteExecution:
    """Tests for remote execution path."""

    @pytest.mark.asyncio
    async def test_remote_execution_path(self):
        """Test that remote hosts use remote execution."""
        # Create a mock remote host
        mock_host = MagicMock()
        mock_host.name = "remote-host"
        mock_host.is_local = False

        # Mock the remote execution
        with patch("ftl2.ftl_modules.executor._execute_remote") as mock_remote:
            mock_remote.return_value = {"changed": True, "msg": "remote ok"}

            result = await execute("command", {"cmd": "ls"}, host=mock_host)

            mock_remote.assert_called_once()
            assert result.host == "remote-host"


class TestPathSelection:
    """Tests for execution path selection logic."""

    @pytest.mark.asyncio
    async def test_ftl_module_uses_fast_path(self):
        """Test that FTL modules use fast path."""
        # 'command' has an FTL implementation
        result = await execute("command", {"cmd": "echo fast"})

        assert result.used_ftl is True

    @pytest.mark.asyncio
    async def test_unknown_module_uses_fallback(self):
        """Test that unknown modules use fallback path."""
        result = await execute("completely_unknown_module", {})

        assert result.used_ftl is False
        assert result.success is False


class TestIntegration:
    """Integration tests for the executor."""

    @pytest.mark.asyncio
    async def test_file_operations_workflow(self):
        """Test a typical file operations workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create directory
            dir_result = await run("file", path=str(base / "mydir"), state="directory")
            assert dir_result.success
            assert dir_result.changed

            # Create file
            file_result = await run("file", path=str(base / "mydir" / "test.txt"), state="touch")
            assert file_result.success

            # Copy file
            copy_result = await run(
                "copy",
                src=str(base / "mydir" / "test.txt"),
                dest=str(base / "mydir" / "test2.txt"),
            )
            assert copy_result.success
            assert (base / "mydir" / "test2.txt").exists()

            # Delete directory
            del_result = await run("file", path=str(base / "mydir"), state="absent")
            assert del_result.success
            assert not (base / "mydir").exists()

    @pytest.mark.asyncio
    async def test_parallel_commands(self):
        """Test running commands in parallel."""
        hosts = [LocalHost(name=f"worker{i}") for i in range(10)]

        results = await execute_on_hosts(
            hosts,
            "command",
            {"cmd": "echo worker"},
        )

        # All should succeed
        assert all(r.success for r in results)
        assert len(results) == 10
