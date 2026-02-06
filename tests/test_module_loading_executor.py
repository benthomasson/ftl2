"""Tests for module loading executor."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.module_loading.executor import (
    ExecutionResult,
    execute_local,
    execute_local_fqcn,
    execute_bundle_local,
    execute_remote,
    execute_remote_with_staging,
    stage_bundle_remote,
    get_module_utils_pythonpath,
    ModuleExecutor,
)
from ftl2.module_loading.bundle import build_bundle, Bundle, BundleInfo


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_success_result(self):
        """Test creating a success result."""
        result = ExecutionResult(
            success=True,
            changed=True,
            output={"msg": "done"},
            return_code=0,
        )
        assert result.success is True
        assert result.changed is True
        assert result.output == {"msg": "done"}
        assert result.error == ""

    def test_failure_result(self):
        """Test creating a failure result."""
        result = ExecutionResult(
            success=False,
            error="Something went wrong",
            return_code=1,
        )
        assert result.success is False
        assert result.error == "Something went wrong"
        assert result.return_code == 1

    def test_from_module_output_success(self):
        """Test creating result from successful module output."""
        stdout = json.dumps({"msg": "success", "changed": True})
        result = ExecutionResult.from_module_output(stdout, "", 0)

        assert result.success is True
        assert result.changed is True
        assert result.output["msg"] == "success"

    def test_from_module_output_failure(self):
        """Test creating result from failed module output."""
        stdout = json.dumps({"failed": True, "msg": "error message"})
        result = ExecutionResult.from_module_output(stdout, "", 0)

        assert result.success is False
        assert result.error == "error message"
        assert result.output["failed"] is True

    def test_from_module_output_nonzero_return(self):
        """Test creating result from non-zero return code."""
        stdout = json.dumps({"msg": "output"})
        result = ExecutionResult.from_module_output(stdout, "stderr msg", 1)

        assert result.success is False
        assert result.return_code == 1

    def test_from_module_output_invalid_json(self):
        """Test creating result from invalid JSON output."""
        result = ExecutionResult.from_module_output("not json", "", 0)

        assert result.success is False
        assert "Invalid JSON" in result.error

    def test_from_module_output_empty_stdout(self):
        """Test creating result from empty stdout."""
        result = ExecutionResult.from_module_output("", "", 0)

        assert result.success is True
        assert result.output == {}

    def test_events_field_default(self):
        """Test that events field defaults to empty list."""
        result = ExecutionResult(success=True)
        assert result.events == []

    def test_from_module_output_with_events(self):
        """Test parsing events from stderr."""
        stdout = '{"changed": true}'
        stderr = '''{"event": "progress", "percent": 0, "message": "Starting"}
{"event": "progress", "percent": 50, "message": "Halfway"}
{"event": "progress", "percent": 100, "message": "Done"}'''

        result = ExecutionResult.from_module_output(stdout, stderr, 0)

        assert result.success is True
        assert result.changed is True
        assert len(result.events) == 3
        assert result.events[0]["event"] == "progress"
        assert result.events[0]["percent"] == 0
        assert result.events[1]["percent"] == 50
        assert result.events[2]["percent"] == 100
        assert result.stderr == ""  # Events removed from stderr

    def test_from_module_output_mixed_stderr(self):
        """Test parsing mixed stderr (events + regular output)."""
        stdout = '{"changed": false}'
        stderr = '''{"event": "log", "level": "info", "message": "Starting task"}
Warning: something happened
{"event": "progress", "percent": 100}
Another warning line'''

        result = ExecutionResult.from_module_output(stdout, stderr, 0)

        assert result.success is True
        assert len(result.events) == 2
        assert result.events[0]["event"] == "log"
        assert result.events[1]["event"] == "progress"
        assert "Warning: something happened" in result.stderr
        assert "Another warning line" in result.stderr
        assert "progress" not in result.stderr

    def test_from_module_output_no_events(self):
        """Test stderr without events is preserved."""
        stdout = '{"changed": true}'
        stderr = "Some warning\nAnother line"

        result = ExecutionResult.from_module_output(stdout, stderr, 0)

        assert result.success is True
        assert result.events == []
        assert result.stderr == "Some warning\nAnother line"

    def test_failure_preserves_events(self):
        """Test that events are preserved even on failure."""
        stdout = '{"failed": true, "msg": "error"}'
        stderr = '''{"event": "progress", "percent": 50}
Error details here'''

        result = ExecutionResult.from_module_output(stdout, stderr, 1)

        assert result.success is False
        assert len(result.events) == 1
        assert result.events[0]["percent"] == 50
        assert "Error details here" in result.stderr


class TestGetModuleUtilsPythonpath:
    """Tests for get_module_utils_pythonpath function."""

    def test_returns_string(self):
        """Test that pythonpath is a string."""
        result = get_module_utils_pythonpath()
        assert isinstance(result, str)

    def test_paths_separated_by_os_pathsep(self):
        """Test paths are separated correctly."""
        import os
        result = get_module_utils_pythonpath()
        # If multiple paths, they should be separated by os.pathsep
        if result:
            # Just verify it's a valid path string
            assert isinstance(result, str)


class TestExecuteLocal:
    """Tests for execute_local function."""

    def test_execute_simple_module(self):
        """Test executing a simple module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "simple_module.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    params = json.load(sys.stdin)
    args = params.get("ANSIBLE_MODULE_ARGS", {})
    result = {"msg": "hello", "changed": False, "args": args}
    print(json.dumps(result))
