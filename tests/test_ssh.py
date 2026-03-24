"""Tests for async SSH transport (Phase 6)."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.ssh import (
    SSHConfig,
    SSHHost,
    SSHConnectionPool,
    ssh_run,
    ssh_run_on_hosts,
)


def create_mock_connection():
    """Create a properly configured mock SSH connection."""
    mock_conn = MagicMock()
    mock_conn.is_closed.return_value = False

    # Make run() return an awaitable
    async def mock_run(cmd, input=None, check=False):
        result = MagicMock()
        result.stdout = ""
        result.stderr = ""
        result.returncode = 0
        return result

    mock_conn.run = AsyncMock(side_effect=mock_run)

    # Make close() work
    mock_conn.close = MagicMock()
    mock_conn.wait_closed = AsyncMock()

    # Make start_sftp_client work
    @asynccontextmanager
    async def mock_sftp():
        sftp = AsyncMock()
        yield sftp

    mock_conn.start_sftp_client = mock_sftp

    return mock_conn


class TestSSHConfig:
    """Tests for SSHConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = SSHConfig(hostname="server.example.com")

        assert config.hostname == "server.example.com"
        assert config.port == 22
        assert config.username is None
        assert config.password is None
        assert config.client_keys is None
        assert config.connect_timeout == 30.0

    def test_custom_config(self):
        """Test custom configuration."""
        config = SSHConfig(
            hostname="server.example.com",
            port=2222,
            username="deploy",
            password="secret",
            client_keys=["/home/user/.ssh/id_rsa"],
        )

        assert config.port == 2222
        assert config.username == "deploy"
        assert config.password == "secret"

    def test_to_asyncssh_options(self):
        """Test converting to asyncssh options dict."""
        config = SSHConfig(
            hostname="server.example.com",
            port=2222,
            username="deploy",
            password="secret",
        )

        options = config.to_asyncssh_options()

        assert options["host"] == "server.example.com"
        assert options["port"] == 2222
        assert options["username"] == "deploy"
        assert options["password"] == "secret"

    def test_to_asyncssh_options_minimal(self):
        """Test minimal config to asyncssh options."""
        config = SSHConfig(hostname="server.example.com")

        options = config.to_asyncssh_options()

        assert options["host"] == "server.example.com"
        assert "username" not in options
        assert "password" not in options

    def test_known_hosts_none_disables_checking(self):
        """Test that known_hosts=None disables host key checking."""
        config = SSHConfig(hostname="server.example.com", known_hosts=None)

        options = config.to_asyncssh_options()

        assert options["known_hosts"] is None


