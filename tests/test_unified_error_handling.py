"""Tests for unified runner error handling (Issue #41).

Validates that both LocalModuleRunner and RemoteModuleRunner follow the
errors-as-data contract: run() always returns ModuleResult, never raises.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.exceptions import (
    AuthenticationError,
    ConnectionError,
    ErrorContext,
    ErrorTypes,
    GateError,
    ModuleExecutionError,
)
from ftl2.executor import ModuleExecutor
from ftl2.runners import (
    ExecutionContext,
    LocalModuleRunner,
    RemoteModuleRunner,
)
from ftl2.types import ExecutionConfig, GateConfig, HostConfig, ModuleResult

# --- Fixtures ---


@pytest.fixture
def localhost():
    return HostConfig(
        name="localhost",
        ansible_host="127.0.0.1",
        ansible_connection="local",
    )


@pytest.fixture
def remote_host():
    return HostConfig(name="web01", ansible_host="192.168.1.10")


@pytest.fixture
def test_modules_dir():
    return Path(__file__).parent / "test_modules"


def make_context(module_name="ping", module_dirs=None, module_args=None):
    return ExecutionContext(
        execution_config=ExecutionConfig(
            module_name=module_name,
            module_dirs=module_dirs or [],
            module_args=module_args or {},
        ),
        gate_config=GateConfig(),
    )


def _patch_find_module():
    """Patch find_module to return a fake path so we reach _get_or_create_gate."""
    return patch("ftl2.runners.find_module", return_value=Path("/fake/module.py"))


# --- 1. Base contract: both runners return ModuleResult, never raise ---


class TestLocalRunnerErrorsAsData:
    """LocalModuleRunner always returns ModuleResult."""

    async def test_module_not_found_returns_result(self, localhost):
        """Module not found returns error result, not exception."""
        runner = LocalModuleRunner()
        context = make_context("nonexistent_module", [Path("/tmp/no_such_dir")])

        result = await runner.run(localhost, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert "not found" in result.error.lower()
        assert result.host_name == "localhost"

    async def test_execution_error_returns_result(self, localhost, test_modules_dir):
        """Execution errors (e.g., bad module output) return error result."""
        runner = LocalModuleRunner()
        context = make_context("test_binary.sh", [test_modules_dir])

        with patch.object(runner, "_run_binary_module", side_effect=RuntimeError("boom")):
            result = await runner.run(localhost, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert "boom" in result.error

    async def test_successful_execution_returns_result(self, localhost, test_modules_dir):
        """Successful execution returns success result."""
        runner = LocalModuleRunner()
        context = make_context("test_binary.sh", [test_modules_dir])

        result = await runner.run(localhost, context)

        assert isinstance(result, ModuleResult)
        assert result.is_success
        assert result.host_name == "localhost"


class TestRemoteRunnerErrorsAsData:
    """RemoteModuleRunner always returns ModuleResult, never raises."""

    async def test_ftl2error_returns_result_with_context(self, remote_host):
        """FTL2Error is caught and converted to ModuleResult with error_context."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = ConnectionError(
            "SSH connection timed out",
            host="web01",
            error_type=ErrorTypes.CONNECTION_TIMEOUT,
        )

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert "timed out" in result.error.lower()
        assert result.error_context is not None
        assert result.error_context.error_type == ErrorTypes.CONNECTION_TIMEOUT

    async def test_auth_error_returns_result_with_context(self, remote_host):
        """AuthenticationError is caught and returns result with context."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = AuthenticationError(
            "Permission denied",
            host="web01",
            host_address="192.168.1.10",
            user="deploy",
        )

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert result.error_context is not None
        assert result.error_context.error_type == ErrorTypes.AUTHENTICATION_FAILED

    async def test_module_execution_error_returns_result(self, remote_host):
        """ModuleExecutionError is caught and returns result with context."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = ModuleExecutionError(
            "Module failed with exit code 1",
            host="web01",
            module="ping",
            exit_code=1,
        )

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert result.error_context is not None
        assert result.error_context.error_type == ErrorTypes.MODULE_EXECUTION_ERROR
        assert result.error_context.exit_code == 1

    async def test_gate_error_returns_result(self, remote_host):
        """GateError is caught and returns result with context."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = GateError("Gate process died", host="web01")

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert result.error_context.error_type == ErrorTypes.GATE_ERROR

    async def test_generic_exception_returns_result(self, remote_host):
        """Unexpected exceptions are caught and return error result."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=RuntimeError("unexpected")):
                result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert "unexpected" in result.error.lower()
        # Generic exceptions don't carry error_context
        assert result.error_context is None

    async def test_module_not_found_returns_result(self, remote_host):
        """Module not found in remote runner returns result, not exception."""
        runner = RemoteModuleRunner()
        context = make_context("nonexistent_module", [Path("/tmp/no_such_dir")])

        result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert "not found" in result.error.lower()


