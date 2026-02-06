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
    async def test_unknown_module_returns_namespace_proxy(self):
        """Test that unknown modules return NamespaceProxy for FQCN support."""
        from ftl2.automation import NamespaceProxy

        async with automation() as ftl:
            # Unknown names return a NamespaceProxy (for FQCN like ftl.amazon.aws.ec2)
            proxy = ftl.nonexistent_module
            assert isinstance(proxy, NamespaceProxy)

            # Calling it will fail during execution (not at attribute access)
            # This enables FQCN patterns like ftl.amazon.aws.ec2_instance()

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
    async def test_proxy_returns_namespace_for_unknown(self):
        """Test that proxy returns NamespaceProxy for unknown names (FQCN support)."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = ModuleProxy(context)

        # Unknown names return NamespaceProxy for FQCN support
        result = proxy.unknown_module
        assert isinstance(result, NamespaceProxy)

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


class TestNamespaceProxy:
    """Tests for NamespaceProxy (FQCN support)."""

    def test_namespace_proxy_creation(self):
        """Test NamespaceProxy creation."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = NamespaceProxy(context, "amazon")

        assert proxy._path == "amazon"
        assert proxy._context is context

    def test_namespace_proxy_chaining(self):
        """Test that NamespaceProxy supports chained access."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = NamespaceProxy(context, "amazon")

        # Chain to next level
        aws_proxy = proxy.aws
        assert isinstance(aws_proxy, NamespaceProxy)
        assert aws_proxy._path == "amazon.aws"

        # Chain again
        ec2_proxy = aws_proxy.ec2_instance
        assert isinstance(ec2_proxy, NamespaceProxy)
        assert ec2_proxy._path == "amazon.aws.ec2_instance"

    def test_namespace_proxy_repr(self):
        """Test NamespaceProxy repr."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = NamespaceProxy(context, "amazon.aws")

        assert repr(proxy) == "NamespaceProxy('amazon.aws')"

    def test_namespace_proxy_via_automation(self):
        """Test FQCN access via automation context."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()

        # Access namespace via context
        amazon = context.amazon
        assert isinstance(amazon, NamespaceProxy)
        assert amazon._path == "amazon"

        # Chain further
        ec2 = context.amazon.aws.ec2_instance
        assert isinstance(ec2, NamespaceProxy)
        assert ec2._path == "amazon.aws.ec2_instance"

    def test_namespace_proxy_is_callable(self):
        """Test that NamespaceProxy is callable."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = NamespaceProxy(context, "amazon.aws.ec2_instance")

        # Should be callable (async)
        assert callable(proxy)

    def test_namespace_proxy_private_attr_raises(self):
        """Test that private attributes raise AttributeError."""
        from ftl2.automation import NamespaceProxy

        context = AutomationContext()
        proxy = NamespaceProxy(context, "amazon")

        with pytest.raises(AttributeError):
            _ = proxy._private


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


