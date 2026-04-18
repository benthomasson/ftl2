"""Smoke test: verify SSH to localhost works in CI.

GitHub Actions runners have sshd running and the runner user's key
authorized for localhost.  This single test confirms that — if it
passes, Layer 2 integration tests can use localhost SSH.

Run locally:  pytest tests/test_ci_ssh.py -v
In CI:        included in the normal test run (no special flags)
"""

import os

import asyncssh
import pytest


def _localhost_ssh_available() -> bool:
    """Quick check: can we TCP-connect to localhost:22?"""
    import socket

    try:
        s = socket.create_connection(("127.0.0.1", 22), timeout=2)
        s.close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _localhost_ssh_available(),
    reason="sshd not listening on localhost:22",
)
async def test_ssh_to_localhost():
    """Connect to localhost over SSH and run 'echo ok'.

    This validates the assumption that the CI runner (or local dev
    machine) accepts SSH connections from the current user with
    default key-based auth.  No password, no Docker, no special setup.
    """
    user = os.getenv("USER") or os.getlogin()

    conn = await asyncssh.connect(
        "127.0.0.1",
        port=22,
        username=user,
        known_hosts=None,
        connect_timeout=5,
    )
    try:
        result = await conn.run("echo ok", check=True)
        assert result.stdout.strip() == "ok"
    finally:
        conn.close()
