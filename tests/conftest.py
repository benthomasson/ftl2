"""Pytest configuration and shared fixtures."""

import os
import subprocess
import time

import pytest

from ftl2.types import HostConfig


@pytest.fixture
def sample_fixture():
    """Example fixture for testing."""
    return {"example": "data"}


# SSH Integration Test Infrastructure
# Check if SSH integration tests are enabled
SSH_INTEGRATION_ENABLED = os.getenv("SSH_INTEGRATION_TESTS", "false").lower() == "true"


def is_docker_available() -> bool:
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def is_ssh_container_running() -> bool:
    """Check if SSH test container is running."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=ftl2-ssh-test", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "ftl2-ssh-test" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def ssh_test_server():
    """Start SSH test server container.

    Starts the Docker container with SSH server for testing.
    The container is started once per test session and cleaned up at the end.

    Yields:
        bool: True if container is running

    Example:
        @pytest.mark.ssh_integration
        async def test_remote_execution(ssh_test_server, ssh_test_host):
            assert ssh_test_server
            # Test remote execution
    """
    if not SSH_INTEGRATION_ENABLED:
        pytest.skip("SSH integration tests disabled (set SSH_INTEGRATION_TESTS=true)")

    if not is_docker_available():
        pytest.skip("Docker not available")

    # Check if container already running
    already_running = is_ssh_container_running()

    if not already_running:
        # Start container
        print("\nStarting SSH test container...")
        subprocess.run(
            ["docker", "compose", "-f", "docker-compose.test.yml", "up", "-d"],
            check=True,
        )

        # Wait for container to be healthy
        print("Waiting for SSH server to be ready...")
        max_attempts = 30
        for _attempt in range(max_attempts):
            if is_ssh_container_running():
                # Additional wait for SSH to fully start
                time.sleep(2)
                print("SSH test container ready!")
                break
            time.sleep(1)
        else:
            raise RuntimeError("SSH test container failed to start")

    yield True

    # Cleanup (only if we started it)
    if not already_running:
        print("\nStopping SSH test container...")
        subprocess.run(
            ["docker", "compose", "-f", "docker-compose.test.yml", "down"],
            check=False,  # Don't fail if already stopped
        )


@pytest.fixture
def ssh_test_host(ssh_test_server) -> HostConfig:
    """Create host configuration for SSH test container.

    Provides a HostConfig pointing to the Docker SSH test server.

    Args:
        ssh_test_server: Fixture ensuring container is running

    Returns:
        HostConfig configured for SSH test container

    Example:
        async def test_remote(ssh_test_host):
            runner = RemoteModuleRunner()
            result = await runner.run(ssh_test_host, context)
    """
    return HostConfig(
        name="ssh-test-server",
        ansible_host="127.0.0.1",
        ansible_port=2222,
        ansible_user="testuser",
        ansible_connection="ssh",
        ansible_python_interpreter="/usr/bin/python3",
        vars={"ansible_password": "testpass"},
    )


@pytest.fixture
def ssh_test_inventory(ssh_test_host):
    """Create inventory with SSH test host.

    Returns:
        Inventory containing the SSH test host
    """
    from ftl2.inventory import HostGroup, Inventory

    inventory = Inventory()
    group = HostGroup(name="test_hosts")
    group.add_host(ssh_test_host)
    inventory.add_group(group)

    return inventory


# Pytest markers for SSH tests
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "ssh_integration: mark test as SSH integration test (requires Docker)"
    )
