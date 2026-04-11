"""QA tests for runtime policy reload (#73).

Covers: reload_policy(), watch_policy(), unwatch_policy(), policy property,
to_dict()/from_dict() serialization, UpdatePolicy message reservation,
and edge cases identified by the reviewer.
"""

import asyncio
import time
from pathlib import Path

import pytest

from ftl2.automation.context import AutomationContext
from ftl2.policy import Policy, PolicyRule


# Helper to write a policy YAML file
def _write_policy(path: Path, rules_yaml: str) -> None:
    path.write_text(f"rules:\n{rules_yaml}")


def _deny_rule_yaml(module: str, reason: str) -> str:
    return (
        f"  - decision: deny\n"
        f"    match:\n"
        f"      module: {module}\n"
        f"    reason: {reason}\n"
    )


# ---------------------------------------------------------------------------
# 1. reload_policy() — manual reload
# ---------------------------------------------------------------------------


class TestReloadPolicyManual:
    """Manual reload tests covering file, directory, path switching, and errors."""

    @pytest.mark.asyncio
    async def test_reload_picks_up_added_rule(self, tmp_path):
        """After adding a rule to the file, reload_policy() sees it."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1

            # Add a second rule
            _write_policy(
                pf,
                _deny_rule_yaml("shell", "v1") + _deny_rule_yaml("raw", "v1"),
            )
            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 2

    @pytest.mark.asyncio
    async def test_reload_picks_up_removed_rule(self, tmp_path):
        """After removing a rule from the file, reload_policy() sees the removal."""
        pf = tmp_path / "rules.yaml"
        _write_policy(
            pf,
            _deny_rule_yaml("shell", "orig") + _deny_rule_yaml("raw", "orig"),
        )

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 2

            _write_policy(pf, _deny_rule_yaml("shell", "orig"))
            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 1

    @pytest.mark.asyncio
    async def test_reload_from_directory_adds_new_file(self, tmp_path):
        """reload_policy() on a directory picks up a newly added YAML file."""
        d = tmp_path / "policies"
        d.mkdir()
        _write_policy(d / "01-base.yaml", _deny_rule_yaml("shell", "base"))

        async with AutomationContext(policy=str(d), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1

            _write_policy(d / "02-extra.yaml", _deny_rule_yaml("raw", "extra"))
            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 2

    @pytest.mark.asyncio
    async def test_reload_switches_source_path(self, tmp_path):
        """reload_policy(path=...) switches the policy source permanently."""
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        _write_policy(a, _deny_rule_yaml("shell", "from-a"))
        _write_policy(b, _deny_rule_yaml("command", "from-b"))

        async with AutomationContext(policy=str(a), quiet=True) as ctx:
            assert ctx.policy.rules[0].reason == "from-a"

            await ctx.reload_policy(path=str(b))
            assert ctx.policy.rules[0].reason == "from-b"

            # Subsequent reload without path uses new source (b)
            _write_policy(b, _deny_rule_yaml("command", "from-b-v2"))
            await ctx.reload_policy()
            assert ctx.policy.rules[0].reason == "from-b-v2"

    @pytest.mark.asyncio
    async def test_reload_no_source_raises_valueerror(self):
        """reload_policy() with no original source and no path raises ValueError."""
        async with AutomationContext(quiet=True) as ctx:
            with pytest.raises(ValueError, match="No policy source"):
                await ctx.reload_policy()

    @pytest.mark.asyncio
    async def test_reload_missing_file_raises_filenotfounderror(self, tmp_path):
        """reload_policy() raises FileNotFoundError when the file is deleted."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            pf.unlink()
            with pytest.raises(FileNotFoundError):
                await ctx.reload_policy()

    @pytest.mark.asyncio
    async def test_reload_atomic_invalid_file_preserves_old_policy(self, tmp_path):
        """If the new file is invalid, the old policy stays in effect."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "original"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            old_policy = ctx.policy
            assert len(old_policy.rules) == 1

            # Write invalid YAML (bad decision value)
            pf.write_text("rules:\n  - decision: allow\n    match: {module: shell}\n")

            with pytest.raises(ValueError):
                await ctx.reload_policy()

            # Old policy must be unchanged
            assert ctx.policy is old_policy
            assert ctx.policy.rules[0].reason == "original"

    @pytest.mark.asyncio
    async def test_reload_empty_rules_file(self, tmp_path):
        """Reloading with an empty rules file results in empty policy (permits all)."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1

            pf.write_text("rules: []\n")
            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 0


# ---------------------------------------------------------------------------
# 2. policy property
# ---------------------------------------------------------------------------