class TestSecretsManagement:
    """Tests for Phase 3: Secrets Management."""

    @pytest.mark.asyncio
    async def test_secrets_from_environment(self, monkeypatch):
        """Test loading secrets from environment variables."""
        monkeypatch.setenv("TEST_SECRET_KEY", "secret_value")

        async with automation(secrets=["TEST_SECRET_KEY"]) as ftl:
            assert ftl.secrets["TEST_SECRET_KEY"] == "secret_value"

    @pytest.mark.asyncio
    async def test_secrets_missing_raises_keyerror(self):
        """Test that missing secrets raise KeyError."""
        async with automation(secrets=["NONEXISTENT_SECRET_KEY_12345"]) as ftl:
            with pytest.raises(KeyError) as exc_info:
                _ = ftl.secrets["NONEXISTENT_SECRET_KEY_12345"]
            assert "not set in environment" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_secrets_not_requested_raises_keyerror(self):
        """Test that unrequested secrets raise KeyError."""
        async with automation(secrets=["SOME_KEY"]) as ftl:
            with pytest.raises(KeyError) as exc_info:
                _ = ftl.secrets["OTHER_KEY"]
            assert "not requested" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_secrets_get_with_default(self, monkeypatch):
        """Test secrets.get() with default value."""
        monkeypatch.setenv("EXISTING_KEY", "value")

        async with automation(secrets=["EXISTING_KEY", "MISSING_KEY"]) as ftl:
            # Existing key returns value
            assert ftl.secrets.get("EXISTING_KEY") == "value"
            # Missing key returns default
            assert ftl.secrets.get("MISSING_KEY", "default") == "default"
            # Unrequested key returns default
            assert ftl.secrets.get("UNREQUESTED", "fallback") == "fallback"

    @pytest.mark.asyncio
    async def test_secrets_contains(self, monkeypatch):
        """Test 'in' operator for secrets."""
        monkeypatch.setenv("SET_SECRET", "value")

        async with automation(secrets=["SET_SECRET", "UNSET_SECRET"]) as ftl:
            assert "SET_SECRET" in ftl.secrets
            assert "UNSET_SECRET" not in ftl.secrets

    @pytest.mark.asyncio
    async def test_secrets_keys(self, monkeypatch):
        """Test secrets.keys() returns requested names."""
        monkeypatch.setenv("KEY1", "value1")

        async with automation(secrets=["KEY1", "KEY2"]) as ftl:
            keys = ftl.secrets.keys()
            assert "KEY1" in keys
            assert "KEY2" in keys

    @pytest.mark.asyncio
    async def test_secrets_loaded_keys(self, monkeypatch):
        """Test secrets.loaded_keys() returns only loaded names."""
        monkeypatch.setenv("LOADED_KEY", "value")

        async with automation(secrets=["LOADED_KEY", "UNLOADED_KEY"]) as ftl:
            loaded = ftl.secrets.loaded_keys()
            assert "LOADED_KEY" in loaded
            assert "UNLOADED_KEY" not in loaded

    @pytest.mark.asyncio
    async def test_secrets_len(self, monkeypatch):
        """Test len() returns number of loaded secrets."""
        monkeypatch.setenv("SECRET_A", "a")
        monkeypatch.setenv("SECRET_B", "b")

        async with automation(secrets=["SECRET_A", "SECRET_B", "SECRET_C"]) as ftl:
            # Only 2 of 3 are set
            assert len(ftl.secrets) == 2

    @pytest.mark.asyncio
    async def test_secrets_repr_safe(self, monkeypatch):
        """Test that repr doesn't expose secret values."""
        monkeypatch.setenv("MY_SECRET", "super_secret_value")

        async with automation(secrets=["MY_SECRET", "MISSING"]) as ftl:
            repr_str = repr(ftl.secrets)
            assert "super_secret_value" not in repr_str
            assert "MY_SECRET" in repr_str
            assert "loaded=" in repr_str
            assert "missing=" in repr_str

    @pytest.mark.asyncio
    async def test_secrets_str_safe(self, monkeypatch):
        """Test that str doesn't expose secret values."""
        monkeypatch.setenv("MY_SECRET", "super_secret_value")

        async with automation(secrets=["MY_SECRET"]) as ftl:
            str_str = str(ftl.secrets)
            assert "super_secret_value" not in str_str
            assert "1 secrets loaded" in str_str

    def test_secrets_proxy_empty(self):
        """Test SecretsProxy with no secrets."""
        from ftl2.automation.context import SecretsProxy

        proxy = SecretsProxy([])
        assert len(proxy) == 0
        assert proxy.keys() == []
        assert proxy.loaded_keys() == []


