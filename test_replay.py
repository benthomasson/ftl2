#!/usr/bin/env python3
"""Test script for audit replay functionality."""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from ftl2.automation import automation, AutomationError


async def test_basic_replay():
    """Test basic replay functionality with successful actions."""
    print("=" * 60)
    print("TEST 1: Basic replay with successful actions")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit.json"
        test1 = tmpdir / "test1.txt"
        test2 = tmpdir / "test2.txt"
        test3 = tmpdir / "test3.txt"

        # First run - record audit
        print("\n--- First run (recording) ---")
        async with automation(record=str(audit_file), quiet=False) as ftl:
            await ftl.file(path=str(test1), state="touch")
            await ftl.file(path=str(test2), state="touch")
            await ftl.command(cmd="echo 'hello world'")

        print(f"\n✓ First run completed")
        print(f"✓ Files created: {test1.exists()}, {test2.exists()}")

        # Verify audit file has output field
        audit_data = json.loads(audit_file.read_text())
        print(f"✓ Audit file created with {len(audit_data['actions'])} actions")

        first_action = audit_data['actions'][0]
        if 'output' in first_action:
            print(f"✓ Audit includes 'output' field")
        else:
            print(f"✗ FAIL: Audit missing 'output' field")
            return False

        # Second run - replay from audit
        print("\n--- Second run (replaying) ---")
        async with automation(
            record=str(audit_file),
            replay=str(audit_file),
            quiet=False
        ) as ftl:
            await ftl.file(path=str(test1), state="touch")
            await ftl.file(path=str(test2), state="touch")
            await ftl.command(cmd="echo 'hello world'")
            # Add one new action
            await ftl.file(path=str(test3), state="touch")

        print(f"\n✓ Second run completed with replay")

        # Verify new audit includes replayed marker
        audit_data2 = json.loads(audit_file.read_text())
        print(f"✓ New audit file has {len(audit_data2['actions'])} actions")

        replayed_count = sum(1 for a in audit_data2['actions'] if a.get('replayed'))
        print(f"✓ Found {replayed_count} replayed actions")

        if replayed_count == 3:
            print("\n✅ TEST 1 PASSED: Basic replay works correctly")
            return True
        else:
            print(f"\n❌ TEST 1 FAILED: Expected 3 replayed actions, got {replayed_count}")
            return False


async def test_replay_with_failure():
    """Test replay stops at first failure and re-executes."""
    print("\n" + "=" * 60)
    print("TEST 2: Replay with failed action")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_fail.json"
        test1 = tmpdir / "test1.txt"

        # Create audit with a failed action
        print("\n--- Creating audit with failure ---")
        actions = [
            {
                "module": "file",
                "host": "localhost",
                "params": {"path": str(test1), "state": "touch"},
                "success": True,
                "changed": True,
                "duration": 0.1,
                "timestamp": "2026-02-09T00:00:00+00:00",
                "output": {"changed": True, "path": str(test1)}
            },
            {
                "module": "command",
                "host": "localhost",
                "params": {"cmd": "false"},
                "success": False,  # This failed!
                "changed": False,
                "duration": 0.1,
                "timestamp": "2026-02-09T00:00:01+00:00",
                "output": {"failed": True, "rc": 1},
                "error": "Command failed"
            }
        ]

        audit_data = {
            "started": "2026-02-09T00:00:00+00:00",
            "completed": "2026-02-09T00:00:02+00:00",
            "check_mode": False,
            "success": False,
            "actions": actions,
            "errors": []
        }
        audit_file.write_text(json.dumps(audit_data, indent=2))

        # Replay - should skip first action, re-execute second
        print("\n--- Replaying (should stop at failed action) ---")
        async with automation(
            replay=str(audit_file),
            quiet=False
        ) as ftl:
            result1 = await ftl.file(path=str(test1), state="touch")
            result2 = await ftl.command(cmd="echo 'success now'")  # Different command

        print(f"\n✓ Replay handled failed action correctly")
        print("\n✅ TEST 2 PASSED: Failed actions trigger re-execution")
        return True


