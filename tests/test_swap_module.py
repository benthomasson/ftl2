"""Tests for the FTL swap module."""

import pytest
from unittest.mock import AsyncMock, patch, mock_open


class TestParseSize:
    """Tests for size string parsing."""

    def test_parse_gigabytes(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("1G") == 1024
        assert parse_size("2G") == 2048
        assert parse_size("0.5G") == 512

    def test_parse_megabytes(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("512M") == 512
        assert parse_size("1024M") == 1024

    def test_parse_kilobytes(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("1024K") == 1

    def test_parse_terabytes(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("1T") == 1024 * 1024

    def test_parse_with_b_suffix(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("1GB") == 1024
        assert parse_size("512MB") == 512

    def test_parse_lowercase(self):
        from ftl2.ftl_modules.swap import parse_size

        assert parse_size("1g") == 1024
        assert parse_size("512m") == 512

    def test_parse_invalid(self):
        from ftl2.ftl_modules.swap import parse_size

        with pytest.raises(ValueError):
            parse_size("invalid")

        with pytest.raises(ValueError):
            parse_size("")


class TestMainFunction:
    """Tests for the main() entry point."""

    @pytest.mark.asyncio
    async def test_main_requires_path(self):
        from ftl2.ftl_modules.swap import main

        result = await main({})

        assert result["failed"] is True
        assert "path is required" in result["msg"]

    @pytest.mark.asyncio
    async def test_main_requires_size_for_present(self):
        from ftl2.ftl_modules.swap import main

        result = await main({"path": "/swapfile", "state": "present"})

        assert result["failed"] is True
        assert "size is required" in result["msg"]

    @pytest.mark.asyncio
    async def test_main_invalid_state(self):
        from ftl2.ftl_modules.swap import main

        result = await main({"path": "/swapfile", "state": "invalid"})

        assert result["failed"] is True
        assert "Invalid state" in result["msg"]

    @pytest.mark.asyncio
    async def test_main_present_calls_swap_present(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "swap_present", new_callable=AsyncMock) as mock:
            mock.return_value = {"changed": True, "path": "/swapfile"}

            result = await swap.main({
                "path": "/swapfile",
                "size": "1G",
                "state": "present",
            })

            mock.assert_called_once_with("/swapfile", "1G", None, True)
            assert result["changed"] is True

    @pytest.mark.asyncio
    async def test_main_absent_calls_swap_absent(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "swap_absent", new_callable=AsyncMock) as mock:
            mock.return_value = {"changed": True, "path": "/swapfile"}

            result = await swap.main({
                "path": "/swapfile",
                "state": "absent",
            })

            mock.assert_called_once_with("/swapfile", True)
            assert result["changed"] is True


class TestSwapPresent:
    """Tests for swap_present function."""

    @pytest.mark.asyncio
    async def test_creates_new_swap_file(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(swap.os.path, "exists", return_value=False), \
             patch.object(swap, "has_swap_signature", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "ensure_fstab_entry", new_callable=AsyncMock, return_value=False):

            mock_run.return_value = ("", "", 0)

            result = await swap.swap_present("/swapfile", "1G")

            assert result["changed"] is True
            # Check dd was called to create the file
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("dd if=/dev/zero" in c for c in calls)
            assert any("chmod 600" in c for c in calls)

    @pytest.mark.asyncio
    async def test_formats_unformatted_swap(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(swap.os.path, "exists", return_value=True), \
             patch.object(swap, "has_swap_signature", new_callable=AsyncMock, return_value=False), \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "ensure_fstab_entry", new_callable=AsyncMock, return_value=False):

            mock_run.return_value = ("", "", 0)

            result = await swap.swap_present("/swapfile", "1G")

            assert result["changed"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("mkswap" in c for c in calls)

    @pytest.mark.asyncio
    async def test_activates_inactive_swap(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(swap.os.path, "exists", return_value=True), \
             patch.object(swap, "has_swap_signature", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=False), \
             patch.object(swap, "ensure_fstab_entry", new_callable=AsyncMock, return_value=False):

            mock_run.return_value = ("", "", 0)

            result = await swap.swap_present("/swapfile", "1G")

            assert result["changed"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("swapon" in c for c in calls)

    @pytest.mark.asyncio
    async def test_idempotent_when_active(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(swap.os.path, "exists", return_value=True), \
             patch.object(swap, "has_swap_signature", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "ensure_fstab_entry", new_callable=AsyncMock, return_value=False):

            result = await swap.swap_present("/swapfile", "1G")

            assert result["changed"] is False
            # run() should not be called for dd, mkswap, or swapon
            mock_run.assert_not_called()


class TestSwapAbsent:
    """Tests for swap_absent function."""

    @pytest.mark.asyncio
    async def test_deactivates_and_removes(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run, \
             patch.object(swap.os.path, "exists", return_value=True), \
             patch.object(swap.os, "remove") as mock_remove, \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=True), \
             patch.object(swap, "remove_fstab_entry", new_callable=AsyncMock, return_value=True):

            mock_run.return_value = ("", "", 0)

            result = await swap.swap_absent("/swapfile")

            assert result["changed"] is True
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("swapoff" in c for c in calls)
            mock_remove.assert_called_once_with("/swapfile")

    @pytest.mark.asyncio
    async def test_idempotent_when_absent(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock), \
             patch.object(swap.os.path, "exists", return_value=False), \
             patch.object(swap, "is_swap_active", new_callable=AsyncMock, return_value=False), \
             patch.object(swap, "remove_fstab_entry", new_callable=AsyncMock, return_value=False):

            result = await swap.swap_absent("/swapfile")

            assert result["changed"] is False


class TestIsSwapActive:
    """Tests for is_swap_active function."""

    @pytest.mark.asyncio
    async def test_active_swap_detected(self):
        from ftl2.ftl_modules import swap

        proc_swaps = """/swapfile  file  1048572  0  -2
"""
        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (proc_swaps, "", 0)

            result = await swap.is_swap_active("/swapfile")

            assert result is True

    @pytest.mark.asyncio
    async def test_inactive_swap_detected(self):
        from ftl2.ftl_modules import swap

        proc_swaps = """Filename  Type  Size  Used  Priority
"""
        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = (proc_swaps, "", 0)

            result = await swap.is_swap_active("/swapfile")

            assert result is False


class TestHasSwapSignature:
    """Tests for has_swap_signature function."""

    @pytest.mark.asyncio
    async def test_valid_signature(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("SWAPSPACE2", "", 0)

            result = await swap.has_swap_signature("/swapfile")

            assert result is True

    @pytest.mark.asyncio
    async def test_no_signature(self):
        from ftl2.ftl_modules import swap

        with patch.object(swap, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = ("", "", 0)

            result = await swap.has_swap_signature("/swapfile")

            assert result is False