# --- 2. Error context preservation ---


class TestErrorContextPreservation:
    """FTL2Error.context is forwarded to ModuleResult.error_context."""

    async def test_connection_error_preserves_suggestions(self, remote_host):
        """Connection error suggestions are preserved in result."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = ConnectionError(
            "Connection refused",
            host="web01",
            host_address="192.168.1.10",
            port=22,
            error_type=ErrorTypes.CONNECTION_REFUSED,
        )

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        assert result.error_context.suggestions  # non-empty list
        assert result.error_context.debug_command  # non-empty string

    async def test_auth_error_preserves_user_info(self, remote_host):
        """Auth error preserves user/host information in context."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        exc = AuthenticationError(
            "Key rejected",
            host="web01",
            host_address="192.168.1.10",
            port=2222,
            user="deploy",
            key_file="/home/deploy/.ssh/id_ed25519",
        )

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", side_effect=exc):
                result = await runner.run(remote_host, context)

        ctx = result.error_context
        assert ctx.host == "web01"
        assert ctx.user == "deploy"
        assert "192.168.1.10:2222" in ctx.host_address


# --- 3. Executor handles uniform results ---


class TestExecutorUniformHandling:
    """_execute_chunk() handles results uniformly since runners don't raise."""

    async def test_mixed_local_remote_errors_all_return_results(self, test_modules_dir):
        """Executor handles mix of local and remote failures uniformly."""
        from ftl2.inventory import HostGroup, Inventory

        inventory = Inventory()
        group = HostGroup(name="mixed")
        local = HostConfig(name="local1", ansible_host="127.0.0.1", ansible_connection="local")
        remote = HostConfig(name="remote1", ansible_host="10.0.0.1")
        group.add_host(local)
        group.add_host(remote)
        inventory.add_group(group)

        context = make_context("nonexistent_module", [Path("/tmp/no_such_dir")])

        mock_remote_result = ModuleResult.error_result(
            host_name="remote1",
            error="Connection refused",
            error_context=ErrorContext(error_type=ErrorTypes.CONNECTION_REFUSED),
        )

        executor = ModuleExecutor()
        original_create = executor.runner_factory.create_runner

        def patched_create(host):
            if host.is_local:
                return original_create(host)
            mock_runner = AsyncMock()
            mock_runner.run.return_value = mock_remote_result
            return mock_runner

        executor.runner_factory.create_runner = patched_create

        results = await executor.run(inventory, context)

        assert results.total_hosts == 2
        assert results.failed == 2
        for _name, result in results.results.items():
            assert isinstance(result, ModuleResult)
            assert result.is_failure

    async def test_executor_safety_net_for_leaked_exception(self, test_modules_dir):
        """If a runner violates contract and raises, executor still returns ModuleResult."""
        from ftl2.inventory import load_localhost

        inventory = load_localhost()
        context = make_context("test_binary.sh", [test_modules_dir])

        executor = ModuleExecutor()

        mock_runner = AsyncMock()
        mock_runner.run.side_effect = RuntimeError("contract violation")

        executor.runner_factory.create_runner = lambda host: mock_runner

        results = await executor.run(inventory, context)

        assert results.total_hosts == 1
        assert results.failed == 1
        result = results.results["localhost"]
        assert isinstance(result, ModuleResult)
        assert "contract violation" in result.error


# --- 4. Symmetry tests: both runners behave the same for equivalent errors ---


