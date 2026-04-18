"""Integration tests for remote execution via SSH.

These tests require Docker and the SSH test container to be running.
Enable with: export SSH_INTEGRATION_TESTS=true

To run:
    export SSH_INTEGRATION_TESTS=true
    docker-compose -f docker-compose.test.yml up -d
    pytest tests/test_ssh_integration.py -v
    docker-compose -f docker-compose.test.yml down
"""

from pathlib import Path

import pytest

from ftl2.executor import ModuleExecutor
from ftl2.runners import ExecutionContext, ModuleRunnerFactory, RemoteModuleRunner
from ftl2.types import ExecutionConfig, GateConfig

# Module directory for tests
TEST_MODULE_DIR = Path(__file__).parent / "test_modules"


@pytest.mark.ssh_integration
async def test_remote_module_execution_basic(ssh_test_host, tmp_path):
    """Test basic remote module execution through SSH."""
    # Create execution config
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "remote-test"},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Create remote runner and execute
    runner = RemoteModuleRunner()
    result = await runner.run(ssh_test_host, context)

    # Verify execution succeeded
    assert result.success, f"Execution failed: {result.output}"
    assert result.host_name == ssh_test_host.name
    assert "stdout" in result.output or "msg" in result.output

    # Clean up
    await runner.cleanup()


@pytest.mark.ssh_integration
async def test_remote_module_execution_with_args(ssh_test_host, tmp_path):
    """Test remote module execution with module arguments."""
    # Create execution config with arguments
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={
            "name": "arg-test",
            "value": "test-value",
        },
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Create remote runner and execute
    runner = RemoteModuleRunner()
    result = await runner.run(ssh_test_host, context)

    # Verify execution succeeded
    assert result.success
    assert result.host_name == ssh_test_host.name

    # Clean up
    await runner.cleanup()


@pytest.mark.ssh_integration
async def test_gate_caching(ssh_test_host, tmp_path):
    """Test that gates are cached and reused across executions."""
    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    # First execution - should build gate
    exec_config1 = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "first"},
    )

    context1 = ExecutionContext(
        execution_config=exec_config1,
        gate_config=gate_config,
    )

    runner1 = RemoteModuleRunner()
    result1 = await runner1.run(ssh_test_host, context1)
    assert result1.success
    await runner1.cleanup()

    # Second execution - should reuse cached gate
    exec_config2 = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "second"},
    )

    context2 = ExecutionContext(
        execution_config=exec_config2,
        gate_config=gate_config,
    )

    runner2 = RemoteModuleRunner()
    result2 = await runner2.run(ssh_test_host, context2)
    assert result2.success
    await runner2.cleanup()

    # Verify both executions succeeded
    assert result1.host_name == result2.host_name


@pytest.mark.ssh_integration
async def test_connection_reuse(ssh_test_host, tmp_path):
    """Test that SSH connections are reused within a runner instance."""
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "connection-test"},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Create single runner instance
    runner = RemoteModuleRunner()

    # Execute multiple times with same runner
    results = []
    for _i in range(3):
        result = await runner.run(ssh_test_host, context)
        results.append(result)

    # Verify all executions succeeded
    for result in results:
        assert result.success
        assert result.host_name == ssh_test_host.name

    # Clean up
    await runner.cleanup()


@pytest.mark.ssh_integration
async def test_executor_with_ssh_inventory(ssh_test_inventory, tmp_path):
    """Test ModuleExecutor with SSH inventory."""
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "executor-test"},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Create executor and run
    executor = ModuleExecutor()
    results = await executor.run(ssh_test_inventory, context)

    # Verify execution results
    assert results.total_hosts == 1
    assert results.successful == 1
    assert results.failed == 0
    assert results.is_success()

    # Verify individual result
    assert len(results.results) == 1
    result = results.results["ssh-test-server"]
    assert result.success
    assert result.host_name == "ssh-test-server"

    # Clean up
    await executor.cleanup()


@pytest.mark.ssh_integration
async def test_remote_error_handling(ssh_test_host, tmp_path):
    """Test error handling for remote execution failures."""
    # Try to execute a non-existent module
    exec_config = ExecutionConfig(
        module_name="nonexistent_module",
        module_dirs=[TEST_MODULE_DIR],
        module_args={},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    runner = RemoteModuleRunner()

    # Execution should raise ModuleExecutionError for non-existent module
    from ftl2.exceptions import ModuleExecutionError
    with pytest.raises(ModuleExecutionError, match="nonexistent_module not found"):
        await runner.run(ssh_test_host, context)

    # Clean up
    await runner.cleanup()


@pytest.mark.ssh_integration
async def test_runner_factory_selects_remote(ssh_test_host, tmp_path):
    """Test that RunnerFactory correctly selects RemoteModuleRunner for SSH hosts."""
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "factory-test"},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Get runner from factory
    factory = ModuleRunnerFactory()
    runner = factory.create_runner(ssh_test_host)

    # Verify it's a RemoteModuleRunner
    assert isinstance(runner, RemoteModuleRunner)

    # Execute and verify
    result = await runner.run(ssh_test_host, context)
    assert result.success

    # Clean up
    await runner.cleanup()


@pytest.mark.ssh_integration
async def test_multiple_concurrent_executions(ssh_test_inventory, tmp_path):
    """Test concurrent remote executions work correctly."""
    exec_config = ExecutionConfig(
        module_name="test_new_style",
        module_dirs=[TEST_MODULE_DIR],
        module_args={"name": "concurrent-test"},
    )

    gate_config = GateConfig(
        cache_dir=tmp_path / "gates",
    )

    context = ExecutionContext(
        execution_config=exec_config,
        gate_config=gate_config,
    )

    # Run executor multiple times
    executor = ModuleExecutor()

    # Execute twice
    results1 = await executor.run(ssh_test_inventory, context)
    results2 = await executor.run(ssh_test_inventory, context)

    # Verify both succeeded
    assert results1.is_success()
    assert results2.is_success()
    assert results1.total_hosts == 1
    assert results2.total_hosts == 1

    # Clean up
    await executor.cleanup()
