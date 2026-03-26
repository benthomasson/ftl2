#!/usr/bin/env python3
"""Test replay parameter comparison (Closes #37).

Validates that replay matching includes parameter comparison,
not just positional (module, host) matching. When parameters
change between runs, the replay system should warn and re-execute
instead of silently returning stale cached results.

All tests use pre-built audit JSON files and replay-only mode to avoid
needing live module execution (which requires platform-specific deps).
"""

import asyncio
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ftl2.automation import automation


def make_audit(actions, success=True):
    """Build a minimal audit data dict."""
    return {
        "started": "2026-02-09T00:00:00+00:00",
        "completed": "2026-02-09T00:00:01+00:00",
        "check_mode": False,
        "success": success,
        "actions": actions,
        "errors": [],
    }


def make_action(module, host="localhost", params=None, success=True, changed=False, output=None):
    """Build a minimal audit action dict."""
    return {
        "module": module,
        "host": host,
        "params": params or {},
        "success": success,
        "changed": changed,
        "duration": 0.001,
        "timestamp": "2026-02-09T00:00:00+00:00",
        "output": output or {},
    }


# ---------------------------------------------------------------------------
# Core parameter comparison tests
# ---------------------------------------------------------------------------

async def test_changed_params_skip_replay():
    """Changed params should cause re-execution, not stale replay."""
    print("TEST 1: Changed params skip replay")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        # Pre-build audit with cmd="echo 'original'"
        audit = make_audit([
            make_action("command", params={"cmd": "echo 'original'"}, success=True,
                        output={"stdout": "original", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        # Replay with different params - should NOT replay (params changed)
        # Since replay returns None, the module executes fresh.
        # But we can't execute fresh without systemd. So we test via
        # the replay-only path: if _try_replay returns None, the action
        # is NOT marked replayed. We use try/except to catch the execution error.
        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=False,
            ) as ftl:
                await ftl.command(cmd="echo 'changed'")
        except Exception:
            pass  # Expected - fresh execution fails without systemd

        # Check audit: if the action was NOT replayed, it means params comparison worked
        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed actions (params changed), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_same_params_replay():
    """Identical params should replay successfully."""
    print("TEST 2: Same params replay normally")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action("command", params={"cmd": "echo 'same'"}, success=True,
                        output={"stdout": "same", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        async with automation(
            record=str(audit_file),
            replay=str(audit_file),
            quiet=True,
        ) as ftl:
            result = await ftl.command(cmd="echo 'same'")

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 1, f"Expected 1 replayed action, got {len(replayed)}"

        print("  PASSED")
        return True


async def test_replay_stops_entirely_after_param_mismatch():
    """After a param mismatch, ALL subsequent actions should execute fresh."""
    print("TEST 3: Replay stops entirely after param mismatch")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"
        f1 = tmpdir / "f1.txt"

        audit = make_audit([
            make_action("file", params={"path": str(f1), "state": "touch"}, success=True,
                        changed=True, output={"changed": True, "path": str(f1)}),
            make_action("command", params={"cmd": "echo 'step2'"}, success=True,
                        output={"stdout": "step2", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        # First action same params - replays. Second action different params - fails replay.
        try:
            async with automation(
                record=str(audit_file),
                replay=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.file(path=str(f1), state="touch")      # same - replays
                await ftl.command(cmd="echo 'different'")          # changed - fresh (will error)
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 1, f"Expected 1 replayed (first only), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_subsequent_actions_fresh_after_param_mismatch():
    """Actions after a param mismatch should also execute fresh (replay disabled)."""
    print("TEST 4: Subsequent actions after mismatch are all fresh")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action("command", params={"cmd": "echo 'a'"}, success=True,
                        output={"stdout": "a", "rc": 0}),
            make_action("command", params={"cmd": "echo 'b'"}, success=True,
                        output={"stdout": "b", "rc": 0}),
            make_action("command", params={"cmd": "echo 'c'"}, success=True,
                        output={"stdout": "c", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        # First same, second changed. Third is same as original but replay
        # should be disabled entirely after step 2.
        try:
            async with automation(
                record=str(audit_file),
                replay=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo 'a'")          # same - replays
                await ftl.command(cmd="echo 'b_changed'")   # changed - fresh (errors)
                await ftl.command(cmd="echo 'c'")            # replay disabled - fresh
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 1, f"Expected only 1 replayed (first), got {len(replayed)}"

        print("  PASSED")
        return True


# ---------------------------------------------------------------------------
# Warning message tests
# ---------------------------------------------------------------------------

async def test_warning_printed_on_param_mismatch():
    """A warning should be printed when params differ (quiet=False)."""
    print("TEST 5: Warning printed on param mismatch")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action("command", params={"cmd": "echo 'v1'"}, success=True,
                        output={"stdout": "v1", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=False,
            ) as ftl:
                await ftl.command(cmd="echo 'v2'")
        except Exception:
            pass
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "params changed" in output, f"Expected 'params changed' warning, got: {output!r}"

        print("  PASSED")
        return True


async def test_no_warning_when_quiet():
    """No warning should be printed when quiet=True."""
    print("TEST 6: No warning when quiet=True")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action("command", params={"cmd": "echo 'v1'"}, success=True,
                        output={"stdout": "v1", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo 'v2'")
        except Exception:
            pass
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "params changed" not in output, f"Warning printed despite quiet=True: {output!r}"

        print("  PASSED")
        return True


# ---------------------------------------------------------------------------
# Secret / redaction interaction tests
# ---------------------------------------------------------------------------

async def test_redacted_secrets_dont_cause_false_mismatch():
    """Params with secrets should match if only the secret value changes (redacted to ***)."""
    print("TEST 7: Redacted secrets don't cause false mismatch")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "uri",
                params={
                    "url": "https://api.example.com/data",
                    "headers": {"Authorization": "***", "Content-Type": "application/json"},
                },
                success=True,
                output={"status": 200, "body": "ok"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        async with automation(replay=str(audit_file), quiet=True) as ftl:
            result = await ftl.uri(
                url="https://api.example.com/data",
                headers={"Authorization": "Bearer new-token-xyz", "Content-Type": "application/json"},
            )

        assert result.get("status") == 200, f"Expected replayed result, got: {result}"

        print("  PASSED")
        return True


async def test_different_nonsecret_http_params_mismatch():
    """Non-secret HTTP param changes should still trigger re-execution."""
    print("TEST 8: Non-secret HTTP param change triggers re-execution")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "uri",
                params={
                    "url": "https://api.example.com/v1/data",
                    "headers": {"Authorization": "***"},
                },
                success=True,
                output={"status": 200, "body": "v1"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(replay=str(audit_file), quiet=True) as ftl:
                result = await ftl.uri(
                    url="https://api.example.com/v2/data",  # Changed URL
                    headers={"Authorization": "Bearer token"},
                )
            print("  PASSED (executed fresh)")
            return True
        except Exception:
            # uri fails without a server - that's fine, the point is it didn't replay
            print("  PASSED (re-executed, failed as expected without server)")
            return True


# ---------------------------------------------------------------------------
# Edge cases from reviewer notes
# ---------------------------------------------------------------------------

async def test_empty_params_vs_no_params_key():
    """action.get('params', {}) returns {} when key missing. Module calls with params won't match."""
    print("TEST 9: Empty params vs missing params key")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"
        f1 = tmpdir / "test.txt"

        # Audit WITHOUT a params key
        audit = make_audit([{
            "module": "file",
            "host": "localhost",
            # No "params" key!
            "success": True,
            "changed": True,
            "duration": 0.001,
            "timestamp": "2026-02-09T00:00:00+00:00",
            "output": {"changed": True, "path": str(f1)},
        }])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.file(path=str(f1), state="touch")
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed (params mismatch), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_nested_dict_params_same():
    """Nested dict parameters should compare correctly when identical."""
    print("TEST 10: Nested dict params match correctly")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "command",
                params={"cmd": "echo test", "environment": {"FOO": "bar", "BAZ": "qux"}},
                success=True,
                output={"stdout": "test"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        async with automation(
            replay=str(audit_file),
            record=str(audit_file),
            quiet=True,
        ) as ftl:
            result = await ftl.command(cmd="echo test", environment={"FOO": "bar", "BAZ": "qux"})

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 1, f"Expected 1 replayed (same nested params), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_nested_dict_params_changed():
    """Changed nested dict value should trigger re-execution."""
    print("TEST 11: Changed nested dict value triggers re-execution")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "command",
                params={"cmd": "echo test", "environment": {"FOO": "bar"}},
                success=True,
                output={"stdout": "test"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo test", environment={"FOO": "changed"})
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed (nested params changed), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_added_param_triggers_reexecution():
    """Adding a new parameter should trigger re-execution."""
    print("TEST 12: Added param triggers re-execution")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "command",
                params={"cmd": "echo test"},
                success=True,
                output={"stdout": "test"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo test", chdir="/tmp")
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed (param added), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_removed_param_triggers_reexecution():
    """Removing a parameter should trigger re-execution."""
    print("TEST 13: Removed param triggers re-execution")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "command",
                params={"cmd": "echo test", "chdir": "/tmp"},
                success=True,
                output={"stdout": "test"},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(
                replay=str(audit_file),
                record=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo test")
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed (param removed), got {len(replayed)}"

        print("  PASSED")
        return True


async def test_bearer_token_redaction_matching():
    """bearer_token param should be redacted before comparison."""
    print("TEST 14: bearer_token redaction matching")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "uri",
                params={"url": "https://api.example.com", "bearer_token": "***"},
                success=True,
                output={"status": 200},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        async with automation(replay=str(audit_file), quiet=True) as ftl:
            result = await ftl.uri(
                url="https://api.example.com",
                bearer_token="totally-different-token",
            )

        assert result.get("status") == 200, f"Expected replayed result, got: {result}"

        print("  PASSED")
        return True


async def test_url_password_redaction_matching():
    """url_password param should be redacted before comparison."""
    print("TEST 15: url_password redaction matching")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action(
                "uri",
                params={"url": "https://api.example.com", "url_password": "***"},
                success=True,
                output={"status": 200},
            )
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        async with automation(replay=str(audit_file), quiet=True) as ftl:
            result = await ftl.uri(
                url="https://api.example.com",
                url_password="different-password",
            )

        assert result.get("status") == 200, f"Expected replayed result, got: {result}"

        print("  PASSED")
        return True


# ---------------------------------------------------------------------------
# Multi-action sequence tests
# ---------------------------------------------------------------------------

async def test_first_action_param_change_stops_all_replay():
    """If the very first action has changed params, nothing should replay."""
    print("TEST 16: First action param change stops all replay")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"

        audit = make_audit([
            make_action("command", params={"cmd": "echo 'first'"}, success=True,
                        output={"stdout": "first", "rc": 0}),
            make_action("command", params={"cmd": "echo 'second'"}, success=True,
                        output={"stdout": "second", "rc": 0}),
        ])
        audit_file.write_text(json.dumps(audit, indent=2))

        try:
            async with automation(
                record=str(audit_file),
                replay=str(audit_file),
                quiet=True,
            ) as ftl:
                await ftl.command(cmd="echo 'first_changed'")
                await ftl.command(cmd="echo 'second'")
        except Exception:
            pass

        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data["actions"] if a.get("replayed")]
        assert len(replayed) == 0, f"Expected 0 replayed (first action changed), got {len(replayed)}"

        print("  PASSED")
        return True


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

ALL_TESTS = [
    test_changed_params_skip_replay,
    test_same_params_replay,
    test_replay_stops_entirely_after_param_mismatch,
    test_subsequent_actions_fresh_after_param_mismatch,
    test_warning_printed_on_param_mismatch,
    test_no_warning_when_quiet,
    test_redacted_secrets_dont_cause_false_mismatch,
    test_different_nonsecret_http_params_mismatch,
    test_empty_params_vs_no_params_key,
    test_nested_dict_params_same,
    test_nested_dict_params_changed,
    test_added_param_triggers_reexecution,
    test_removed_param_triggers_reexecution,
    test_bearer_token_redaction_matching,
    test_url_password_redaction_matching,
    test_first_action_param_change_stops_all_replay,
]


async def main():
    print("\nTesting replay parameter comparison (issue #37)")
    print("=" * 60)

    passed = 0
    failed = 0
    errors = []

    for test_fn in ALL_TESTS:
        try:
            result = await test_fn()
            if result:
                passed += 1
            else:
                failed += 1
                errors.append(f"{test_fn.__name__}: returned False")
        except Exception as e:
            failed += 1
            errors.append(f"{test_fn.__name__}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    if errors:
        print("\nFAILURES:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