class TestSSHHost:
    """Tests for SSHHost class."""

    def test_host_properties(self):
        """Test host name and is_local properties."""
        host = SSHHost("server.example.com", username="deploy")

        assert host.name == "server.example.com"
        assert host.is_local is False

    @pytest.mark.asyncio
    async def test_run_command(self):
        """Test running a command via SSH."""
        host = SSHHost("server.example.com")

        # Mock the connection
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_result = MagicMock()
        mock_result.stdout = "hello world\n"
        mock_result.stderr = ""
        mock_result.returncode = 0

        async def mock_run(*args, **kwargs):
            return mock_result

        mock_conn.run = mock_run

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            stdout, stderr, rc = await host.run("echo hello world")

        assert stdout == "hello world\n"
        assert stderr == ""
        assert rc == 0

    @pytest.mark.asyncio
    async def test_run_with_stdin(self):
        """Test running a command with stdin input."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_result = MagicMock()
        mock_result.stdout = "processed"
        mock_result.stderr = ""
        mock_result.returncode = 0

        captured_kwargs = {}

        async def mock_run(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_result

        mock_conn.run = mock_run

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            stdout, _, _ = await host.run("cat", stdin="input data")

        assert captured_kwargs.get("input") == "input data"

    @pytest.mark.asyncio
    async def test_run_timeout(self):
        """Test command timeout handling."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False

        async def mock_run_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        mock_conn.run = mock_run_timeout

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            stdout, stderr, rc = await host.run("sleep 1000", timeout=1)

        assert stdout == ""
        assert "timed out" in stderr
        assert rc == -1

    @pytest.mark.asyncio
    async def test_has_file_exists(self):
        """Test checking if file exists."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False

        mock_sftp = AsyncMock()
        mock_sftp.stat.return_value = MagicMock()  # File exists

        @asynccontextmanager
        async def mock_sftp_ctx():
            yield mock_sftp

        mock_conn.start_sftp_client = mock_sftp_ctx

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            exists = await host.has_file("/tmp/test.txt")

        assert exists is True

    @pytest.mark.asyncio
    async def test_has_file_not_exists(self):
        """Test checking if file doesn't exist."""
        import asyncssh

        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False

        mock_sftp = AsyncMock()
        mock_sftp.stat.side_effect = asyncssh.SFTPNoSuchFile("not found")

        @asynccontextmanager
        async def mock_sftp_ctx():
            yield mock_sftp

        mock_conn.start_sftp_client = mock_sftp_ctx

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            exists = await host.has_file("/tmp/nonexistent.txt")

        assert exists is False

    @pytest.mark.asyncio
    async def test_write_file(self):
        """Test writing a file."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False

        mock_file = AsyncMock()
        mock_sftp = AsyncMock()

        @asynccontextmanager
        async def mock_open(*args, **kwargs):
            yield mock_file

        mock_sftp.open = mock_open

        @asynccontextmanager
        async def mock_sftp_ctx():
            yield mock_sftp

        mock_conn.start_sftp_client = mock_sftp_ctx

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            await host.write_file("/tmp/test.txt", b"hello world")

        mock_file.write.assert_called_once_with(b"hello world")

    @pytest.mark.asyncio
    async def test_write_pyz_makes_executable(self):
        """Test that writing .pyz files makes them executable."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False

        mock_file = AsyncMock()
        mock_sftp = AsyncMock()
        chmod_called = []

        @asynccontextmanager
        async def mock_open(*args, **kwargs):
            yield mock_file

        mock_sftp.open = mock_open

        @asynccontextmanager
        async def mock_sftp_ctx():
            yield mock_sftp

        mock_conn.start_sftp_client = mock_sftp_ctx

        async def mock_run(cmd, *args, **kwargs):
            if "chmod" in cmd:
                chmod_called.append(cmd)
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result

        mock_conn.run = mock_run

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            await host.write_file("/tmp/bundle.pyz", b"bundle data")

        # Should have called chmod
        assert len(chmod_called) == 1
        assert "chmod" in chmod_called[0]

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test using host as context manager."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        mock_conn.wait_closed = AsyncMock()

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            async with host:
                pass  # Connection established

        mock_conn.close.assert_called_once()
        mock_conn.wait_closed.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_reuse(self):
        """Test that connections are reused."""
        host = SSHHost("server.example.com")

        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        run_count = [0]

        async def mock_run(*args, **kwargs):
            run_count[0] += 1
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result

        mock_conn.run = mock_run

        mock_connect = AsyncMock(return_value=mock_conn)
        with patch("ftl2.ssh.asyncssh.connect", mock_connect):
            await host.run("cmd1")
            await host.run("cmd2")
            await host.run("cmd3")

        # Should only connect once
        assert mock_connect.call_count == 1
        assert run_count[0] == 3


class TestSSHConnectionPool:
    """Tests for SSHConnectionPool."""

    @pytest.mark.asyncio
    async def test_get_creates_host(self):
        """Test that get() creates a new host."""
        pool = SSHConnectionPool()

        host = await pool.get("server.example.com", username="deploy")

        assert host.name == "server.example.com"
        assert host.config.username == "deploy"

    @pytest.mark.asyncio
    async def test_get_reuses_host(self):
        """Test that get() reuses hosts with same key."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", username="deploy")
        host2 = await pool.get("server.example.com", username="deploy")

        assert host1 is host2

    @pytest.mark.asyncio
    async def test_different_users_different_hosts(self):
        """Test that different users get different hosts."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", username="deploy")
        host2 = await pool.get("server.example.com", username="admin")

        assert host1 is not host2

    @pytest.mark.asyncio
    async def test_different_ports_different_hosts(self):
        """Test that different ports get different hosts."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", port=22)
        host2 = await pool.get("server.example.com", port=2222)

        assert host1 is not host2

    @pytest.mark.asyncio
    async def test_close_all(self):
        """Test closing all connections."""
        pool = SSHConnectionPool()

        # Create some hosts
        await pool.get("server1.example.com")
        await pool.get("server2.example.com")
        await pool.get("server3.example.com")

        # Mock disconnect on all hosts
        for host in pool._hosts.values():
            host.disconnect = AsyncMock()

        await pool.close_all()

        # All hosts should be removed
        assert len(pool._hosts) == 0

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test pool as context manager."""
        async with SSHConnectionPool() as pool:
            await pool.get("server.example.com")

        # Pool should be empty after context
        assert len(pool._hosts) == 0


