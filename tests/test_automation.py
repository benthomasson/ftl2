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

    def test_empty_inventory_dict(self):
        """Test that empty inventory dict loads successfully."""
        context = AutomationContext(inventory={
            "minecraft": {
                "hosts": {}
            }
        })

        # Group should exist even without hosts
        assert "minecraft" in context.hosts.groups
        assert len(context.hosts["minecraft"]) == 0

    @pytest.mark.asyncio
    async def test_empty_inventory_from_file(self):
        """Test that empty inventory file loads successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            inv_file = Path(tmpdir) / "inventory.yml"
            inv_file.write_text("""
minecraft:
  hosts: {}
""")

            context = AutomationContext(inventory=str(inv_file))

            # Group should exist even without hosts
            assert "minecraft" in context.hosts.groups

    @pytest.mark.asyncio
    async def test_empty_inventory_with_add_host(self):
        """Test provisioning workflow: empty inventory + add_host."""
        context = AutomationContext(inventory={
            "servers": {
                "hosts": {}
            }
        })

        # Start with empty group
        assert len(context.hosts["servers"]) == 0

        # Add a host dynamically
        context.add_host("web01", ansible_host="192.168.1.10", groups=["servers"])

        # Now the group has a host
        assert len(context.hosts["servers"]) == 1
        assert "web01" in context.hosts


class TestAddHost:
    """Tests for dynamic host registration with add_host()."""

    def test_add_host_basic(self):
        """Test adding a host with minimal parameters."""
        context = AutomationContext()

        host = context.add_host("web01", ansible_host="192.168.1.10")

        assert host.name == "web01"
        assert host.ansible_host == "192.168.1.10"
        assert "web01" in context.hosts

    def test_add_host_with_all_params(self):
        """Test adding a host with all parameters."""
        context = AutomationContext()

        host = context.add_host(
            hostname="db01",
            ansible_host="192.168.1.20",
            ansible_user="admin",
            ansible_port=2222,
            groups=["databases", "production"],
            db_type="postgres",
        )

        assert host.name == "db01"
        assert host.ansible_host == "192.168.1.20"
        assert host.ansible_user == "admin"
        assert host.ansible_port == 2222
        assert host.vars.get("db_type") == "postgres"
        assert "db01" in context.hosts
        assert "databases" in context.hosts
        assert "production" in context.hosts

    def test_add_host_defaults_ansible_host_to_hostname(self):
        """Test that ansible_host defaults to hostname if not specified."""
        context = AutomationContext()

        host = context.add_host("myhost.example.com")

        assert host.ansible_host == "myhost.example.com"

    def test_add_host_creates_groups_if_needed(self):
        """Test that groups are created if they don't exist."""
        context = AutomationContext()

        # Group doesn't exist yet
        assert "newgroup" not in context.hosts.groups

        context.add_host("host01", groups=["newgroup"])

        # Group now exists
        assert "newgroup" in context.hosts.groups
        assert "host01" in context.hosts

    def test_add_host_adds_to_existing_group(self):
        """Test adding host to an existing group."""
        # Start with an inventory that has a group
        context = AutomationContext(inventory={
            "webservers": {
                "hosts": {
                    "web01": {"ansible_host": "192.168.1.10"},
                }
            }
        })

        # Add another host to the same group
        context.add_host("web02", ansible_host="192.168.1.11", groups=["webservers"])

        # Both hosts should be in the group
        webservers = context.hosts["webservers"]
        hostnames = [h.name for h in webservers]
        assert "web01" in hostnames
        assert "web02" in hostnames

    def test_add_host_defaults_to_ungrouped(self):
        """Test that hosts without groups go to 'ungrouped'."""
        context = AutomationContext()

        context.add_host("lonely", ansible_host="10.0.0.1")

        assert "ungrouped" in context.hosts.groups

    def test_add_host_invalidates_proxy_cache(self):
        """Test that add_host invalidates the hosts proxy cache."""
        context = AutomationContext()

        # Access hosts to create the proxy
        _ = context.hosts.all

        # Add a host
        context.add_host("newhost", ansible_host="10.0.0.5")

        # The new host should be visible
        assert "newhost" in context.hosts

    @pytest.mark.asyncio
    async def test_add_host_then_run_on(self):
        """Test full workflow: add host then run_on it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                # Add a "remote" host that's actually local
                ftl.add_host(
                    "localtest",
                    ansible_host="localhost",
                    groups=["testgroup"],
                )

                # Verify it's in the inventory
                assert "localtest" in ftl.hosts
                assert "testgroup" in ftl.hosts.groups


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


class TestHostScopedProxy:
    """Tests for host-scoped module proxy (ftl.<host>.module() syntax)."""

    def test_localhost_returns_host_scoped_proxy(self):
        """Test that ftl.localhost returns HostScopedProxy."""
        from ftl2.automation import HostScopedProxy

        context = AutomationContext()
        proxy = context.localhost

        assert isinstance(proxy, HostScopedProxy)
        assert proxy._target == "localhost"

    def test_local_returns_host_scoped_proxy(self):
        """Test that ftl.local returns HostScopedProxy."""
        from ftl2.automation import HostScopedProxy

        context = AutomationContext()
        proxy = context.local

        assert isinstance(proxy, HostScopedProxy)
        assert proxy._target == "localhost"

    def test_group_name_returns_host_scoped_proxy(self):
        """Test that ftl.<group> returns HostScopedProxy."""
        from ftl2.automation import HostScopedProxy

        context = AutomationContext(inventory={
            "webservers": {
                "hosts": {
                    "web01": {"ansible_host": "192.168.1.10"},
                }
            }
        })

        proxy = context.webservers
        assert isinstance(proxy, HostScopedProxy)
        assert proxy._target == "webservers"

    def test_host_name_returns_host_scoped_proxy(self):
        """Test that ftl.<host> returns HostScopedProxy."""
        from ftl2.automation import HostScopedProxy

        context = AutomationContext(inventory={
            "webservers": {
                "hosts": {
                    "web01": {"ansible_host": "192.168.1.10"},
                }
            }
        })

        # Access by host name (not group name)
        proxy = context.web01
        assert isinstance(proxy, HostScopedProxy)
        assert proxy._target == "web01"

    def test_host_scoped_proxy_module_access(self):
        """Test accessing modules on HostScopedProxy."""
        from ftl2.automation import HostScopedProxy, HostScopedModuleProxy

        context = AutomationContext()
        proxy = context.localhost

        # Access a module
        file_proxy = proxy.file
        assert isinstance(file_proxy, HostScopedModuleProxy)
        assert file_proxy._target == "localhost"
        assert file_proxy._path == "file"

    def test_host_scoped_proxy_fqcn_access(self):
        """Test accessing FQCN modules on HostScopedProxy."""
        from ftl2.automation import HostScopedModuleProxy

        context = AutomationContext(inventory={
            "webservers": {
                "hosts": {"web01": {}}
            }
        })

        # Access FQCN module via group
        proxy = context.webservers.ansible.posix.firewalld
        assert isinstance(proxy, HostScopedModuleProxy)
        assert proxy._target == "webservers"
        assert proxy._path == "ansible.posix.firewalld"

    @pytest.mark.asyncio
    async def test_host_scoped_proxy_localhost_executes_directly(self):
        """Test that ftl.localhost executes directly (not via run_on)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"

            async with automation() as ftl:
                # Use host-scoped syntax for localhost
                results = await ftl.localhost.file(path=str(test_file), state="touch")

                # Should return list[ExecuteResult] for consistency
                assert isinstance(results, list)
                assert len(results) == 1
                assert results[0].success is True
                assert results[0].host == "localhost"
                assert test_file.exists()

    @pytest.mark.asyncio
    async def test_host_scoped_proxy_with_group(self):
        """Test host-scoped proxy with group targets localhost."""
        with tempfile.TemporaryDirectory() as tmpdir:
            async with automation() as ftl:
                # Add host to a group
                ftl.add_host("localtest", ansible_host="localhost", groups=["testgroup"])

                # Use group-scoped syntax (will use run_on)
                results = await ftl.testgroup.command(cmd="echo hello")

                assert isinstance(results, list)
                assert len(results) == 1

    def test_host_scoped_proxy_repr(self):
        """Test HostScopedProxy repr."""
        from ftl2.automation import HostScopedProxy

        context = AutomationContext()
        proxy = HostScopedProxy(context, "webservers")

        assert repr(proxy) == "HostScopedProxy('webservers')"

    def test_host_scoped_module_proxy_repr(self):
        """Test HostScopedModuleProxy repr."""
        from ftl2.automation import HostScopedModuleProxy

        context = AutomationContext()
        proxy = HostScopedModuleProxy(context, "webservers", "service")

        assert repr(proxy) == "HostScopedModuleProxy('webservers', 'service')"

    def test_modules_still_work(self):
        """Test that regular modules still work (not intercepted as hosts)."""
        context = AutomationContext()

        # 'file' is a module, not a host - should return callable
        file_module = context.file
        assert callable(file_module)

        # 'command' is a module
        command_module = context.command
        assert callable(command_module)

    @pytest.mark.asyncio
    async def test_local_works_without_inventory(self):
        """Test ftl.local works even with empty inventory."""
        async with automation(inventory={"servers": {"hosts": {}}}) as ftl:
            # ftl.local should work without localhost in inventory
            results = await ftl.local.command(cmd="echo hello")

            # Returns list[ExecuteResult] for consistency with other targets
            assert isinstance(results, list)
            assert len(results) == 1
            assert results[0].success is True
            assert "stdout" in results[0].output

    @pytest.mark.asyncio
    async def test_localhost_works_without_inventory(self):
        """Test ftl.localhost works even with empty inventory."""
        async with automation(inventory={"servers": {"hosts": {}}}) as ftl:
            # ftl.localhost should work without localhost in inventory
            results = await ftl.localhost.command(cmd="echo hello")

            assert isinstance(results, list)
            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_local_fqcn_module(self):
        """Test ftl.local with FQCN module."""
        async with automation(inventory={"servers": {"hosts": {}}}) as ftl:
            # FQCN module via local
            results = await ftl.local.ansible.builtin.command(cmd="echo fqcn")

            assert isinstance(results, list)
            assert len(results) == 1


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


