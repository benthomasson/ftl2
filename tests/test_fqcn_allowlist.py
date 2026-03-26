"""Tests for FQCN modules respecting the _enabled_modules allowlist.

Verifies that fully-qualified collection names (e.g. ansible.builtin.shell)
are checked against the allowlist, not just short names.

Closes #34.
"""

import pytest

from ftl2 import automation


class TestFQCNAllowlistBypass:
    """FQCN access must respect the enabled-modules allowlist."""

    @pytest.mark.asyncio
    async def test_fqcn_blocked_by_allowlist(self):
        """FQCN access to a module not in the allowlist raises AttributeError."""
        async with automation(modules=["file", "copy"], print_summary=False, quiet=True) as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.ansible.builtin.shell(cmd="echo pwned")

            assert "not enabled" in str(exc_info.value)
            assert "shell" in str(exc_info.value) or "ansible.builtin.shell" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fqcn_allowed_by_short_name(self):
        """FQCN access works when the short name is in the allowlist."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(modules=["file"], print_summary=False, quiet=True) as ftl:
                # ansible.builtin.file should be allowed because "file" is in the list
                result = await ftl.ansible.builtin.file(path=str(test_file), state="touch")
                assert result["changed"] is True

    @pytest.mark.asyncio
    async def test_fqcn_allowed_by_full_name(self):
        """FQCN access works when the full FQCN is in the allowlist."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(
                modules=["ansible.builtin.file"], print_summary=False, quiet=True
            ) as ftl:
                result = await ftl.ansible.builtin.file(path=str(test_file), state="touch")
                assert result["changed"] is True

    @pytest.mark.asyncio
    async def test_execute_enforces_allowlist(self):
        """Direct execute() calls also enforce the allowlist."""
        async with automation(modules=["file"], print_summary=False, quiet=True) as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.execute("ansible.builtin.shell", {"cmd": "echo pwned"})

            assert "not enabled" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_short_name_still_blocked(self):
        """Short-name access to disabled modules still raises (regression check)."""
        async with automation(modules=["file"], print_summary=False, quiet=True) as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.command(cmd="echo hello")

            assert "command" in str(exc_info.value)
            assert "not enabled" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_restriction_allows_fqcn(self):
        """When no modules restriction is set, FQCN access works normally."""
        async with automation(print_summary=False, quiet=True) as ftl:
            result = await ftl.ansible.builtin.command(cmd="echo hello")
            assert result["stdout"].strip() == "hello"

    @pytest.mark.asyncio
    async def test_multiple_fqcn_blocked(self):
        """Multiple different FQCN bypasses are all blocked."""
        async with automation(modules=["file"], print_summary=False, quiet=True) as ftl:
            for fqcn_attr_chain in [
                lambda: ftl.ansible.builtin.command(cmd="echo pwned"),
                lambda: ftl.ansible.builtin.shell(cmd="echo pwned"),
                lambda: ftl.ansible.builtin.raw(cmd="echo pwned"),
            ]:
                with pytest.raises(AttributeError):
                    await fqcn_attr_chain()

    @pytest.mark.asyncio
    async def test_fqcn_allowlist_allows_short_name_input(self):
        """When allowlist has FQCN entries, short-name input is also allowed.

        e.g. modules=["ansible.builtin.command"] should allow ftl.command().
        """
        async with automation(
            modules=["ansible.builtin.command"], print_summary=False, quiet=True
        ) as ftl:
            result = await ftl.command(cmd="echo symmetric")
            assert result["stdout"].strip() == "symmetric"

    @pytest.mark.asyncio
    async def test_fqcn_allowlist_blocks_other_short_names(self):
        """When allowlist has FQCN entries, other short names are still blocked."""
        async with automation(
            modules=["ansible.builtin.command"], print_summary=False, quiet=True
        ) as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.shell(cmd="echo pwned")

            assert "not enabled" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_available_modules_includes_fqcn_entries(self):
        """available_modules includes FQCN entries when their short name is a known module."""
        async with automation(
            modules=["ansible.builtin.file", "copy"], print_summary=False, quiet=True
        ) as ftl:
            available = ftl.available_modules
            assert "ansible.builtin.file" in available
            assert "copy" in available
