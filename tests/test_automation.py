"""Tests for FTL2 automation context manager."""

import tempfile
from pathlib import Path

import pytest

from ftl2 import automation, AutomationContext
from ftl2.automation import ModuleProxy


class TestAutomationContextManager:
    """Tests for the automation() context manager."""

    @pytest.mark.asyncio
    async def test_basic_context_manager(self):
        """Test basic context manager usage."""
        async with automation() as ftl:
            assert isinstance(ftl, AutomationContext)

    @pytest.mark.asyncio
    async def test_file_module_access(self):
        """Test accessing file module via attribute."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation() as ftl:
                result = await ftl.file(path=str(test_file), state="touch")

            assert result["changed"] is True
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_copy_module_access(self):
        """Test accessing copy module via attribute."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("hello")

            async with automation() as ftl:
                result = await ftl.copy(src=str(src), dest=str(dest))

            assert result["changed"] is True
            assert dest.exists()
            assert dest.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_command_module_access(self):
        """Test accessing command module via attribute."""
        async with automation() as ftl:
            result = await ftl.command(cmd="echo hello")

        assert result.get("stdout", "").strip() == "hello"

    @pytest.mark.asyncio
    async def test_unknown_module_raises_attribute_error(self):
        """Test that unknown modules raise AttributeError."""
        async with automation() as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.nonexistent_module()

            assert "nonexistent_module" in str(exc_info.value)
            assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_restricted_modules(self):
        """Test restricting available modules."""
        async with automation(modules=["file"]) as ftl:
            # file should work
            with tempfile.TemporaryDirectory() as tmpdir:
                test_file = Path(tmpdir) / "test.txt"
                result = await ftl.file(path=str(test_file), state="touch")
                assert result["changed"] is True

    @pytest.mark.asyncio
    async def test_restricted_module_raises_on_disabled(self):
        """Test that disabled modules raise AttributeError."""
        async with automation(modules=["file"]) as ftl:
            with pytest.raises(AttributeError) as exc_info:
                await ftl.command(cmd="echo hello")

            assert "command" in str(exc_info.value)
            assert "not enabled" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_verbose_mode(self, capsys):
        """Test verbose mode outputs execution info."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(verbose=True) as ftl:
                await ftl.file(path=str(test_file), state="touch")

            captured = capsys.readouterr()
            assert "[file]" in captured.out
            assert "ok" in captured.out

    @pytest.mark.asyncio
    async def test_results_tracking(self):
        """Test that results are tracked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation() as ftl:
                await ftl.file(path=str(test_file), state="touch")
                await ftl.command(cmd="echo hello")

                assert len(ftl.results) == 2
                assert ftl.results[0].module == "file"
                assert ftl.results[1].module == "command"

    @pytest.mark.asyncio
    async def test_available_modules(self):
        """Test available_modules property."""
        async with automation() as ftl:
            modules = ftl.available_modules
            assert "file" in modules
            assert "copy" in modules
            assert "command" in modules

    @pytest.mark.asyncio
    async def test_available_modules_restricted(self):
        """Test available_modules with restrictions."""
        async with automation(modules=["file", "copy"]) as ftl:
            modules = ftl.available_modules
            assert modules == ["file", "copy"]


class TestModuleProxy:
    """Tests for ModuleProxy class."""

    @pytest.mark.asyncio
    async def test_proxy_returns_callable(self):
        """Test that proxy returns callable for valid module."""
        context = AutomationContext()
        proxy = ModuleProxy(context)

        file_func = proxy.file
        assert callable(file_func)

    @pytest.mark.asyncio
    async def test_proxy_raises_on_unknown(self):
        """Test that proxy raises AttributeError for unknown module."""
        context = AutomationContext()
        proxy = ModuleProxy(context)

        with pytest.raises(AttributeError):
            _ = proxy.unknown_module

    def test_proxy_raises_on_private(self):
        """Test that proxy raises on private attributes."""
        context = AutomationContext()
        proxy = ModuleProxy(context)

        with pytest.raises(AttributeError):
            _ = proxy._private

    @pytest.mark.asyncio
    async def test_proxy_wrapper_has_name(self):
        """Test that wrapper function has proper name."""
        context = AutomationContext()
        proxy = ModuleProxy(context)

        file_func = proxy.file
        assert file_func.__name__ == "file"


class TestAutomationContext:
    """Tests for AutomationContext class."""

    def test_context_init_defaults(self):
        """Test context initialization with defaults."""
        context = AutomationContext()

        assert context.check_mode is False
        assert context.verbose is False
        assert context._enabled_modules is None

    def test_context_init_with_options(self):
        """Test context initialization with options."""
        context = AutomationContext(
            modules=["file", "copy"],
            check_mode=True,
            verbose=True,
        )

        assert context._enabled_modules == ["file", "copy"]
        assert context.check_mode is True
        assert context.verbose is True

    @pytest.mark.asyncio
    async def test_context_manager_protocol(self):
        """Test async context manager protocol."""
        context = AutomationContext()

        async with context as ctx:
            assert ctx is context

    @pytest.mark.asyncio
    async def test_execute_tracks_results(self):
        """Test that execute() tracks results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            context = AutomationContext()
            await context.execute("file", {"path": str(test_file), "state": "touch"})

            assert len(context.results) == 1
            assert context.results[0].success is True


class TestTopLevelImport:
    """Tests for top-level ftl2 import."""

    def test_import_automation_from_ftl2(self):
        """Test that automation can be imported from ftl2."""
        from ftl2 import automation
        assert automation is not None

    def test_import_automation_context_from_ftl2(self):
        """Test that AutomationContext can be imported from ftl2."""
        from ftl2 import AutomationContext
        assert AutomationContext is not None
