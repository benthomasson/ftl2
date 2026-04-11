"""Tests for runtime policy reload (#73)."""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

from ftl2.automation.context import AutomationContext
from ftl2.policy import Policy, PolicyRule


# ---------------------------------------------------------------------------
# reload_policy()
# ---------------------------------------------------------------------------


class TestReloadPolicy:
    """Tests for AutomationContext.reload_policy()."""

    @pytest.mark.asyncio
    async def test_reload_from_file(self, tmp_path):
        """reload_policy() picks up new rules from the original file."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: no shell\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1

            # Update the file with an additional rule
            policy_file.write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: shell\n"
                "    reason: no shell\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: raw\n"
                "    reason: no raw\n"
            )

            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 2

    @pytest.mark.asyncio
    async def test_reload_from_directory(self, tmp_path):
        """reload_policy() works with a policy directory."""
        policy_dir = tmp_path / "policies"
        policy_dir.mkdir()
        (policy_dir / "01-base.yaml").write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: no shell\n"
        )

        async with AutomationContext(policy=str(policy_dir), quiet=True) as ctx:
            assert len(ctx.policy.rules) == 1

            # Add another file
            (policy_dir / "02-extra.yaml").write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: raw\n"
                "    reason: no raw\n"
            )

            await ctx.reload_policy()
            assert len(ctx.policy.rules) == 2

    @pytest.mark.asyncio
    async def test_reload_with_explicit_path(self, tmp_path):
        """reload_policy(path=...) switches to a new source."""
        file_a = tmp_path / "a.yaml"
        file_b = tmp_path / "b.yaml"
        file_a.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: from a\n"
        )
        file_b.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: raw\n"
            "    reason: from b\n"
        )

        async with AutomationContext(policy=str(file_a), quiet=True) as ctx:
            assert ctx.policy.rules[0].reason == "from a"

            await ctx.reload_policy(path=str(file_b))
            assert ctx.policy.rules[0].reason == "from b"

            # Source should be updated — reloading without path uses b
            file_b.write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: command\n"
                "    reason: from b updated\n"
            )
            await ctx.reload_policy()
            assert ctx.policy.rules[0].reason == "from b updated"

    @pytest.mark.asyncio
    async def test_reload_no_source_raises(self):
        """reload_policy() raises ValueError when no source is configured."""
        async with AutomationContext(quiet=True) as ctx:
            with pytest.raises(ValueError, match="No policy source"):
                await ctx.reload_policy()

    @pytest.mark.asyncio
    async def test_reload_atomic_on_invalid_file(self, tmp_path):
        """Invalid file during reload leaves old policy unchanged."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: original\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            original_policy = ctx.policy
            assert len(original_policy.rules) == 1

            # Write invalid YAML
            policy_file.write_text("rules:\n  - decision: invalid_value\n")

            with pytest.raises(ValueError):
                await ctx.reload_policy()

            # Old policy should be unchanged
            assert ctx.policy is original_policy
            assert len(ctx.policy.rules) == 1
            assert ctx.policy.rules[0].reason == "original"

    @pytest.mark.asyncio
    async def test_reload_file_not_found(self, tmp_path):
        """reload_policy() raises FileNotFoundError for missing file."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text("rules: []\n")

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            policy_file.unlink()

            with pytest.raises(FileNotFoundError):
                await ctx.reload_policy()


# ---------------------------------------------------------------------------
# policy property
# ---------------------------------------------------------------------------


class TestPolicyProperty:
    """Tests for AutomationContext.policy property."""

    @pytest.mark.asyncio
    async def test_policy_returns_current_policy(self, tmp_path):
        """policy property returns the active Policy object."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: test\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            assert isinstance(ctx.policy, Policy)
            assert len(ctx.policy.rules) == 1

    @pytest.mark.asyncio
    async def test_policy_empty_when_no_policy(self):
        """policy property returns empty policy when none configured."""
        async with AutomationContext(quiet=True) as ctx:
            assert isinstance(ctx.policy, Policy)
            assert len(ctx.policy.rules) == 0


# ---------------------------------------------------------------------------
# watch_policy() / unwatch_policy()
# ---------------------------------------------------------------------------


class TestWatchPolicy:
    """Tests for policy file watching."""

    @pytest.mark.asyncio
    async def test_watch_detects_changes(self, tmp_path):
        """watch_policy() detects file changes and auto-reloads."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: v1\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            assert len(ctx.policy.rules) == 1

            # Modify the file
            # Ensure mtime changes (some filesystems have 1s resolution)
            time.sleep(0.05)
            policy_file.write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: shell\n"
                "    reason: v2\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: raw\n"
                "    reason: v2\n"
            )

            # Wait for the watcher to detect the change
            for _ in range(30):
                await asyncio.sleep(0.1)
                if len(ctx.policy.rules) == 2:
                    break

            assert len(ctx.policy.rules) == 2
            assert ctx.policy.rules[0].reason == "v2"

    @pytest.mark.asyncio
    async def test_watch_survives_invalid_file(self, tmp_path):
        """Auto-reload skips invalid files and keeps old policy."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: original\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)

            # Write invalid content
            time.sleep(0.05)
            policy_file.write_text("rules:\n  - decision: bad_value\n")

            # Wait a bit — watcher should log error but keep old policy
            await asyncio.sleep(0.5)
            assert len(ctx.policy.rules) == 1
            assert ctx.policy.rules[0].reason == "original"

            # Write valid content — should recover
            time.sleep(0.05)
            policy_file.write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: raw\n"
                "    reason: recovered\n"
            )

            for _ in range(30):
                await asyncio.sleep(0.1)
                if ctx.policy.rules[0].reason == "recovered":
                    break

            assert ctx.policy.rules[0].reason == "recovered"

    @pytest.mark.asyncio
    async def test_unwatch_stops_watching(self, tmp_path):
        """unwatch_policy() stops the background task."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: v1\n"
        )

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            ctx.unwatch_policy()

            # Modify the file
            time.sleep(0.05)
            policy_file.write_text(
                "rules:\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: shell\n"
                "    reason: v2\n"
                "  - decision: deny\n"
                "    match:\n"
                "      module: raw\n"
                "    reason: v2\n"
            )

            await asyncio.sleep(0.5)
            # Should still have the old policy — watcher is stopped
            assert len(ctx.policy.rules) == 1
            assert ctx.policy.rules[0].reason == "v1"

    @pytest.mark.asyncio
    async def test_watch_no_source_raises(self):
        """watch_policy() raises ValueError when no source is configured."""
        async with AutomationContext(quiet=True) as ctx:
            with pytest.raises(ValueError, match="No policy source"):
                await ctx.watch_policy()

    @pytest.mark.asyncio
    async def test_watch_idempotent(self, tmp_path):
        """Calling watch_policy() twice doesn't create duplicate tasks."""
        policy_file = tmp_path / "rules.yaml"
        policy_file.write_text("rules: []\n")

        async with AutomationContext(policy=str(policy_file), quiet=True) as ctx:
            await ctx.watch_policy(interval=0.1)
            task1 = ctx._policy_watch_task
            await ctx.watch_policy(interval=0.1)
            task2 = ctx._policy_watch_task
            assert task1 is task2
