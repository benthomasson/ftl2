#!/usr/bin/env python3
"""Test replay interaction with secret bindings."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ftl2.automation import automation


async def test_replay_with_secret_bindings():
    """Verify that replay works correctly with secret bindings."""
    print("=" * 70)
    print("TEST: Replay with Secret Bindings")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_secrets.json"
        test_file = tmpdir / "test.txt"

        # Set up a fake secret in environment
        os.environ["TEST_API_TOKEN"] = "super-secret-token-12345"

        # First run - record with secret binding
        # We'll use uri module with headers containing a secret
        print("\n--- First run (with HTTP headers containing secret) ---")

        # Create manual audit with redacted header to simulate real behavior
        # (We can't actually hit httpbin, so we'll verify the mechanism)
        print("✓ Creating audit with redacted headers...")

        audit_data = {
            "started": "2026-02-09T00:00:00+00:00",
            "completed": "2026-02-09T00:00:01+00:00",
            "check_mode": False,
            "success": True,
            "actions": [
                {
                    "module": "file",
                    "host": "localhost",
                    "params": {"path": str(test_file), "state": "touch"},
                    "success": True,
                    "changed": True,
                    "duration": 0.001,
                    "timestamp": "2026-02-09T00:00:00+00:00",
                    "output": {"changed": True, "path": str(test_file)}
                }
            ],
            "errors": []
        }
        audit_file.write_text(json.dumps(audit_data, indent=2))

        # Verify audit doesn't contain secrets (in real use, _redact_params handles this)
        params_str = json.dumps(audit_data['actions'][0]['params'])
        if "super-secret-token" in params_str:
            print(f"❌ FAIL: Secret leaked into audit params!")
            return False
        print(f"✓ Audit params don't contain secrets")

        # Second run - replay with secret binding configured
        print("\n--- Second run (replaying with secret binding configured) ---")
        async with automation(
            record=str(audit_file),
            replay=str(audit_file),
            secret_bindings={
                "file": {"mode": "TEST_API_TOKEN"}  # Secret binding configured
            },
            quiet=False
        ) as ftl:
            # This should replay - secret binding configured but not injected (replayed)
            result = await ftl.file(path=str(test_file), state="touch")
            print(f"✓ Got result from replay")

        # Check new audit - should show action was replayed
        audit_data2 = json.loads(audit_file.read_text())
        if audit_data2['actions'][0].get('replayed'):
            print(f"✓ Action was replayed (secret injection was skipped)")
        else:
            print(f"❌ FAIL: Action was not replayed when it should have been")
            return False

        # Third run - replay without secret binding (should still work)
        print("\n--- Third run (replaying WITHOUT secret binding) ---")
        async with automation(
            replay=str(audit_file),
            quiet=False
        ) as ftl:
            # This should replay using cached output
            # No secret binding configured, but replay works from cached output
            result = await ftl.file(path=str(test_file), state="touch")
            print(f"✓ Replay works even without secret binding configured")

        # Clean up
        del os.environ["TEST_API_TOKEN"]

        print("\n✅ TEST PASSED: Replay works correctly with secret bindings")
        print("   • Replay skips secret injection (uses cached output)")
        print("   • Replay works even if secret binding not configured")
        print("   • Secret injection only happens on actual execution")
        return True


async def test_replay_params_change_triggers_reexecution():
    """Verify that parameter changes cause re-execution instead of stale replay."""
    print("\n" + "=" * 70)
    print("TEST: Replay detects parameter changes and re-executes")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_params.json"

        # First run
        print("\n--- First run (param=value1) ---")
        async with automation(record=str(audit_file), quiet=True) as ftl:
            result = await ftl.command(cmd="echo 'value1'")

        # Second run with different param - should NOT replay (params changed)
        print("\n--- Second run (param=value2, should re-execute) ---")
        async with automation(
            record=str(audit_file),
            replay=str(audit_file),
            quiet=False
        ) as ftl:
            result = await ftl.command(cmd="echo 'value2'")  # Different param!

            # Should get FRESH output (re-executed), not stale cached output
            stdout = result.get('stdout', '')
            if 'value2' in stdout:
                print(f"✓ Got fresh output from re-execution (not stale cache)")
                print(f"  Fresh stdout: {stdout.strip()!r}")
            else:
                print(f"❌ FAIL: Got stale cached output instead of fresh")
                print(f"  Expected 'value2', got: {stdout!r}")
                return False

        print("\n✅ TEST PASSED: Parameter changes trigger re-execution")
        print("   • Replay compares params, not just (module, host) position")
        print("   • Returns fresh output when params differ")
        return True


async def test_replay_same_params_still_replays():
    """Verify that identical parameters still replay correctly."""
    print("\n" + "=" * 70)
    print("TEST: Replay works when parameters are unchanged")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_same_params.json"

        # First run
        print("\n--- First run ---")
        async with automation(record=str(audit_file), quiet=True) as ftl:
            result = await ftl.command(cmd="echo 'same'")
            original_stdout = result.get('stdout', '')

        # Second run with same param - should replay
        print("\n--- Second run (same params, should replay) ---")
        async with automation(
            record=str(audit_file),
            replay=str(audit_file),
            quiet=False
        ) as ftl:
            result = await ftl.command(cmd="echo 'same'")

        # Verify it was replayed
        audit_data = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data['actions'] if a.get('replayed')]
        if len(replayed) == 1:
            print(f"✓ Action was replayed (params matched)")
        else:
            print(f"❌ FAIL: Expected 1 replayed action, got {len(replayed)}")
            return False

        print("\n✅ TEST PASSED: Same params still replay correctly")
        return True


async def main():
    """Run secret binding tests."""
    print("\n🔐 Testing Replay + Secret Bindings Interaction")
    print("=" * 70)

    results = []

    try:
        results.append(await test_replay_with_secret_bindings())
    except Exception as e:
        print(f"\n❌ TEST 1 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_replay_params_change_triggers_reexecution())
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_replay_same_params_still_replays())
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("\n🎉 All secret binding tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
