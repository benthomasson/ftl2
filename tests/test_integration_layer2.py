"""Layer 2 integration tests: real SSH via AutomationContext.

These tests exercise the full path: SSH -> gate process -> module -> result.
They require sshd on localhost:22 with key-based auth for the current user.
In CI, this is set up by the "Set up localhost SSH" workflow step.
Locally, they skip automatically if sshd is not available.

Run:  uv run pytest tests/test_integration_layer2.py -v
"""

import os

import pytest

from ftl2.automation.context import AutomationContext

pytestmark = pytest.mark.integration


# ── Helpers ──────────────────────────────────────────────────────────────


@pytest.fixture
async def ftl(localhost_ssh_inventory):
    """AutomationContext wired to the localhost SSH inventory."""
    async with AutomationContext(
        inventory=localhost_ssh_inventory,
        quiet=True,
    ) as ctx:
        yield ctx


# ── 1. Command execution ────────────────────────────────────────────────


async def test_command_echo(ftl, localhost_ssh_host):
    """Run 'echo hello' on localhost via SSH and verify stdout."""
    results = await ftl.run_on(localhost_ssh_host, "command", cmd="echo hello")
    assert len(results) == 1
    r = results[0]
    assert r.success
    assert r.output["stdout"].strip() == "hello"
    assert r.output["rc"] == 0
    assert r.host == "localhost-ssh"


async def test_command_return_code(ftl, localhost_ssh_host):
    """Non-zero rc is captured without raising."""
    results = await ftl.run_on(localhost_ssh_host, "command", cmd="exit 42")
    r = results[0]
    # The command module returns success=True with non-zero rc
    # unless check=True is passed
    assert r.output["rc"] == 42


async def test_command_stderr(ftl, localhost_ssh_host):
    """stderr is captured separately from stdout."""
    results = await ftl.run_on(
        localhost_ssh_host, "command", cmd="echo err >&2"
    )
    r = results[0]
    assert r.success
    assert "err" in r.output["stderr"]


async def test_shell_pipeline(ftl, localhost_ssh_host):
    """Shell module handles pipes and shell features."""
    results = await ftl.run_on(
        localhost_ssh_host, "shell", cmd="echo aaa bbb ccc | wc -w"
    )
    r = results[0]
    assert r.success
    assert r.output["stdout"].strip() == "3"


# ── 2. File operations ──────────────────────────────────────────────────


async def test_file_create_directory(ftl, localhost_ssh_host, tmp_path):
    """Create a directory on the remote host via the file module."""
    target = str(tmp_path / "integration_test_dir")
    results = await ftl.run_on(
        localhost_ssh_host, "file", path=target, state="directory"
    )
    r = results[0]
    assert r.success
    assert r.changed
    assert os.path.isdir(target)


async def test_file_touch(ftl, localhost_ssh_host, tmp_path):
    """Touch creates a file that did not exist."""
    target = str(tmp_path / "touched.txt")
    results = await ftl.run_on(
        localhost_ssh_host, "file", path=target, state="touch"
    )
    r = results[0]
    assert r.success
    assert r.changed
    assert os.path.exists(target)


async def test_file_absent(ftl, localhost_ssh_host, tmp_path):
    """Absent removes a file."""
    target = tmp_path / "to_remove.txt"
    target.write_text("bye")
    results = await ftl.run_on(
        localhost_ssh_host, "file", path=str(target), state="absent"
    )
    r = results[0]
    assert r.success
    assert r.changed
    assert not target.exists()


async def test_file_idempotent_directory(ftl, localhost_ssh_host, tmp_path):
    """Creating a directory that already exists is idempotent (changed=False)."""
    target = str(tmp_path / "already_here")
    os.makedirs(target)
    results = await ftl.run_on(
        localhost_ssh_host, "file", path=target, state="directory"
    )
    r = results[0]
    assert r.success
    assert not r.changed


# ── 3. Copy module ───────────────────────────────────────────────────────


async def test_copy_file(ftl, localhost_ssh_host, tmp_path):
    """Copy a file from src to dest on the remote host."""
    src = tmp_path / "src.txt"
    src.write_text("integration test content")
    dest = str(tmp_path / "dest.txt")

    results = await ftl.run_on(
        localhost_ssh_host, "copy", src=str(src), dest=dest
    )
    r = results[0]
    assert r.success
    assert r.changed
    with open(dest) as f:
        assert f.read() == "integration test content"


# ── 4. Multiple hosts / group targeting ──────────────────────────────────


async def test_run_on_group_name(ftl, localhost_ssh_host):
    """run_on accepts a group name string."""
    results = await ftl.run_on("ci_hosts", "command", cmd="echo group_ok")
    assert len(results) == 1
    assert results[0].success
    assert results[0].output["stdout"].strip() == "group_ok"


# ── 5. ExecuteResult metadata ───────────────────────────────────────────


async def test_result_has_metadata(ftl, localhost_ssh_host):
    """ExecuteResult carries module name, host, and timing."""
    results = await ftl.run_on(localhost_ssh_host, "command", cmd="true")
    r = results[0]
    assert r.module == "command"
    assert r.host == "localhost-ssh"
    assert r.duration > 0
    assert r.timestamp > 0


# ── 6. Context state tracking ───────────────────────────────────────────


async def test_context_tracks_results(ftl, localhost_ssh_host):
    """AutomationContext accumulates results across calls."""
    await ftl.run_on(localhost_ssh_host, "command", cmd="echo one")
    await ftl.run_on(localhost_ssh_host, "command", cmd="echo two")
    assert len(ftl.results) == 2


async def test_context_tracks_errors(ftl, localhost_ssh_host):
    """Failed modules appear in ftl.errors."""
    # Use a module call that will fail
    results = await ftl.run_on(
        localhost_ssh_host, "file", path="/root/no_permission_test", state="directory"
    )
    if not results[0].success:
        assert len(ftl.errors) >= 1


# ── 7. Command idempotency (creates/removes) ────────────────────────────


async def test_command_creates_skips(ftl, localhost_ssh_host, tmp_path):
    """Command with creates= skips when file exists."""
    marker = tmp_path / "marker"
    marker.write_text("exists")
    results = await ftl.run_on(
        localhost_ssh_host, "command", cmd="echo should_not_run", creates=str(marker)
    )
    r = results[0]
    assert r.success
    assert not r.changed


async def test_command_removes_skips(ftl, localhost_ssh_host, tmp_path):
    """Command with removes= skips when file does not exist."""
    missing = str(tmp_path / "nonexistent")
    results = await ftl.run_on(
        localhost_ssh_host, "command", cmd="echo should_not_run", removes=missing
    )
    r = results[0]
    assert r.success
    assert not r.changed