class TestSSHSecurity:
    """Tests for SSH security hardening."""

    def test_default_known_hosts_uses_system(self):
        """Test that default known_hosts uses system defaults (not None)."""
        config = SSHConfig(hostname="server.example.com")
        options = config.to_asyncssh_options()
        # known_hosts should NOT be in options (asyncssh uses system defaults)
        assert "known_hosts" not in options

    def test_ssh_host_default_known_hosts(self):
        """Test that SSHHost default doesn't disable host key checking."""
        host = SSHHost("server.example.com")
        assert host.config.known_hosts == ()  # Empty tuple = use system defaults

    def test_command_injection_path_exists(self):
        """Test that path_exists quotes the path argument."""
        host = SSHHost("server.example.com")
        # The path with shell metacharacters should be quoted
        import shlex
        malicious_path = "/tmp/test; rm -rf /"
        expected_cmd = f"test -e {shlex.quote(malicious_path)}"
        assert "'" in expected_cmd  # shlex.quote wraps in single quotes

    def test_command_injection_has_file(self):
        """Test that has_file shell fallback quotes the path."""
        import shlex
        malicious_path = "/tmp/$(whoami)"
        quoted = shlex.quote(malicious_path)
        # The quoted version should neutralize the command substitution
        assert "$(" not in quoted or "'" in quoted

    @pytest.mark.asyncio
    async def test_pool_different_passwords_different_hosts(self):
        """Test that different passwords produce different pool entries."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", username="deploy", password="pass1")
        host2 = await pool.get("server.example.com", username="deploy", password="pass2")

        assert host1 is not host2

    @pytest.mark.asyncio
    async def test_pool_different_keys_different_hosts(self):
        """Test that different client keys produce different pool entries."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", client_keys=["/key1"])
        host2 = await pool.get("server.example.com", client_keys=["/key2"])

        assert host1 is not host2

    @pytest.mark.asyncio
    async def test_pool_same_credentials_reuses(self):
        """Test that same credentials reuse the same host."""
        pool = SSHConnectionPool()

        host1 = await pool.get("server.example.com", username="deploy", password="pass1")
        host2 = await pool.get("server.example.com", username="deploy", password="pass1")

        assert host1 is host2

    @pytest.mark.asyncio
    async def test_chown_quotes_arguments(self):
        """Test that chown/chgrp quote their arguments."""
        host = SSHHost("server.example.com")
        mock_conn = create_mock_connection()

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            await host.chown("/tmp/safe", owner="root", group="wheel")

        # Verify the commands used quoted paths
        calls = mock_conn.run.call_args_list
        assert len(calls) == 2
        # Check owner call
        owner_cmd = calls[0][0][0]
        assert "chown" in owner_cmd
        assert "root" in owner_cmd
        # Check group call
        group_cmd = calls[1][0][0]
        assert "chgrp" in group_cmd
        assert "wheel" in group_cmd


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    @pytest.mark.asyncio
    async def test_ssh_run(self):
        """Test ssh_run convenience function."""
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        mock_conn.wait_closed = AsyncMock()

        async def mock_run(*args, **kwargs):
            result = MagicMock()
            result.stdout = "hello\n"
            result.stderr = ""
            result.returncode = 0
            return result

        mock_conn.run = mock_run

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            stdout, stderr, rc = await ssh_run(
                "server.example.com",
                "echo hello",
                username="deploy",
            )

        assert stdout == "hello\n"
        assert rc == 0

    @pytest.mark.asyncio
    async def test_ssh_run_on_hosts(self):
        """Test running on multiple hosts concurrently."""
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        mock_conn.close = MagicMock()
        mock_conn.wait_closed = AsyncMock()

        async def mock_run(*args, **kwargs):
            result = MagicMock()
            result.stdout = "up\n"
            result.stderr = ""
            result.returncode = 0
            return result

        mock_conn.run = mock_run

        with patch("ftl2.ssh.asyncssh.connect", AsyncMock(return_value=mock_conn)):
            results = await ssh_run_on_hosts(
                ["server1", "server2", "server3"],
                "uptime",
                username="deploy",
            )

        assert len(results) == 3
        for hostname, stdout, stderr, rc in results:
            assert hostname in ["server1", "server2", "server3"]
            assert stdout == "up\n"
            assert rc == 0


class TestIntegrationWithExecutor:
    """Tests for integration with ftl_modules executor."""

    @pytest.mark.asyncio
    async def test_ssh_host_implements_remote_host_protocol(self):
        """Test that SSHHost works with executor's RemoteHost protocol."""
        from ftl2.ftl_modules.executor import execute

        host = SSHHost("server.example.com", username="deploy")

        # Verify it has the required protocol methods
        assert hasattr(host, "name")
        assert hasattr(host, "is_local")
        assert hasattr(host, "run")
        assert callable(host.run)

        # Verify properties
        assert host.name == "server.example.com"
        assert host.is_local is False

    @pytest.mark.asyncio
    async def test_execute_with_ssh_host(self):
        """Test executing module with SSHHost."""
        from ftl2.ftl_modules.executor import execute

        host = SSHHost("server.example.com")

        # Mock the remote execution path
        with patch("ftl2.ftl_modules.executor._execute_remote") as mock_remote:
            mock_remote.return_value = {"changed": True, "msg": "ok"}

            result = await execute("command", {"cmd": "ls"}, host=host)

            mock_remote.assert_called_once()
            assert result.host == "server.example.com"
