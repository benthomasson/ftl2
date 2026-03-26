"""Tests for known_hosts=None security fix (Issue #38).

Validates that:
- known_hosts=None raises ValueError (no silent security bypass)
- disable_host_key_checking=True is the only way to disable verification
- Default behavior is secure (system known_hosts)
- SSHHost and SSHConnectionPool pass through the new flag correctly
- Edge cases around type handling and cache key behavior
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.ssh import SSHConfig, SSHConnectionPool, SSHHost


# =============================================================================
# SSHConfig: known_hosts=None rejection
# =============================================================================


class TestKnownHostsNoneRejection:
    """known_hosts=None must raise ValueError, not silently disable checking."""

    def test_none_raises_valueerror(self):
        """Core fix: known_hosts=None raises ValueError."""
        with pytest.raises(ValueError, match="known_hosts=None is not supported"):
            SSHConfig(hostname="host", known_hosts=None)

    def test_error_message_mentions_flag(self):
        """Error message guides user to the new flag."""
        with pytest.raises(ValueError, match="disable_host_key_checking=True"):
            SSHConfig(hostname="host", known_hosts=None)

    def test_error_message_mentions_empty_tuple(self):
        """Error message mentions () for system defaults."""
        with pytest.raises(ValueError, match=r"known_hosts=\(\)"):
            SSHConfig(hostname="host", known_hosts=None)

    def test_none_with_disable_flag_still_raises(self):
        """Even with disable_host_key_checking=True, known_hosts=None still raises."""
        with pytest.raises(ValueError):
            SSHConfig(hostname="host", known_hosts=None, disable_host_key_checking=True)


# =============================================================================
# SSHConfig: disable_host_key_checking flag
# =============================================================================


class TestDisableHostKeyCheckingFlag:
    """The explicit flag is the only way to disable host key verification."""

    def test_flag_defaults_to_false(self):
        config = SSHConfig(hostname="host")
        assert config.disable_host_key_checking is False

    def test_flag_true_sets_asyncssh_known_hosts_none(self):
        """When flag is True, asyncssh gets known_hosts=None (its disable mechanism)."""
        config = SSHConfig(hostname="host", disable_host_key_checking=True)
        options = config.to_asyncssh_options()
        assert options["known_hosts"] is None

    def test_flag_false_omits_known_hosts(self):
        """When flag is False and known_hosts=(), known_hosts is omitted (system defaults)."""
        config = SSHConfig(hostname="host", disable_host_key_checking=False)
        options = config.to_asyncssh_options()
        assert "known_hosts" not in options

    def test_flag_true_overrides_custom_known_hosts_path(self):
        """disable_host_key_checking=True takes precedence over a custom path."""
        config = SSHConfig(
            hostname="host",
            known_hosts="/etc/ssh/known_hosts",
            disable_host_key_checking=True,
        )
        options = config.to_asyncssh_options()
        assert options["known_hosts"] is None


# =============================================================================
# SSHConfig: known_hosts valid values
# =============================================================================


class TestKnownHostsValidValues:
    """Test the allowed known_hosts values: () and string paths."""

    def test_empty_tuple_default(self):
        config = SSHConfig(hostname="host")
        assert config.known_hosts == ()

    def test_empty_tuple_omits_from_options(self):
        """() means 'use system defaults' — don't pass known_hosts to asyncssh."""
        config = SSHConfig(hostname="host", known_hosts=())
        options = config.to_asyncssh_options()
        assert "known_hosts" not in options

    def test_string_path_passed_through(self):
        """A string path is passed to asyncssh as-is."""
        config = SSHConfig(hostname="host", known_hosts="/custom/known_hosts")
        options = config.to_asyncssh_options()
        assert options["known_hosts"] == "/custom/known_hosts"

    def test_home_dir_path(self):
        """Tilde paths work."""
        config = SSHConfig(hostname="host", known_hosts="~/.ssh/known_hosts")
        options = config.to_asyncssh_options()
        assert options["known_hosts"] == "~/.ssh/known_hosts"


# =============================================================================
# SSHHost: flag passthrough
# =============================================================================