''')

            result = execute_local(module, {"key": "value"})

            assert result.success is True
            assert result.output["msg"] == "hello"
            assert result.output["args"]["key"] == "value"

    def test_execute_with_check_mode(self):
        """Test executing module with check mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "check_module.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    params = json.load(sys.stdin)
    args = params.get("ANSIBLE_MODULE_ARGS", {})
    check_mode = args.get("_ansible_check_mode", False)
    result = {"check_mode": check_mode, "changed": False}
    print(json.dumps(result))
''')

            result = execute_local(module, {}, check_mode=True)

            assert result.success is True
            assert result.output["check_mode"] is True

    def test_execute_failed_module(self):
        """Test executing a module that reports failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "fail_module.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    result = {"failed": True, "msg": "intentional failure"}
    print(json.dumps(result))
    sys.exit(1)
''')

            result = execute_local(module, {})

            assert result.success is False
            assert "intentional failure" in result.error

    def test_execute_exception_module(self):
        """Test executing a module that raises an exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "exception_module.py"
            module.write_text('''
raise ValueError("Test exception")
''')

            result = execute_local(module, {})

            assert result.success is False
            assert result.return_code != 0

    def test_execute_timeout(self):
        """Test module execution timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "slow_module.py"
            module.write_text('''
import time
time.sleep(10)
''')

            result = execute_local(module, {}, timeout=1)

            assert result.success is False
            assert "timed out" in result.error.lower()

    def test_execute_module_with_events(self):
        """Test executing a module that emits events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "event_module.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    params = json.load(sys.stdin)
    args = params.get("ANSIBLE_MODULE_ARGS", {})

    # Emit progress events to stderr
    print(json.dumps({"event": "progress", "percent": 0, "message": "Starting"}), file=sys.stderr)
    print(json.dumps({"event": "progress", "percent": 50, "message": "Working"}), file=sys.stderr)
    print(json.dumps({"event": "progress", "percent": 100, "message": "Done"}), file=sys.stderr)

    # Final result to stdout
    result = {"changed": True, "msg": "completed with events"}
    print(json.dumps(result))
''')

            result = execute_local(module, {})

            assert result.success is True
            assert result.output["msg"] == "completed with events"
            assert len(result.events) == 3
            assert result.events[0]["percent"] == 0
            assert result.events[1]["percent"] == 50
            assert result.events[2]["percent"] == 100
            assert result.events[2]["message"] == "Done"


class TestExecuteBundleLocal:
    """Tests for execute_bundle_local function."""

    def test_execute_bundle(self):
        """Test executing a bundle locally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "bundled_module.py"
            module.write_text('''
