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


# --- Localhost SSH fixtures (Layer 2 — no Docker needed) ---


def _localhost_ssh_available() -> bool:
    """Check if sshd is listening on localhost:22."""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", 22), timeout=2)
        s.close()
        return True
    except OSError:
        return False


LOCALHOST_SSH_AVAILABLE = _localhost_ssh_available()


@pytest.fixture(scope="session")
def require_localhost_ssh():
    """Skip the entire test if localhost SSH is not available.

    Also ensures 127.0.0.1 is in ~/.ssh/known_hosts so that asyncssh
    host key verification succeeds (it looks up by IP, not hostname).
    """
    if not LOCALHOST_SSH_AVAILABLE:
        pytest.skip("sshd not listening on localhost:22")

    # Ensure host key for 127.0.0.1 (port 22) is in known_hosts.
    # Entries for other ports (e.g. [127.0.0.1]:2222) don't count.
    known_hosts = os.path.expanduser("~/.ssh/known_hosts")
    needs_keyscan = True
    if os.path.exists(known_hosts):
        with open(known_hosts) as f:
            for line in f:
                if line.startswith("127.0.0.1 "):
                    needs_keyscan = False
                    break
    if needs_keyscan:
        subprocess.run(
            "ssh-keyscan 127.0.0.1 >> ~/.ssh/known_hosts 2>/dev/null",
            shell=True,
            check=False,
        )


@pytest.fixture
def localhost_ssh_host(require_localhost_ssh) -> HostConfig:
    """HostConfig for the current user on localhost via SSH."""
    import sys

    user = os.getenv("USER") or os.getlogin()
    return HostConfig(
        name="localhost-ssh",
        ansible_host="127.0.0.1",
        ansible_port=22,
        ansible_user=user,
        ansible_connection="ssh",
        ansible_python_interpreter=sys.executable,
    )


@pytest.fixture
def localhost_ssh_inventory(localhost_ssh_host):
    """Inventory containing a single localhost SSH host."""
    from ftl2.inventory import HostGroup, Inventory

    inventory = Inventory()
    group = HostGroup(name="ci_hosts")
    group.add_host(localhost_ssh_host)
    inventory.add_group(group)
    return inventory


# Pytest markers for SSH tests
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "ssh_integration: mark test as SSH integration test (requires Docker)"
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test (requires localhost SSH)"
    )
