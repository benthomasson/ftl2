"""Regression test for issue #30: module JSON stdout rc field must not overwrite subprocess exit code."""

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_subprocess_rc_takes_precedence_over_module_rc():
    """When module JSON stdout contains an 'rc' field, the subprocess exit code must win.

    Regression test for: https://github.com/.../issues/30
    The bug was that result.update(module_output) would overwrite result["rc"]
    with whatever the module printed, losing the real subprocess exit code.
    """
    from ftl2.ftl_gate.__main__ import run_module

    module_json = json.dumps({"rc": 42, "changed": True, "msg": "hello"})
    fake_stdout = module_json.encode()
    fake_stderr = b""
    subprocess_rc = 0

    with (
        patch(
            "ftl2.ftl_gate.__main__.check_output",
            new_callable=AsyncMock,
            return_value=(fake_stdout, fake_stderr, subprocess_rc),
        ),
        patch(
            "ftl2.ftl_gate.__main__._module_cache",
            {
                "fake_module": b"#!/usr/bin/env python3\nprint('hello')\n",
            },
        ),
    ):
        result = await run_module("fake_module", module_args={"key": "value"})

    # Subprocess exit code must take precedence over module's rc
    assert result["rc"] == subprocess_rc, (
        f"Subprocess rc ({subprocess_rc}) should win over module rc (42), got {result['rc']}"
    )
    # Other module fields should still be merged
    assert result["changed"] is True
    assert result["msg"] == "hello"


@pytest.mark.asyncio
async def test_rc_preserved_when_module_has_no_rc():
    """When module JSON stdout does not contain an 'rc' field, subprocess rc is preserved."""
    from ftl2.ftl_gate.__main__ import run_module

    module_json = json.dumps({"changed": False, "msg": "ok"})
    fake_stdout = module_json.encode()
    fake_stderr = b""
    subprocess_rc = 1

    with (
        patch(
            "ftl2.ftl_gate.__main__.check_output",
            new_callable=AsyncMock,
            return_value=(fake_stdout, fake_stderr, subprocess_rc),
        ),
        patch(
            "ftl2.ftl_gate.__main__._module_cache",
            {
                "fake_module": b"#!/usr/bin/env python3\nprint('ok')\n",
            },
        ),
    ):
        result = await run_module("fake_module")

    assert result["rc"] == subprocess_rc
    assert result["changed"] is False


@pytest.mark.asyncio
async def test_rc_preserved_when_stdout_not_json():
    """When module stdout is not valid JSON, subprocess rc is still preserved."""
    from ftl2.ftl_gate.__main__ import run_module

    fake_stdout = b"not json at all"
    fake_stderr = b"some error"
    subprocess_rc = 2

    with (
        patch(
            "ftl2.ftl_gate.__main__.check_output",
            new_callable=AsyncMock,
            return_value=(fake_stdout, fake_stderr, subprocess_rc),
        ),
        patch(
            "ftl2.ftl_gate.__main__._module_cache",
            {
                "fake_module": b"#!/usr/bin/env python3\nprint('hi')\n",
            },
        ),
    ):
        result = await run_module("fake_module")

    assert result["rc"] == subprocess_rc
    assert result["stdout"] == "not json at all"
    assert result["stderr"] == "some error"