def main(args):
    return {"msg": "from bundle", "changed": False, "args": args}
''')

            bundle = build_bundle(module, dependencies=[])
            result = execute_bundle_local(bundle, {"test": "param"})

            assert result.success is True
            assert result.output["msg"] == "from bundle"
            assert result.output["args"]["test"] == "param"

    def test_execute_bundle_with_work_dir(self):
        """Test executing bundle with custom work directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text('''
def main(args):
    return {"result": "ok", "changed": False}
''')

            bundle = build_bundle(module, dependencies=[])
            work_dir = Path(tmpdir) / "work"
            work_dir.mkdir()

            result = execute_bundle_local(bundle, {}, work_dir=work_dir)

            assert result.success is True
            # Bundle file should exist in work dir
            bundle_files = list(work_dir.glob("*.pyz"))
            assert len(bundle_files) == 1

    def test_execute_bundle_check_mode(self):
        """Test executing bundle with check mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "check_mod.py"
            module.write_text('''
def main(args):
    return {"check_mode": args.get("_ansible_check_mode", False), "changed": False}
''')

            bundle = build_bundle(module, dependencies=[])
            result = execute_bundle_local(bundle, {}, check_mode=True)

            assert result.success is True
            assert result.output["check_mode"] is True


class TestRemoteExecution:
    """Tests for remote execution functions."""

    @pytest.mark.asyncio
    async def test_stage_bundle_new(self):
        """Test staging a bundle on a remote host."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])

            # Mock remote host
            host = AsyncMock()
            host.has_file.return_value = False

            path = await stage_bundle_remote(host, bundle)

            assert bundle.info.content_hash in path
            host.run.assert_called_once()  # mkdir
            host.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_stage_bundle_already_exists(self):
        """Test staging when bundle already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])

            # Mock remote host - bundle exists
            host = AsyncMock()
            host.has_file.return_value = True

            path = await stage_bundle_remote(host, bundle)

            assert bundle.info.content_hash in path
            host.run.assert_not_called()  # No mkdir needed
            host.write_file.assert_not_called()  # No transfer needed

    @pytest.mark.asyncio
    async def test_execute_remote(self):
        """Test executing a bundle on remote host."""
        host = AsyncMock()
        host.run.return_value = (
            json.dumps({"msg": "remote ok", "changed": True}),
            "",
            0,
        )

        result = await execute_remote(
            host,
            "/tmp/ftl2_bundles/abc123.pyz",
            {"key": "value"},
        )

        assert result.success is True
        assert result.output["msg"] == "remote ok"
        host.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_remote_failure(self):
        """Test handling remote execution failure."""
        host = AsyncMock()
        host.run.side_effect = Exception("Connection failed")

        result = await execute_remote(
            host,
            "/tmp/ftl2_bundles/abc123.pyz",
            {},
        )

        assert result.success is False
        assert "Connection failed" in result.error

    @pytest.mark.asyncio
    async def test_execute_remote_with_staging(self):
        """Test combined staging and execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])

            host = AsyncMock()
            host.has_file.return_value = False
            host.run.return_value = (
                json.dumps({"staged_and_run": True, "changed": False}),
                "",
                0,
            )

            result = await execute_remote_with_staging(host, bundle, {})

            assert result.success is True
            assert result.output["staged_and_run"] is True


class TestModuleExecutor:
    """Tests for ModuleExecutor class."""

    def test_executor_local_execution(self):
        """Test executor for local execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a fake collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "mymod.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    params = json.load(sys.stdin)
    args = params.get("ANSIBLE_MODULE_ARGS", {})
    result = {"executor": "local", "changed": False}
    print(json.dumps(result))
''')

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            result = executor.execute_local("test.coll.mymod", {})

            assert result.success is True
            assert result.output["executor"] == "local"

    def test_executor_get_bundle(self):
        """Test getting bundle from executor."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "bundleable.py"
            module.write_text("def main(args): return {}")

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            bundle = executor.get_bundle("test.coll.bundleable")

            assert bundle.info.fqcn == "test.coll.bundleable"
            assert bundle.info.size > 0

    def test_executor_cache_reuse(self):
        """Test that executor reuses cached bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "cached.py"
            module.write_text("def main(args): return {}")

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            bundle1 = executor.get_bundle("test.coll.cached")
            bundle2 = executor.get_bundle("test.coll.cached")

            assert bundle1 is bundle2

    def test_prebuild_bundles(self):
        """Test pre-building multiple bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure with multiple modules
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            for name in ["mod1", "mod2", "mod3"]:
                module = modules_dir / f"{name}.py"
                module.write_text(f"def main(args): return {{'name': '{name}'}}")

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            fqcns = ["test.coll.mod1", "test.coll.mod2", "test.coll.mod3"]
            bundles = executor.prebuild_bundles(fqcns)

            assert len(bundles) == 3
            for fqcn in fqcns:
                assert fqcn in bundles

    def test_prebuild_handles_errors(self):
        """Test pre-building handles missing modules gracefully."""
        executor = ModuleExecutor()

        # Try to build nonexistent module
        bundles = executor.prebuild_bundles(["nonexistent.fake.module"])

        assert len(bundles) == 0

    @pytest.mark.asyncio
    async def test_executor_remote_execution(self):
        """Test executor for remote execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "remote_mod.py"
            module.write_text("def main(args): return {'remote': True}")

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            # Mock remote host
            host = AsyncMock()
            host.has_file.return_value = False
            host.run.return_value = (
                json.dumps({"remote": True, "changed": False}),
                "",
                0,
            )

            result = await executor.execute_remote(host, "test.coll.remote_mod", {})

            assert result.success is True
            assert result.output["remote"] is True

    @pytest.mark.asyncio
    async def test_prestage_bundles(self):
        """Test pre-staging bundles on multiple hosts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "test" / "coll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "stage_mod.py"
            module.write_text("def main(args): return {}")

            executor = ModuleExecutor(
                playbook_dir=Path(tmpdir),
                extra_collection_paths=[Path(tmpdir) / "collections"],
            )

            # Mock multiple hosts
            hosts = [AsyncMock() for _ in range(3)]
            for host in hosts:
                host.has_file.return_value = False

            result = await executor.prestage_bundles(
                hosts,
                ["test.coll.stage_mod"],
            )

            assert "test.coll.stage_mod" in result
            assert len(result["test.coll.stage_mod"]) == 3

            # Each host should have had write_file called
            for host in hosts:
                host.write_file.assert_called_once()


class TestExecuteLocalFqcn:
    """Tests for execute_local_fqcn function."""

    def test_execute_by_fqcn(self):
        """Test executing a module by FQCN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            coll_dir = Path(tmpdir) / "collections" / "ansible_collections" / "myns" / "mycoll"
            modules_dir = coll_dir / "plugins" / "modules"
            modules_dir.mkdir(parents=True)

            module = modules_dir / "mymodule.py"
            module.write_text('''
import sys
import json

if __name__ == "__main__":
    params = json.load(sys.stdin)
    args = params.get("ANSIBLE_MODULE_ARGS", {})
    result = {"fqcn_resolved": True, "changed": False}
    print(json.dumps(result))
''')

            result = execute_local_fqcn(
                "myns.mycoll.mymodule",
                {},
                extra_paths=[Path(tmpdir) / "collections"],
            )

            assert result.success is True
            assert result.output["fqcn_resolved"] is True

    def test_execute_nonexistent_fqcn(self):
        """Test executing a nonexistent FQCN."""
        result = execute_local_fqcn(
            "nonexistent.fake.module",
            {},
        )

        assert result.success is False
        assert "Failed to resolve" in result.error or "not found" in result.error.lower()
