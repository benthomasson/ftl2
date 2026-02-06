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


class TestInventoryIntegration:
    """Tests for Phase 2: Inventory Integration."""

    @pytest.mark.asyncio
    async def test_default_localhost_inventory(self):
        """Test that default inventory includes localhost."""
        async with automation() as ftl:
            assert len(ftl.hosts) >= 1
            assert "localhost" in ftl.hosts

    @pytest.mark.asyncio
    async def test_hosts_all_property(self):
        """Test ftl.hosts.all returns all hosts."""
        async with automation() as ftl:
            all_hosts = ftl.hosts.all
            assert len(all_hosts) >= 1

    @pytest.mark.asyncio
    async def test_hosts_groups_property(self):
        """Test ftl.hosts.groups returns group names."""
        async with automation() as ftl:
            groups = ftl.hosts.groups
            assert isinstance(groups, list)

    @pytest.mark.asyncio
    async def test_hosts_keys(self):
        """Test ftl.hosts.keys() returns host names."""
        async with automation() as ftl:
            keys = ftl.hosts.keys()
            assert "localhost" in keys

    @pytest.mark.asyncio
    async def test_hosts_contains(self):
        """Test 'in' operator for hosts."""
        async with automation() as ftl:
            assert "localhost" in ftl.hosts

    @pytest.mark.asyncio
    async def test_hosts_getitem_host(self):
        """Test getting specific host by name."""
        async with automation() as ftl:
            hosts = ftl.hosts["localhost"]
            assert len(hosts) == 1
            assert hosts[0].name == "localhost"

    @pytest.mark.asyncio
    async def test_hosts_getitem_unknown_raises(self):
        """Test that unknown host/group raises KeyError."""
        async with automation() as ftl:
            with pytest.raises(KeyError):
                _ = ftl.hosts["nonexistent"]

    @pytest.mark.asyncio
    async def test_inventory_from_dict(self):
        """Test loading inventory from dict."""
        inv_dict = {
            "webservers": {
                "hosts": {
                    "web01": {"ansible_host": "192.168.1.10"},
                    "web02": {"ansible_host": "192.168.1.11"},
                }
            },
            "databases": {
                "hosts": {
                    "db01": {"ansible_host": "192.168.1.20"},
                }
            },
        }

        context = AutomationContext(inventory=inv_dict)

        assert "webservers" in context.hosts
        assert "databases" in context.hosts
        assert len(context.hosts["webservers"]) == 2
        assert len(context.hosts["databases"]) == 1

    @pytest.mark.asyncio
    async def test_inventory_from_file(self):
        """Test loading inventory from YAML file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text("""
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_port: 22
    web02:
      ansible_host: 192.168.1.11
""")

            context = AutomationContext(inventory=str(inv_file))

            assert "webservers" in context.hosts
            assert len(context.hosts["webservers"]) == 2

    @pytest.mark.asyncio
    async def test_inventory_missing_file_falls_back_to_localhost(self):
        """Test that missing inventory file falls back to localhost."""
        context = AutomationContext(inventory="/nonexistent/path.yml")
        assert "localhost" in context.hosts

    @pytest.mark.asyncio
    async def test_run_on_localhost(self):
        """Test run_on with localhost."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation() as ftl:
                results = await ftl.run_on("localhost", "file", path=str(test_file), state="touch")

            assert len(results) == 1
            assert results[0].success is True
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_run_on_with_host_list(self):
        """Test run_on with list of hosts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation() as ftl:
                # Get localhost as a HostConfig list
                hosts = ftl.hosts["localhost"]
                results = await ftl.run_on(hosts, "file", path=str(test_file), state="touch")

            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_run_on_results_tracked(self):
        """Test that run_on results are tracked in ftl.results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                await ftl.run_on("localhost", "command", cmd="echo hello")

                # Results should include run_on executions
                assert len(ftl.results) >= 1
                assert ftl.results[-1].module == "command"


class TestHostsProxy:
    """Tests for HostsProxy class."""

    def test_hosts_proxy_len(self):
        """Test len() on HostsProxy."""
        from ftl2.automation.context import HostsProxy
        from ftl2.inventory import load_localhost

        inv = load_localhost()
        proxy = HostsProxy(inv)
        assert len(proxy) == 1

    def test_hosts_proxy_iter(self):
        """Test iterating over HostsProxy."""
        from ftl2.automation.context import HostsProxy
        from ftl2.inventory import load_localhost

        inv = load_localhost()
        proxy = HostsProxy(inv)
        host_names = list(proxy)
        assert "localhost" in host_names