class TestRunnerSymmetry:
    """Both runners produce equivalent ModuleResult for similar error conditions."""

    async def test_module_not_found_symmetry(self, localhost, remote_host):
        """Both runners return similar error results for module-not-found."""
        local_runner = LocalModuleRunner()
        remote_runner = RemoteModuleRunner()
        context = make_context("does_not_exist", [Path("/tmp/no_such_dir")])

        local_result = await local_runner.run(localhost, context)
        remote_result = await remote_runner.run(remote_host, context)

        assert local_result.is_failure
        assert remote_result.is_failure
        assert "not found" in local_result.error.lower()
        assert "not found" in remote_result.error.lower()
        assert local_result.success is False
        assert remote_result.success is False

    async def test_error_result_structure_consistency(self, localhost, remote_host):
        """Error results from both runners have consistent structure."""
        local_runner = LocalModuleRunner()
        remote_runner = RemoteModuleRunner()

        # Local: module not found error
        context = make_context("nonexistent", [Path("/tmp/nope")])
        local_result = await local_runner.run(localhost, context)

        # Remote: connection error (patching find_module so we reach _get_or_create_gate)
        exc = ConnectionError("fail", host="web01")
        with _patch_find_module():
            with patch.object(remote_runner, "_get_or_create_gate", side_effect=exc):
                context2 = make_context("ping")
                remote_result = await remote_runner.run(remote_host, context2)

        for result in [local_result, remote_result]:
            assert hasattr(result, "host_name")
            assert hasattr(result, "success")
            assert hasattr(result, "error")
            assert hasattr(result, "error_context")
            assert result.success is False
            assert result.error is not None
            assert isinstance(result.output, dict)
            assert result.output.get("error") is True


# --- 5. Inner exception re-raise in RemoteModuleRunner ---


class TestRemoteRunnerInnerExceptionHandling:
    """Inner try/except for gate cleanup re-raises to outer handler."""

    async def test_gate_cleanup_error_still_returns_result(self, remote_host):
        """If execution fails, inner handler re-raises and outer catches it."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        mock_gate = MagicMock()
        mock_gate.multiplexed = False

        exc = ModuleExecutionError("execution failed", host="web01")

        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", return_value=mock_gate):
                with patch.object(runner, "_execute_through_gate", side_effect=exc):
                    with patch.object(runner, "_close_gate", new_callable=AsyncMock):
                        result = await runner.run(remote_host, context)

        assert isinstance(result, ModuleResult)
        assert result.is_failure
        assert result.error_context is not None

    async def test_multiplexed_gate_not_closed_on_error(self, remote_host):
        """Multiplexed gate stays open on error (other requests may be in-flight)."""
        runner = RemoteModuleRunner()
        context = make_context("ping")

        mock_gate = MagicMock()
        mock_gate.multiplexed = True

        exc = ModuleExecutionError("execution failed", host="web01")

        close_mock = AsyncMock()
        with _patch_find_module():
            with patch.object(runner, "_get_or_create_gate", return_value=mock_gate):
                with patch.object(runner, "_execute_through_gate", side_effect=exc):
                    with patch.object(runner, "_close_gate", close_mock):
                        result = await runner.run(remote_host, context)

        close_mock.assert_not_called()
        assert result.is_failure


# --- 6. Retry integration: error_context.error_type drives retry decisions ---


class TestRetryWithErrorContext:
    """Retry logic uses error_context.error_type from unified results."""

    async def test_retryable_error_type_from_remote_runner(self, remote_host, test_modules_dir):
        """Remote runner error with retryable error_type triggers retry."""
        from ftl2.inventory import HostGroup, Inventory

        inventory = Inventory()
        group = HostGroup(name="test")
        group.add_host(remote_host)
        inventory.add_group(group)

        context = make_context("test_binary.sh", [test_modules_dir])

        timeout_result = ModuleResult.error_result(
            host_name="web01",
            error="Connection timed out",
            error_context=ErrorContext(error_type=ErrorTypes.CONNECTION_TIMEOUT),
        )
        success_result = ModuleResult.success_result(
            host_name="web01",
            output={"msg": "ok"},
        )

        call_count = 0

        async def mock_run(host, ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return timeout_result
            return success_result

        mock_runner = AsyncMock()
        mock_runner.run = mock_run
        mock_runner.cleanup = AsyncMock()

        from ftl2.executor import RetryConfig

        executor = ModuleExecutor(retry_config=RetryConfig(max_attempts=2, initial_delay=0))
        executor.runner_factory.create_runner = lambda host: mock_runner

        results = await executor.run(inventory, context)

        assert call_count == 2  # retried once
        assert results.successful == 1


# --- 7. Base class docstring contract ---


class TestBaseClassContract:
    """ModuleRunner.run() docstring specifies errors-as-data contract."""

    def test_base_class_docstring_specifies_no_raise(self):
        """Base class docstring says implementations must not raise."""
        from ftl2.runners import ModuleRunner

        docstring = ModuleRunner.run.__doc__
        assert "must not" in docstring.lower()
        assert "raise" in docstring.lower() or "error" in docstring.lower()
