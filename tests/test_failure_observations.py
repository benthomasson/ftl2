"""Tests for failure observations — auto-diagnostic collection on module failure."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ftl2.automation.context import AutomationContext
from ftl2.ftl_modules.executor import ExecuteResult
from ftl2.types import HostConfig


def _make_context():
    """Build an AutomationContext with defaults."""
    with patch.object(AutomationContext, '_check_name_collisions'):
        ctx = AutomationContext()
    return ctx


def _make_host(name="web01"):
    return HostConfig(name=name, ansible_host="192.168.1.10")


class TestFailureObservationRegistry:
    """Default observations and custom registration."""

    def test_default_service_observations(self):
        ctx = _make_context()
        obs = ctx._failure_observations.get("service", [])
        assert len(obs) == 2
        assert obs[0]["name"] == "systemctl_status"
        assert obs[1]["name"] == "journalctl"

    def test_default_copy_observations(self):
        ctx = _make_context()
        obs = ctx._failure_observations.get("copy", [])
        assert len(obs) == 2
        assert obs[0]["name"] == "dest_stat"

    def test_observe_on_failure_extends(self):
        ctx = _make_context()
        ctx.observe_on_failure("service", [
            {"name": "selinux_audit", "cmd": "ausearch -m avc -ts recent"},
        ])
        obs = ctx._failure_observations["service"]
        assert len(obs) == 3
        assert obs[2]["name"] == "selinux_audit"

    def test_observe_on_failure_new_module(self):
        ctx = _make_context()
        ctx.observe_on_failure("dnf", [
            {"name": "repo_list", "cmd": "dnf repolist"},
        ])
        assert len(ctx._failure_observations["dnf"]) == 1


class TestCollectFailureObservations:
    """_collect_failure_observations runs commands and returns results."""

    @pytest.fixture
    def ctx_with_ssh(self):
        ctx = _make_context()
        mock_ssh = AsyncMock()
        mock_ssh.run = AsyncMock(return_value=("active\n", "", 0))
        ctx._ssh_connections = {"web01": mock_ssh}
        return ctx, mock_ssh

    async def test_collects_service_observations(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "service", {"name": "caddy"}
        )
        assert "systemctl_status" in results
        assert results["systemctl_status"]["stdout"] == "active"
        assert results["systemctl_status"]["rc"] == 0

    async def test_substitutes_params(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        host = _make_host()
        await ctx._collect_failure_observations(
            host, "service", {"name": "caddy"}
        )
        calls = [c.args[0] for c in mock_ssh.run.call_args_list]
        assert any("caddy" in cmd for cmd in calls)

    async def test_skips_missing_params(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "copy", {}
        )
        assert "dest_stat" not in results
        assert "getenforce" in results

    async def test_skips_malformed_format_string(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        ctx._failure_observations["test_mod"] = [
            {"name": "bad_fmt", "cmd": "echo {unclosed"},
            {"name": "good", "cmd": "echo hello"},
        ]
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "test_mod", {}
        )
        assert "bad_fmt" not in results
        assert "good" in results

    async def test_returns_empty_for_unknown_module(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "unknown_module", {}
        )
        assert results == {}

    async def test_captures_observation_errors(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        mock_ssh.run = AsyncMock(side_effect=Exception("SSH broken"))
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "shell", {"cmd": "echo hi"}
        )
        assert "getenforce" in results
        assert "error" in results["getenforce"]

    async def test_handles_ssh_connection_failure(self):
        ctx = _make_context()
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "service", {"name": "caddy"}
        )
        assert results == {}

    async def test_become_prefix_applied(self):
        ctx = _make_context()
        mock_ssh = AsyncMock()
        mock_ssh.run = AsyncMock(return_value=("", "", 0))
        ctx._ssh_connections = {"web01": mock_ssh}
        host = _make_host()

        from ftl2.types import BecomeConfig
        become = BecomeConfig(become=True, become_user="root", become_method="sudo")

        await ctx._collect_failure_observations(
            host, "service", {"name": "nginx"}, become=become
        )
        calls = [c.args[0] for c in mock_ssh.run.call_args_list]
        assert all("sudo" in cmd for cmd in calls)

    async def test_fqcn_module_uses_short_name(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        host = _make_host()
        results = await ctx._collect_failure_observations(
            host, "ansible.builtin.service", {"name": "nginx"}
        )
        assert "systemctl_status" in results

    async def test_observations_attached_to_failed_result(self, ctx_with_ssh):
        ctx, mock_ssh = ctx_with_ssh
        result = ExecuteResult(
            success=False,
            output={"failed": True, "msg": "service not running"},
            module="service",
            host="web01",
        )
        host = _make_host()
        observations = await ctx._collect_failure_observations(
            host, "service", {"name": "caddy"}
        )
        result.output["observations"] = observations
        assert "systemctl_status" in result.output["observations"]
        assert "journalctl" in result.output["observations"]

    def test_default_file_observations(self):
        ctx = _make_context()
        obs = ctx._failure_observations.get("file", [])
        assert len(obs) == 2
        assert obs[0]["name"] == "path_stat"
        assert obs[1]["name"] == "getenforce"

    def test_default_shell_observations(self):
        ctx = _make_context()
        obs = ctx._failure_observations.get("shell", [])
        assert len(obs) == 1
        assert obs[0]["name"] == "getenforce"


class TestObservationDisplay:
    """Observations are printed in error output."""

    def test_log_error_prints_observations(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={
                "failed": True,
                "msg": "service failed",
                "observations": {
                    "systemctl_status": {"stdout": "inactive (dead)", "stderr": "", "rc": 3},
                    "journalctl": {"stdout": "Jun 11 error: permission denied", "stderr": "", "rc": 0},
                },
            },
            error="service failed",
            module="service",
            host="web01",
        )
        ctx._log_error("web01:service", result)
        captured = capsys.readouterr()
        assert "service failed" in captured.out
        assert "inactive (dead)" in captured.out
        assert "permission denied" in captured.out

    def test_log_error_shows_observation_errors(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={
                "failed": True,
                "msg": "copy failed",
                "observations": {
                    "dest_stat": {"error": "SSH broken"},
                },
            },
            error="copy failed",
            module="copy",
            host="web01",
        )
        ctx._log_error("web01:copy", result)
        captured = capsys.readouterr()
        assert "SSH broken" in captured.out

    def test_log_error_no_observations(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={"failed": True, "msg": "oops"},
            error="oops",
            module="shell",
            host="web01",
        )
        ctx._log_error("web01:shell", result)
        captured = capsys.readouterr()
        assert "oops" in captured.out

    def test_log_result_prints_observations_on_failure(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={
                "failed": True,
                "msg": "service not found",
                "observations": {
                    "systemctl_status": {"stdout": "Unit not found", "stderr": "", "rc": 4},
                },
            },
            error="service not found",
            module="service",
            host="web01",
        )
        ctx._log_result("web01:service", result, duration=0.5)
        captured = capsys.readouterr()
        assert "FAILED" in captured.out
        assert "Unit not found" in captured.out

    def test_log_error_shows_both_stdout_and_stderr(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={
                "failed": True,
                "msg": "service failed",
                "observations": {
                    "systemctl_status": {
                        "stdout": "inactive (dead)",
                        "stderr": "Warning: unit changed on disk",
                        "rc": 3,
                    },
                },
            },
            error="service failed",
            module="service",
            host="web01",
        )
        ctx._log_error("web01:service", result)
        captured = capsys.readouterr()
        assert "inactive (dead)" in captured.out
        assert "Warning: unit changed on disk" in captured.out

    def test_log_result_shows_stderr(self, capsys):
        ctx = _make_context()
        result = ExecuteResult(
            success=False,
            output={
                "failed": True,
                "msg": "service failed",
                "observations": {
                    "systemctl_status": {
                        "stdout": "",
                        "stderr": "error from stderr",
                        "rc": 1,
                    },
                },
            },
            error="service failed",
            module="service",
            host="web01",
        )
        ctx._log_result("web01:service", result, duration=0.5)
        captured = capsys.readouterr()
        assert "error from stderr" in captured.out