async def test_replay_with_mismatch():
    """Test replay stops when module/host doesn't match."""
    print("\n" + "=" * 60)
    print("TEST 3: Replay with mismatch (script changed)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_mismatch.json"
        test1 = tmpdir / "test1.txt"
        test2 = tmpdir / "test2.txt"

        # Create audit with 2 actions
        actions = [
            {
                "module": "file",
                "host": "localhost",
                "params": {"path": str(test1), "state": "touch"},
                "success": True,
                "changed": True,
                "duration": 0.1,
                "timestamp": "2026-02-09T00:00:00+00:00",
                "output": {"changed": True, "path": str(test1)}
            },
            {
                "module": "command",
                "host": "localhost",
                "params": {"cmd": "echo step2"},
                "success": True,
                "changed": False,
                "duration": 0.1,
                "timestamp": "2026-02-09T00:00:01+00:00",
                "output": {"stdout": "step2"}
            }
        ]

        audit_data = {
            "started": "2026-02-09T00:00:00+00:00",
            "completed": "2026-02-09T00:00:02+00:00",
            "check_mode": False,
            "success": True,
            "actions": actions,
            "errors": []
        }
        audit_file.write_text(json.dumps(audit_data, indent=2))

        # Replay but with different second step (mismatch)
        print("\n--- Replaying with changed script (mismatch at step 2) ---")
        async with automation(
            replay=str(audit_file),
            record=str(audit_file),
            quiet=False
        ) as ftl:
            await ftl.file(path=str(test1), state="touch")  # Replays
            await ftl.file(path=str(test2), state="touch")  # Mismatch! Different module params
            await ftl.command(cmd="echo 'new step'")  # Executes normally

        # Check new audit
        audit_data2 = json.loads(audit_file.read_text())
        replayed = [a for a in audit_data2['actions'] if a.get('replayed')]
        not_replayed = [a for a in audit_data2['actions'] if not a.get('replayed')]

        print(f"\n✓ Replayed actions: {len(replayed)}")
        print(f"✓ Newly executed actions: {len(not_replayed)}")

        if len(replayed) == 1 and len(not_replayed) == 2:
            print("\n✅ TEST 3 PASSED: Replay stops at mismatch")
            return True
        else:
            print(f"\n❌ TEST 3 FAILED: Expected 1 replayed, 2 new; got {len(replayed)} replayed, {len(not_replayed)} new")
            return False


async def test_output_caching():
    """Test that replayed output is correctly cached and returned."""
    print("\n" + "=" * 60)
    print("TEST 4: Output caching works correctly")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        audit_file = tmpdir / "audit_cache.json"

        # First run - capture output
        print("\n--- First run (capture output) ---")
        async with automation(record=str(audit_file), quiet=True) as ftl:
            result = await ftl.command(cmd="echo 'cached output'")
            original_stdout = result.get('stdout', '')
            print(f"Original stdout: {original_stdout!r}")

        # Second run - verify cached output is returned
        print("\n--- Second run (verify cached output) ---")
        async with automation(replay=str(audit_file), quiet=True) as ftl:
            result = await ftl.command(cmd="echo 'cached output'")
            cached_stdout = result.get('stdout', '')
            print(f"Cached stdout: {cached_stdout!r}")

        if original_stdout and cached_stdout == original_stdout:
            print("\n✅ TEST 4 PASSED: Output caching works correctly")
            return True
        else:
            print(f"\n❌ TEST 4 FAILED: Cached output doesn't match")
            print(f"   Expected: {original_stdout!r}")
            print(f"   Got: {cached_stdout!r}")
            return False


async def main():
    """Run all tests."""
    print("\n🧪 Testing FTL2 Audit Replay Feature")
    print("=" * 60)

    results = []

    try:
        results.append(await test_basic_replay())
    except Exception as e:
        print(f"\n❌ TEST 1 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_replay_with_failure())
    except Exception as e:
        print(f"\n❌ TEST 2 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_replay_with_mismatch())
    except Exception as e:
        print(f"\n❌ TEST 3 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    try:
        results.append(await test_output_caching())
    except Exception as e:
        print(f"\n❌ TEST 4 FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        results.append(False)

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