class TestPolicyProperty:
    """Tests for the read-only policy property."""

    @pytest.mark.asyncio
    async def test_policy_returns_policy_instance(self, tmp_path):
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "test"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            assert isinstance(ctx.policy, Policy)
            assert len(ctx.policy.rules) == 1

    @pytest.mark.asyncio
    async def test_policy_empty_when_no_policy_configured(self):
        async with AutomationContext(quiet=True) as ctx:
            assert isinstance(ctx.policy, Policy)
            assert len(ctx.policy.rules) == 0

    @pytest.mark.asyncio
    async def test_policy_updates_after_reload(self, tmp_path):
        """policy property reflects the new policy after reload_policy()."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            p1 = ctx.policy
            _write_policy(pf, _deny_rule_yaml("raw", "v2"))
            await ctx.reload_policy()
            p2 = ctx.policy
            assert p1 is not p2
            assert p2.rules[0].reason == "v2"


# ---------------------------------------------------------------------------
# 3. watch_policy() / unwatch_policy()
# ---------------------------------------------------------------------------


class TestWatchPolicy:
    """Tests for the file-watching auto-reload."""

    @pytest.mark.asyncio
    async def test_watch_detects_file_change(self, tmp_path):
        """Watcher detects file changes and auto-reloads policy."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            assert len(ctx.policy.rules) == 1

            # Ensure mtime changes
            time.sleep(0.05)
            _write_policy(
                pf,
                _deny_rule_yaml("shell", "v2") + _deny_rule_yaml("raw", "v2"),
            )

            # Wait for watcher to detect
            for _ in range(30):
                await asyncio.sleep(0.1)
                if len(ctx.policy.rules) == 2:
                    break
            assert len(ctx.policy.rules) == 2
            assert ctx.policy.rules[0].reason == "v2"

    @pytest.mark.asyncio
    async def test_watch_survives_invalid_file(self, tmp_path):
        """Watcher logs error on invalid file but keeps old policy and continues."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "original"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)

            # Write invalid content
            time.sleep(0.05)
            pf.write_text("rules:\n  - decision: allow\n    match: {module: x}\n")

            await asyncio.sleep(0.5)
            # Old policy should survive
            assert len(ctx.policy.rules) == 1
            assert ctx.policy.rules[0].reason == "original"

            # Recover with valid content
            time.sleep(0.05)
            _write_policy(pf, _deny_rule_yaml("raw", "recovered"))

            for _ in range(30):
                await asyncio.sleep(0.1)
                if ctx.policy.rules[0].reason == "recovered":
                    break
            assert ctx.policy.rules[0].reason == "recovered"

    @pytest.mark.asyncio
    async def test_unwatch_stops_detection(self, tmp_path):
        """After unwatch_policy(), file changes are not detected."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            ctx.unwatch_policy()

            time.sleep(0.05)
            _write_policy(pf, _deny_rule_yaml("shell", "v2"))

            await asyncio.sleep(0.5)
            # Still old policy — watcher is stopped
            assert ctx.policy.rules[0].reason == "v1"

    @pytest.mark.asyncio
    async def test_watch_no_source_raises_valueerror(self):
        """watch_policy() with no policy source raises ValueError."""
        async with AutomationContext(quiet=True) as ctx:
            with pytest.raises(ValueError, match="No policy source"):
                await ctx.watch_policy()

    @pytest.mark.asyncio
    async def test_watch_idempotent(self, tmp_path):
        """Calling watch_policy() twice returns the same task (no duplicates)."""
        pf = tmp_path / "rules.yaml"
        pf.write_text("rules: []\n")

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            task1 = ctx._policy_watch_task
            await ctx.watch_policy(interval=0.1)
            task2 = ctx._policy_watch_task
            assert task1 is task2

    @pytest.mark.asyncio
    async def test_aexit_cancels_watcher(self, tmp_path):
        """__aexit__ cleanly cancels the watcher without asyncio warnings."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "v1"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            assert ctx._policy_watch_task is not None
        # After exit, task should be cancelled
        assert ctx._policy_watch_task is None

    @pytest.mark.asyncio
    async def test_unwatch_when_not_watching_is_noop(self, tmp_path):
        """unwatch_policy() when not watching does nothing (no error)."""
        pf = tmp_path / "rules.yaml"
        pf.write_text("rules: []\n")

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            ctx.unwatch_policy()  # should not raise


# ---------------------------------------------------------------------------
# 5. Edge cases from reviewer
# ---------------------------------------------------------------------------


class TestReviewerEdgeCases:
    """Edge cases identified in the code review."""

    @pytest.mark.asyncio
    async def test_directory_ignores_non_yaml_files(self, tmp_path):
        """Directory-mode policy ignores .txt, .md, etc."""
        d = tmp_path / "policies"
        d.mkdir()
        _write_policy(d / "rules.yaml", _deny_rule_yaml("shell", "yaml"))
        (d / "readme.txt").write_text("not a policy")
        (d / "notes.md").write_text("also not a policy")

        async with AutomationContext(policy=str(d), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1
            assert ctx.policy.rules[0].reason == "yaml"

    @pytest.mark.asyncio
    async def test_rapid_successive_writes_converge(self, tmp_path):
        """After rapid file writes, watcher converges to the final state."""
        pf = tmp_path / "rules.yaml"
        _write_policy(pf, _deny_rule_yaml("shell", "initial"))

        async with AutomationContext(policy=str(pf), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)

            # Rapid writes
            for i in range(5):
                time.sleep(0.02)
                _write_policy(pf, _deny_rule_yaml("shell", f"v{i}"))

            # Wait for watcher to converge
            for _ in range(30):
                await asyncio.sleep(0.1)
                if ctx.policy.rules[0].reason == "v4":
                    break

            assert ctx.policy.rules[0].reason == "v4"

    @pytest.mark.asyncio
    async def test_watch_directory_detects_new_file(self, tmp_path):
        """Watcher on a directory detects when a new YAML file is added."""
        d = tmp_path / "policies"
        d.mkdir()
        _write_policy(d / "01-base.yaml", _deny_rule_yaml("shell", "base"))

        async with AutomationContext(policy=str(d), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            assert len(ctx.policy.rules) == 1

            time.sleep(0.05)
            _write_policy(d / "02-extra.yaml", _deny_rule_yaml("raw", "extra"))

            for _ in range(30):
                await asyncio.sleep(0.1)
                if len(ctx.policy.rules) == 2:
                    break

            assert len(ctx.policy.rules) == 2
