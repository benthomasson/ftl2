"""Tests for argument merging and resolution."""


from ftl2.arguments import ArgumentConfig, has_refs, merge_arguments
from ftl2.refs import Ref
from ftl2.types import HostConfig


class TestArgumentConfig:
    """Tests for ArgumentConfig dataclass."""

    def test_create_empty(self):
        """Test creating an empty ArgumentConfig."""
        config = ArgumentConfig()
        assert config.module_args == {}
        assert config.host_args == {}

    def test_create_with_module_args(self):
        """Test creating ArgumentConfig with module args."""
        config = ArgumentConfig(module_args={"src": "/tmp", "mode": "0755"})
        assert config.module_args == {"src": "/tmp", "mode": "0755"}
        assert config.host_args == {}

    def test_create_with_host_args(self):
        """Test creating ArgumentConfig with host-specific args."""
        config = ArgumentConfig(
            host_args={"web1": {"dest": "/var/www"}, "db1": {"dest": "/var/db"}}
        )
        assert config.module_args == {}
        assert config.host_args == {"web1": {"dest": "/var/www"}, "db1": {"dest": "/var/db"}}

    def test_create_with_both(self):
        """Test creating ArgumentConfig with both types."""
        config = ArgumentConfig(
            module_args={"src": "/tmp"},
            host_args={"web1": {"dest": "/var/www"}},
        )
        assert config.module_args == {"src": "/tmp"}
        assert config.host_args == {"web1": {"dest": "/var/www"}}


class TestHasRefs:
    """Tests for has_refs function."""

    def test_empty_dict(self):
        """Test that empty dict has no refs."""
        assert not has_refs({})

    def test_none(self):
        """Test that None has no refs."""
        assert not has_refs(None)

    def test_literals_only(self):
        """Test that dict with only literals has no refs."""
        args = {"src": "/tmp", "mode": "0755", "count": 42}
        assert not has_refs(args)

    def test_single_ref(self):
        """Test that dict with one ref is detected."""
        config = Ref(None, "config")
        args = {"src": config.src_dir, "mode": "0755"}
        assert has_refs(args)

    def test_multiple_refs(self):
        """Test that dict with multiple refs is detected."""
        config = Ref(None, "config")
        args = {
            "src": config.src_dir,
            "dest": config.dest_dir,
            "mode": "0755",
        }
        assert has_refs(args)

    def test_all_refs(self):
        """Test that dict with all refs is detected."""
        config = Ref(None, "config")
        args = {
            "src": config.src_dir,
            "dest": config.dest_dir,
        }
        assert has_refs(args)


class TestMergeArguments:
    """Tests for merge_arguments function."""

    def test_no_args(self):
        """Test merging with no arguments."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        result = merge_arguments(host, None, None)
        assert result == {}

    def test_module_args_only_literals(self):
        """Test merging with only literal module args."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        module_args = {"src": "/tmp", "mode": "0755"}
        result = merge_arguments(host, module_args, None)
        assert result == {"src": "/tmp", "mode": "0755"}

    def test_module_args_with_refs(self):
        """Test merging with refs in module args."""
        host = HostConfig(
            name="web1",
            ansible_host="192.168.1.100",
            vars={"config": {"src_dir": "/opt/app", "dest_dir": "/var/app"}},
        )
        config = Ref(None, "config")
        module_args = {"src": config.src_dir, "mode": "0755"}
        result = merge_arguments(host, module_args, None)
        assert result == {"src": "/opt/app", "mode": "0755"}

    def test_host_specific_override(self):
        """Test that host-specific args override module args."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        module_args = {"src": "/tmp", "dest": "/var/tmp"}
        host_args = {"web1": {"dest": "/var/www"}}
        result = merge_arguments(host, module_args, host_args)
        assert result == {"src": "/tmp", "dest": "/var/www"}

    def test_host_specific_additional_args(self):
        """Test that host-specific args can add new keys."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        module_args = {"src": "/tmp"}
        host_args = {"web1": {"dest": "/var/www", "owner": "www-data"}}
        result = merge_arguments(host, module_args, host_args)
        assert result == {"src": "/tmp", "dest": "/var/www", "owner": "www-data"}

    def test_refs_with_host_specific_override(self):
        """Test that host-specific args override dereferenced refs."""
        host = HostConfig(
            name="web1",
            ansible_host="192.168.1.100",
            vars={"config": {"dest_dir": "/opt/app"}},
        )
        config = Ref(None, "config")
        module_args = {"src": "/tmp", "dest": config.dest_dir}
        host_args = {"web1": {"dest": "/var/www"}}  # Override the ref
        result = merge_arguments(host, module_args, host_args)
        assert result == {"src": "/tmp", "dest": "/var/www"}

    def test_wrong_host_no_override(self):
        """Test that host-specific args for different host don't apply."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        module_args = {"src": "/tmp", "dest": "/var/tmp"}
        host_args = {"web2": {"dest": "/var/www"}}  # Different host
        result = merge_arguments(host, module_args, host_args)
        assert result == {"src": "/tmp", "dest": "/var/tmp"}

    def test_multiple_refs_resolved(self):
        """Test that multiple refs are all resolved."""
        host = HostConfig(
            name="web1",
            ansible_host="192.168.1.100",
            vars={
                "config": {
                    "src_dir": "/opt/app",
                    "dest_dir": "/var/app",
                    "backup_dir": "/backup",
                }
            },
        )
        config = Ref(None, "config")
        module_args = {
            "src": config.src_dir,
            "dest": config.dest_dir,
            "backup": config.backup_dir,
            "mode": "0755",
        }
        result = merge_arguments(host, module_args, None)
        assert result == {
            "src": "/opt/app",
            "dest": "/var/app",
            "backup": "/backup",
            "mode": "0755",
        }

    def test_nested_ref_resolution(self):
        """Test that deeply nested refs are resolved."""
        host = HostConfig(
            name="web1",
            ansible_host="192.168.1.100",
            vars={
                "deployment": {
                    "app": {"paths": {"config": "/etc/myapp", "data": "/var/myapp"}}
                }
            },
        )
        deploy = Ref(None, "deployment")
        module_args = {
            "config_path": deploy.app.paths.config,
            "data_path": deploy.app.paths.data,
        }
        result = merge_arguments(host, module_args, None)
        assert result == {"config_path": "/etc/myapp", "data_path": "/var/myapp"}

    def test_empty_host_args_dict(self):
        """Test that empty host_args dict works correctly."""
        host = HostConfig(name="web1", ansible_host="192.168.1.100")
        module_args = {"src": "/tmp"}
        host_args = {}  # Empty dict, not None
        result = merge_arguments(host, module_args, host_args)
        assert result == {"src": "/tmp"}

    def test_preserve_original_args(self):
        """Test that original args dicts are not modified."""
        host = HostConfig(
            name="web1",
            ansible_host="192.168.1.100",
            vars={"config": {"src_dir": "/opt/app"}},
        )
        config = Ref(None, "config")
        module_args = {"src": config.src_dir, "mode": "0755"}
        host_args = {"web1": {"dest": "/var/www"}}

        # Make copies to compare later
        original_module_args = module_args.copy()
        original_host_args = host_args.copy()

        result = merge_arguments(host, module_args, host_args)

        # Original dicts should be unchanged
        assert module_args == original_module_args
        assert host_args == original_host_args
        # Result should have all values
        assert result == {"src": "/opt/app", "mode": "0755", "dest": "/var/www"}