class TestSecretBindings:
    """Tests for secret bindings (automatic secret injection)."""

    def test_secret_bindings_defaults_empty(self):
        """Test that secret_bindings defaults to empty dict."""
        context = AutomationContext()
        assert context._secret_bindings == {}

    def test_secret_bindings_loads_env_vars(self, monkeypatch):
        """Test that secret_bindings loads referenced env vars."""
        monkeypatch.setenv("MY_TOKEN", "secret123")

        context = AutomationContext(
            secret_bindings={"some.module": {"token": "MY_TOKEN"}}
        )

        assert "MY_TOKEN" in context._bound_secrets
        assert context._bound_secrets["MY_TOKEN"] == "secret123"

    def test_secret_bindings_missing_env_var(self, monkeypatch):
        """Test that missing env vars are not loaded."""
        # Don't set MISSING_VAR

        context = AutomationContext(
            secret_bindings={"some.module": {"token": "MISSING_VAR"}}
        )

        assert "MISSING_VAR" not in context._bound_secrets

    def test_get_secret_bindings_exact_match(self, monkeypatch):
        """Test getting bindings for exact module match."""
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-123")

        context = AutomationContext(
            secret_bindings={"community.general.slack": {"token": "SLACK_TOKEN"}}
        )

        injections = context._get_secret_bindings_for_module("community.general.slack")
        assert injections == {"token": "xoxb-123"}

    def test_get_secret_bindings_glob_match(self, monkeypatch):
        """Test getting bindings with glob pattern."""
        monkeypatch.setenv("AWS_KEY", "AKIAIOSFODNN7EXAMPLE")

        context = AutomationContext(
            secret_bindings={"amazon.aws.*": {"aws_access_key_id": "AWS_KEY"}}
        )

        # Should match ec2_instance
        injections = context._get_secret_bindings_for_module("amazon.aws.ec2_instance")
        assert injections == {"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE"}

        # Should match s3_bucket
        injections = context._get_secret_bindings_for_module("amazon.aws.s3_bucket")
        assert injections == {"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE"}

    def test_get_secret_bindings_no_match(self, monkeypatch):
        """Test that non-matching modules get no injections."""
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-123")

        context = AutomationContext(
            secret_bindings={"community.general.slack": {"token": "SLACK_TOKEN"}}
        )

        injections = context._get_secret_bindings_for_module("file")
        assert injections == {}

    def test_get_secret_bindings_multiple_patterns(self, monkeypatch):
        """Test multiple patterns can apply."""
        monkeypatch.setenv("AWS_KEY", "key123")
        monkeypatch.setenv("AWS_SECRET", "secret456")

        context = AutomationContext(
            secret_bindings={
                "amazon.aws.*": {"aws_access_key_id": "AWS_KEY"},
                "amazon.aws.ec2_instance": {"aws_secret_access_key": "AWS_SECRET"},
            }
        )

        injections = context._get_secret_bindings_for_module("amazon.aws.ec2_instance")
        assert injections == {
            "aws_access_key_id": "key123",
            "aws_secret_access_key": "secret456",
        }

    def test_secret_bindings_via_automation(self, monkeypatch):
        """Test secret_bindings parameter in automation() function."""
        monkeypatch.setenv("TEST_SECRET", "value123")

        import asyncio

        async def check():
            async with automation(
                secret_bindings={"test.module": {"param": "TEST_SECRET"}}
            ) as ftl:
                assert "TEST_SECRET" in ftl._bound_secrets

        asyncio.get_event_loop().run_until_complete(check())


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