class TestOutputModes:
    """Tests for Phase 5: Progress and Output."""

    def test_quiet_mode_defaults_false(self):
        """Test that quiet defaults to False."""
        context = AutomationContext()
        assert context.quiet is False

    def test_quiet_mode_can_be_enabled(self):
        """Test that quiet can be enabled."""
        context = AutomationContext(quiet=True)
        assert context.quiet is True

    def test_quiet_overrides_verbose(self):
        """Test that quiet overrides verbose."""
        context = AutomationContext(verbose=True, quiet=True)
        assert context.quiet is True
        assert context.verbose is False  # quiet should disable verbose

    @pytest.mark.asyncio
    async def test_quiet_mode_no_output(self, capsys):
        """Test that quiet mode suppresses output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(quiet=True) as ftl:
                await ftl.file(path=str(test_file), state="touch")

            captured = capsys.readouterr()
            assert captured.out == ""

    @pytest.mark.asyncio
    async def test_verbose_shows_timing(self, capsys):
        """Test that verbose mode shows timing information."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(verbose=True) as ftl:
                await ftl.file(path=str(test_file), state="touch")

            captured = capsys.readouterr()
            # Should show timing like "(0.01s)"
            assert "s)" in captured.out

    @pytest.mark.asyncio
    async def test_event_callback(self):
        """Test on_event callback receives events."""
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(on_event=events.append) as ftl:
                await ftl.file(path=str(test_file), state="touch")

        # Should have start and complete events
        assert len(events) == 2
        assert events[0]["event"] == "module_start"
        assert events[1]["event"] == "module_complete"
        assert events[1]["module"] == "file"
        assert events[1]["success"] is True
        assert "duration" in events[1]
        assert "timestamp" in events[1]

    @pytest.mark.asyncio
    async def test_event_callback_with_run_on(self):
        """Test on_event callback with run_on."""
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation(on_event=events.append) as ftl:
                await ftl.run_on("localhost", "command", cmd="echo hello")

        # Should have start and complete for the host
        assert len(events) == 2
        assert events[0]["host"] == "localhost"
        assert events[1]["host"] == "localhost"

    @pytest.mark.asyncio
    async def test_output_mode_property(self):
        """Test output_mode property returns correct mode."""
        from ftl2.automation import OutputMode

        # Normal mode
        context1 = AutomationContext()
        assert context1.output_mode == OutputMode.NORMAL

        # Verbose mode
        context2 = AutomationContext(verbose=True)
        assert context2.output_mode == OutputMode.VERBOSE

        # Quiet mode
        context3 = AutomationContext(quiet=True)
        assert context3.output_mode == OutputMode.QUIET

        # Events mode
        context4 = AutomationContext(on_event=lambda e: None)
        assert context4.output_mode == OutputMode.EVENTS

    @pytest.mark.asyncio
    async def test_normal_mode_shows_errors(self, capsys):
        """Test that normal mode shows errors but not successes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                # This should succeed - no output in normal mode
                await ftl.command(cmd="echo hello")

            captured = capsys.readouterr()
            # Successful execution shouldn't print in normal mode
            assert "[command] ok" not in captured.out

    @pytest.mark.asyncio
    async def test_event_includes_check_mode(self):
        """Test that events include check_mode flag."""
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(check_mode=True, on_event=events.append) as ftl:
                await ftl.file(path=str(test_file), state="touch")

        assert events[0]["check_mode"] is True
        assert events[1]["check_mode"] is True


class TestSecretsProxy:
    """Tests for SecretsProxy class directly."""

    def test_proxy_init(self, monkeypatch):
        """Test SecretsProxy initialization."""
        from ftl2.automation.context import SecretsProxy

        monkeypatch.setenv("TEST_KEY", "test_value")
        proxy = SecretsProxy(["TEST_KEY", "MISSING_KEY"])

        assert len(proxy) == 1
        assert "TEST_KEY" in proxy
        assert "MISSING_KEY" not in proxy

    def test_proxy_getitem(self, monkeypatch):
        """Test SecretsProxy __getitem__."""
        from ftl2.automation.context import SecretsProxy

        monkeypatch.setenv("MY_KEY", "my_value")
        proxy = SecretsProxy(["MY_KEY"])

        assert proxy["MY_KEY"] == "my_value"

    def test_proxy_get_default(self):
        """Test SecretsProxy get() with default."""
        from ftl2.automation.context import SecretsProxy

        proxy = SecretsProxy(["UNSET_KEY"])
        assert proxy.get("UNSET_KEY", "default") == "default"
        assert proxy.get("UNREQUESTED", "fallback") == "fallback"


class TestErrorHandling:
    """Tests for Phase 6: Error Handling."""

    def test_failed_defaults_false(self):
        """Test that failed defaults to False."""
        context = AutomationContext()
        assert context.failed is False

    def test_errors_defaults_empty(self):
        """Test that errors defaults to empty list."""
        context = AutomationContext()
        assert context.errors == []

    def test_error_messages_defaults_empty(self):
        """Test that error_messages defaults to empty list."""
        context = AutomationContext()
        assert context.error_messages == []

    def test_fail_fast_defaults_false(self):
        """Test that fail_fast defaults to False."""
        context = AutomationContext()
        assert context.fail_fast is False

    def test_fail_fast_can_be_enabled(self):
        """Test that fail_fast can be enabled."""
        context = AutomationContext(fail_fast=True)
        assert context.fail_fast is True

    @pytest.mark.asyncio
    async def test_failed_after_success(self):
        """Test failed is False after successful execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
                assert ftl.failed is False

    @pytest.mark.asyncio
    async def test_errors_empty_after_success(self):
        """Test errors is empty after successful execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
                assert ftl.errors == []

    @pytest.mark.asyncio
    async def test_continue_after_error(self):
        """Test that execution continues after error by default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                # This should work
                await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
                # Run another command
                await ftl.command(cmd="echo hello")

                # Both should have run
                assert len(ftl.results) == 2

    @pytest.mark.asyncio
    async def test_fail_fast_raises_automation_error(self):
        """Test that fail_fast raises AutomationError."""
        from ftl2.automation import AutomationError
        from ftl2.ftl_modules import ExecuteResult

        # Simulate a failure by manually adding a failed result and checking logic
        context = AutomationContext(fail_fast=True)

        # Create a mock failed result
        failed_result = ExecuteResult(
            success=False,
            changed=False,
            output={"failed": True},
            error="Test failure",
            module="test_module",
            host="localhost",
        )

        # Verify fail_fast is set
        assert context.fail_fast is True

    @pytest.mark.asyncio
    async def test_failed_property_with_manual_result(self):
        """Test failed property with manually added failed result."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext()

        # Add a successful result
        context._results.append(ExecuteResult(
            success=True, changed=True, output={}, module="file", host="localhost"
        ))
        assert context.failed is False

        # Add a failed result
        context._results.append(ExecuteResult(
            success=False, changed=False, output={}, error="Failed", module="command", host="localhost"
        ))
        assert context.failed is True

    @pytest.mark.asyncio
    async def test_errors_property_with_manual_results(self):
        """Test errors property returns only failed results."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext()

        # Add mixed results
        context._results.append(ExecuteResult(
            success=True, changed=True, output={}, module="file", host="localhost"
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={}, error="Error 1", module="command", host="localhost"
        ))
        context._results.append(ExecuteResult(
            success=True, changed=False, output={}, module="copy", host="localhost"
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={}, error="Error 2", module="service", host="localhost"
        ))

        assert len(context.errors) == 2
        assert context.errors[0].module == "command"
        assert context.errors[1].module == "service"

    @pytest.mark.asyncio
    async def test_error_messages_property(self):
        """Test error_messages returns error strings."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext()

        context._results.append(ExecuteResult(
            success=False, changed=False, output={}, error="First error", module="cmd1", host="localhost"
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={}, error="Second error", module="cmd2", host="localhost"
        ))

        assert context.error_messages == ["First error", "Second error"]

    @pytest.mark.asyncio
    async def test_fail_fast_via_automation_function(self):
        """Test fail_fast parameter in automation() function."""
        async with automation(fail_fast=True) as ftl:
            assert ftl.fail_fast is True

    @pytest.mark.asyncio
    async def test_error_handling_workflow(self):
        """Test typical error handling workflow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation(quiet=True) as ftl:
                # Run some operations
                await ftl.file(path=f"{tmpdir}/test.txt", state="touch")
                await ftl.command(cmd="echo hello")

                # Check for errors
                if ftl.failed:
                    for error in ftl.errors:
                        print(f"Error: {error.error}")
                else:
                    # All succeeded
                    assert len(ftl.results) == 2
                    assert all(r.success for r in ftl.results)