class TestSSHHostFlagPassthrough:
    """SSHHost passes disable_host_key_checking to SSHConfig."""

    def test_default_secure(self):
        host = SSHHost("host")
        assert host.config.disable_host_key_checking is False
        assert host.config.known_hosts == ()

    def test_disable_flag_passed_to_config(self):
        host = SSHHost("host", disable_host_key_checking=True)
        assert host.config.disable_host_key_checking is True

    def test_known_hosts_none_raises_through_host(self):
        """ValueError propagates through SSHHost constructor."""
        with pytest.raises(ValueError, match="known_hosts=None is not supported"):
            SSHHost("host", known_hosts=None)

    def test_custom_known_hosts_path(self):
        host = SSHHost("host", known_hosts="/custom/path")
        assert host.config.known_hosts == "/custom/path"

    def test_known_hosts_type_annotation(self):
        """known_hosts accepts str and tuple, not None."""
        # str works
        h1 = SSHHost("host", known_hosts="/path")
        assert h1.config.known_hosts == "/path"
        # tuple works
        h2 = SSHHost("host", known_hosts=())
        assert h2.config.known_hosts == ()


# =============================================================================
# SSHConnectionPool: flag passthrough
# =============================================================================


class TestPoolFlagPassthrough:
    """SSHConnectionPool passes the new flag through to SSHHost."""

    @pytest.mark.asyncio
    async def test_pool_passes_disable_flag(self):
        pool = SSHConnectionPool()
        host = await pool.get("host", disable_host_key_checking=True)
        assert host.config.disable_host_key_checking is True

    @pytest.mark.asyncio
    async def test_pool_default_secure(self):
        pool = SSHConnectionPool()
        host = await pool.get("host")
        assert host.config.disable_host_key_checking is False
        assert host.config.known_hosts == ()

    @pytest.mark.asyncio
    async def test_pool_known_hosts_none_raises(self):
        pool = SSHConnectionPool()
        with pytest.raises(ValueError, match="known_hosts=None is not supported"):
            await pool.get("host", known_hosts=None)

    @pytest.mark.asyncio
    async def test_pool_custom_known_hosts(self):
        pool = SSHConnectionPool()
        host = await pool.get("host", known_hosts="/custom/path")
        assert host.config.known_hosts == "/custom/path"


# =============================================================================
# SSHConnectionPool: cache key issue (reviewer finding)
# =============================================================================


class TestPoolCacheKeySecurity:
    """Cache key includes known_hosts and disable_host_key_checking.

    The reviewer noted the original cache key omitted security fields.
    The implementer fixed this — these tests verify the fix.
    """

    @pytest.mark.asyncio
    async def test_different_security_settings_produce_different_hosts(self):
        """Different disable_host_key_checking values must not share cached host."""
        pool = SSHConnectionPool()
        host_insecure = await pool.get("host", disable_host_key_checking=True)
        host_secure = await pool.get("host", disable_host_key_checking=False)

        assert host_insecure is not host_secure
        assert host_insecure.config.disable_host_key_checking is True
        assert host_secure.config.disable_host_key_checking is False

    @pytest.mark.asyncio
    async def test_different_known_hosts_produce_different_hosts(self):
        """Different known_hosts paths must not share cached host."""
        pool = SSHConnectionPool()
        host1 = await pool.get("host", known_hosts="/path/a")
        host2 = await pool.get("host", known_hosts="/path/b")

        assert host1 is not host2
        assert host1.config.known_hosts == "/path/a"
        assert host2.config.known_hosts == "/path/b"

    @pytest.mark.asyncio
    async def test_same_security_settings_reuse_host(self):
        """Same security settings should still reuse the cached host."""
        pool = SSHConnectionPool()
        host1 = await pool.get("host", disable_host_key_checking=True)
        host2 = await pool.get("host", disable_host_key_checking=True)

        assert host1 is host2


# =============================================================================
# to_asyncssh_options: comprehensive output verification
# =============================================================================


class TestToAsyncsshOptions:
    """Verify to_asyncssh_options produces correct output for all combinations."""

    def test_all_fields_set(self):
        config = SSHConfig(
            hostname="host",
            port=2222,
            username="user",
            password="pass",
            client_keys=["/key1", "/key2"],
            known_hosts="/known",
            connect_timeout=10.0,
            keepalive_interval=5.0,
        )
        opts = config.to_asyncssh_options()
        assert opts == {
            "host": "host",
            "port": 2222,
            "username": "user",
            "password": "pass",
            "client_keys": ["/key1", "/key2"],
            "known_hosts": "/known",
            "connect_timeout": 10.0,
            "keepalive_interval": 5.0,
        }

    def test_disable_flag_wins_over_path(self):
        """disable_host_key_checking=True overrides a custom known_hosts path."""
        config = SSHConfig(
            hostname="host",
            known_hosts="/custom",
            disable_host_key_checking=True,
        )
        opts = config.to_asyncssh_options()
        assert opts["known_hosts"] is None

    def test_minimal_config_output(self):
        config = SSHConfig(hostname="host")
        opts = config.to_asyncssh_options()
        assert set(opts.keys()) == {"host", "port", "connect_timeout", "keepalive_interval"}