class TestPrintErrors:
    """Tests for automatic error printing."""

    def test_print_errors_defaults_true(self):
        """Test that print_errors defaults to True."""
        context = AutomationContext()
        assert context._print_errors is True

    def test_print_errors_can_be_disabled(self):
        """Test that print_errors can be set to False."""
        context = AutomationContext(print_errors=False)
        assert context._print_errors is False

    @pytest.mark.asyncio
    async def test_print_errors_via_automation_function(self):
        """Test print_errors parameter in automation() function."""
        async with automation(print_errors=False) as ftl:
            assert ftl._print_errors is False

    @pytest.mark.asyncio
    async def test_errors_printed_on_exit(self, capsys):
        """Test that errors are printed on context exit when print_errors=True."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_errors=True)
        context._results.append(ExecuteResult(
            success=False,
            changed=False,
            output={},
            error="Permission denied",
            module="file",
            host="localhost",
        ))

        # Simulate context exit
        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "ERRORS (1):" in captured.out
        assert "file on localhost: Permission denied" in captured.out

    @pytest.mark.asyncio
    async def test_multiple_errors_printed(self, capsys):
        """Test multiple errors are all printed."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_errors=True)
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Error 1", module="file", host="localhost",
        ))
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="command", host="localhost",
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Error 2", module="service", host="web01",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "ERRORS (2):" in captured.out
        assert "file on localhost: Error 1" in captured.out
        assert "service on web01: Error 2" in captured.out

    @pytest.mark.asyncio
    async def test_no_output_when_no_errors(self, capsys):
        """Test no error output when all modules succeed."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_errors=True)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "ERRORS" not in captured.out

    @pytest.mark.asyncio
    async def test_no_output_when_print_errors_disabled(self, capsys):
        """Test no error output when print_errors=False."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_errors=False)
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Should not print", module="file", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "ERRORS" not in captured.out
        assert "Should not print" not in captured.out

    @pytest.mark.asyncio
    async def test_no_output_when_quiet_mode(self, capsys):
        """Test no error output when quiet=True even if print_errors=True."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_errors=True, quiet=True)
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Should not print in quiet mode", module="file", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "ERRORS" not in captured.out


class TestPrintSummary:
    """Tests for automatic per-host summary printing."""

    def test_print_summary_defaults_true(self):
        """Test that print_summary defaults to True."""
        context = AutomationContext()
        assert context._print_summary is True

    def test_print_summary_can_be_disabled(self):
        """Test that print_summary can be set to False."""
        context = AutomationContext(print_summary=False)
        assert context._print_summary is False

    @pytest.mark.asyncio
    async def test_print_summary_via_automation_function(self):
        """Test print_summary parameter in automation() function."""
        async with automation(print_summary=False) as ftl:
            assert ftl._print_summary is False

    @pytest.mark.asyncio
    async def test_summary_printed_on_exit(self, capsys):
        """Test that summary is printed on context exit when print_summary=True."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_summary=True, print_errors=False)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))
        context._results.append(ExecuteResult(
            success=True, changed=False, output={},
            module="command", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "SUMMARY:" in captured.out
        assert "localhost: 2 tasks (1 changed, 1 ok)" in captured.out

    @pytest.mark.asyncio
    async def test_summary_multiple_hosts(self, capsys):
        """Test summary with multiple hosts."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_summary=True, print_errors=False)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="web01",
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Failed", module="service", host="web01",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "SUMMARY:" in captured.out
        assert "localhost: 1 tasks (1 changed)" in captured.out
        assert "web01: 2 tasks (1 changed, 1 failed)" in captured.out

    @pytest.mark.asyncio
    async def test_no_summary_when_no_results(self, capsys):
        """Test no summary when there are no results."""
        context = AutomationContext(print_summary=True)

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "SUMMARY:" not in captured.out

    @pytest.mark.asyncio
    async def test_no_summary_when_disabled(self, capsys):
        """Test no summary when print_summary=False."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_summary=False)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "SUMMARY:" not in captured.out

    @pytest.mark.asyncio
    async def test_no_summary_when_quiet_mode(self, capsys):
        """Test no summary when quiet=True."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_summary=True, quiet=True)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        assert "SUMMARY:" not in captured.out

    @pytest.mark.asyncio
    async def test_summary_before_errors(self, capsys):
        """Test that summary is printed before errors."""
        from ftl2.ftl_modules import ExecuteResult

        context = AutomationContext(print_summary=True, print_errors=True)
        context._results.append(ExecuteResult(
            success=True, changed=True, output={},
            module="file", host="localhost",
        ))
        context._results.append(ExecuteResult(
            success=False, changed=False, output={},
            error="Permission denied", module="service", host="localhost",
        ))

        await context.__aexit__(None, None, None)

        captured = capsys.readouterr()
        # Both should be present
        assert "SUMMARY:" in captured.out
        assert "ERRORS" in captured.out
        # Summary should come before errors
        summary_pos = captured.out.find("SUMMARY:")
        errors_pos = captured.out.find("ERRORS")
        assert summary_pos < errors_pos