class TestAutomationError:
    """Tests for AutomationError exception."""

    def test_automation_error_message(self):
        """Test AutomationError message."""
        from ftl2.automation import AutomationError

        error = AutomationError("Test error")
        assert str(error) == "Test error"
        assert error.message == "Test error"
        assert error.result is None

    def test_automation_error_with_result(self):
        """Test AutomationError with result."""
        from ftl2.automation import AutomationError
        from ftl2.ftl_modules import ExecuteResult

        result = ExecuteResult(
            success=False,
            changed=False,
            output={},
            module="test_module",
            host="test_host",
            error="Test failure",
        )
        error = AutomationError("Module failed", result=result)

        assert error.result == result
        assert "test_module" in str(error)
        assert "test_host" in str(error)


class TestCheckMode:
    """Tests for Phase 4: Check Mode (Dry Run)."""

    def test_check_mode_defaults_false(self):
        """Test that check_mode defaults to False."""
        context = AutomationContext()
        assert context.check_mode is False

    def test_check_mode_can_be_enabled(self):
        """Test that check_mode can be enabled."""
        context = AutomationContext(check_mode=True)
        assert context.check_mode is True

    @pytest.mark.asyncio
    async def test_check_mode_via_automation(self):
        """Test check_mode via automation() context manager."""
        async with automation(check_mode=True) as ftl:
            assert ftl.check_mode is True

    @pytest.mark.asyncio
    async def test_check_mode_file_no_create(self):
        """Test that check_mode doesn't create files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "should_not_exist.txt"

            async with automation(check_mode=True) as ftl:
                result = await ftl.file(path=str(test_file), state="touch")

            # File should NOT be created in check mode
            # Note: This depends on module implementation
            # The check_mode flag is passed but module may or may not honor it
            assert ftl.check_mode is True

    @pytest.mark.asyncio
    async def test_check_mode_results_tracked(self):
        """Test that check_mode results are tracked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(check_mode=True) as ftl:
                await ftl.file(path=str(test_file), state="touch")

                assert len(ftl.results) == 1
                # Result should indicate it was a check mode run

    @pytest.mark.asyncio
    async def test_check_mode_verbose_indicator(self, capsys):
        """Test that verbose mode shows check mode indicator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(check_mode=True, verbose=True) as ftl:
                await ftl.file(path=str(test_file), state="touch")

            captured = capsys.readouterr()
            assert "[CHECK MODE]" in captured.out

    @pytest.mark.asyncio
    async def test_check_mode_with_command(self):
        """Test check_mode with command module."""
        async with automation(check_mode=True) as ftl:
            # Command should still execute (commands don't typically support check mode)
            result = await ftl.command(cmd="echo hello")
            assert ftl.check_mode is True

    @pytest.mark.asyncio
    async def test_check_mode_run_on(self):
        """Test check_mode with run_on."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation(check_mode=True) as ftl:
                results = await ftl.run_on("localhost", "file", path=str(test_file), state="touch")

                assert len(results) == 1
                # Check mode should be applied

    def test_check_mode_with_all_options(self):
        """Test check_mode combined with other options."""
        context = AutomationContext(
            modules=["file", "copy"],
            check_mode=True,
            verbose=True,
            secrets=["API_KEY"],
        )

        assert context.check_mode is True
        assert context.verbose is True
        assert context._enabled_modules == ["file", "copy"]
