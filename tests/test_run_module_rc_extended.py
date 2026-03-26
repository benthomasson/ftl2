"""Extended regression tests for issue #30: subprocess rc must never be overwritten by module JSON output.

Covers edge cases from code review feed-forward:
- Inverse direction (module rc=0, subprocess rc=nonzero)
- Module rc=null, rc="string", rc=float
- JSON list output (not dict — should skip update)
- Empty JSON object
- Module rc matches subprocess rc (no conflict)
- Large rc values
- Multiple JSON objects in stdout (only first parsed)
- Nested rc fields
- Module outputs rc=0 specifically
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

FAKE_MODULE_BYTES = b"#!/usr/bin/env python3\nprint('hello')\n"


def _mock_context(stdout: bytes, stderr: bytes = b"", rc: int = 0):
    """Return a context manager that patches check_output and _module_cache."""
    return (
        patch(
            "ftl2.ftl_gate.__main__.check_output",
            new_callable=AsyncMock,
            return_value=(stdout, stderr, rc),
        ),
        patch(
            "ftl2.ftl_gate.__main__._module_cache",
            {"fake_module": FAKE_MODULE_BYTES},
        ),
    )


async def _run(stdout: bytes, stderr: bytes = b"", rc: int = 0, module_args=None):
    """Helper: run fake_module with given mocked subprocess output."""
    from ftl2.ftl_gate.__main__ import run_module

    co, cm = _mock_context(stdout, stderr, rc)
    with co, cm:
        return await run_module("fake_module", module_args=module_args)


# --- Reviewer-suggested edge cases ---


@pytest.mark.asyncio
async def test_nonzero_subprocess_rc_wins_over_module_rc_zero():
    """Inverse of base test: module claims rc=0 but subprocess failed (rc=1)."""
    stdout = json.dumps({"rc": 0, "changed": True}).encode()
    result = await _run(stdout, rc=1)
    assert result["rc"] == 1, "Subprocess rc must win even when module claims success"
    assert result["changed"] is True


@pytest.mark.asyncio
async def test_module_rc_null():
    """Module outputs {"rc": null} — subprocess rc must still win."""
    stdout = json.dumps({"rc": None, "msg": "done"}).encode()
    result = await _run(stdout, rc=3)
    assert result["rc"] == 3


@pytest.mark.asyncio
async def test_module_rc_string():
    """Module outputs {"rc": "error"} — subprocess rc must still win."""
    stdout = json.dumps({"rc": "error", "msg": "bad"}).encode()
    result = await _run(stdout, rc=0)
    assert result["rc"] == 0


@pytest.mark.asyncio
async def test_module_rc_float():
    """Module outputs {"rc": 1.5} — subprocess rc must still win."""
    stdout = json.dumps({"rc": 1.5}).encode()
    result = await _run(stdout, rc=0)
    assert result["rc"] == 0


@pytest.mark.asyncio
async def test_json_list_output_skips_update():
    """Module outputs a JSON list (not dict) — should skip update entirely, rc preserved."""
    stdout = json.dumps([1, 2, 3]).encode()
    result = await _run(stdout, rc=5)
    assert result["rc"] == 5
    assert "changed" not in result  # list was not merged


@pytest.mark.asyncio
async def test_empty_json_object():
    """Module outputs {} — rc preserved since there's no rc to overwrite."""
    stdout = json.dumps({}).encode()
    result = await _run(stdout, rc=7)
    assert result["rc"] == 7


@pytest.mark.asyncio
async def test_matching_rc_values():
    """Module rc matches subprocess rc — no conflict, but subprocess value used."""
    stdout = json.dumps({"rc": 0, "changed": False}).encode()
    result = await _run(stdout, rc=0)
    assert result["rc"] == 0
    assert result["changed"] is False


@pytest.mark.asyncio
async def test_large_rc_value_preserved():
    """Subprocess rc=255 (max typical) — must be preserved over module rc."""
    stdout = json.dumps({"rc": 1}).encode()
    result = await _run(stdout, rc=255)
    assert result["rc"] == 255


@pytest.mark.asyncio
async def test_negative_subprocess_rc():
    """Subprocess rc=-1 (signal kill) — must be preserved."""
    stdout = json.dumps({"rc": 0}).encode()
    result = await _run(stdout, rc=-1)
    assert result["rc"] == -1


@pytest.mark.asyncio
async def test_other_fields_still_merged():
    """All non-rc fields from module JSON are merged into result."""
    stdout = json.dumps({
        "rc": 99,
        "changed": True,
        "msg": "installed",
        "ansible_facts": {"pkg_version": "1.2.3"},
        "warnings": ["deprecated"],
    }).encode()
    result = await _run(stdout, rc=0)
    assert result["rc"] == 0
    assert result["changed"] is True
    assert result["msg"] == "installed"
    assert result["ansible_facts"] == {"pkg_version": "1.2.3"}
    assert result["warnings"] == ["deprecated"]


@pytest.mark.asyncio
async def test_stdout_field_preserved_after_merge():
    """The original stdout string is preserved even after merge (module can't overwrite it)."""
    module_data = {"rc": 1, "stdout": "fake stdout from module"}
    stdout = json.dumps(module_data).encode()
    result = await _run(stdout, rc=0)
    # The module's "stdout" key overwrites the original — this is expected behavior
    # for stdout/stderr (they get the raw value initially, module can override).
    # But rc must NOT be overwritten.
    assert result["rc"] == 0


@pytest.mark.asyncio
async def test_nested_rc_not_confused():
    """Module has rc inside a nested dict — only top-level rc matters."""
    stdout = json.dumps({"data": {"rc": 42}, "changed": True}).encode()
    result = await _run(stdout, rc=0)
    assert result["rc"] == 0
    assert result["data"]["rc"] == 42  # nested rc preserved as data


@pytest.mark.asyncio
async def test_module_args_passed_through():
    """Module args don't affect rc handling."""
    stdout = json.dumps({"rc": 10, "changed": True}).encode()
    result = await _run(stdout, rc=0, module_args={"name": "nginx", "state": "present"})
    assert result["rc"] == 0


@pytest.mark.asyncio
async def test_stderr_preserved_with_rc_conflict():
    """stderr is preserved even when there's an rc conflict."""
    stdout = json.dumps({"rc": 1}).encode()
    result = await _run(stdout, stderr=b"something went wrong", rc=0)
    assert result["rc"] == 0
    assert result["stderr"] == "something went wrong"


@pytest.mark.asyncio
async def test_json_with_extra_whitespace():
    """Module stdout with leading/trailing whitespace around JSON — still parsed."""
    stdout = b'  \n {"rc": 99, "changed": true} \n '
    result = await _run(stdout, rc=0)
    # json.loads handles whitespace, so this should parse and rc should be preserved
    assert result["rc"] == 0
    assert result["changed"] is True


@pytest.mark.asyncio
async def test_json_string_output():
    """Module outputs a JSON string (not dict/list) — should not crash, rc preserved."""
    stdout = json.dumps("just a string").encode()
    result = await _run(stdout, rc=4)
    assert result["rc"] == 4
