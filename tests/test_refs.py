"""Tests for variable reference system."""

import pytest

from ftl2.refs import Ref, deref, get_nested_value, get_ref_path


class TestRef:
    """Tests for Ref class."""

    def test_create_root_ref(self):
        """Test creating a root reference."""
        ref = Ref(None, "config")
        assert ref._parent is None
        assert ref._name == "config"

    def test_create_child_ref(self):
        """Test creating a child reference."""
        parent = Ref(None, "config")
        child = Ref(parent, "database")
        assert child._parent is parent
        assert child._name == "database"

    def test_attribute_access_creates_ref(self):
        """Test that attribute access creates new Ref."""
        root = Ref(None, "config")
        child = root.database
        assert isinstance(child, Ref)
        assert child._parent is root
        assert child._name == "database"

    def test_attribute_access_is_cached(self):
        """Test that attribute access returns cached Ref."""
        root = Ref(None, "config")
        first = root.database
        second = root.database
        assert first is second

    def test_nested_attribute_access(self):
        """Test deeply nested attribute access."""
        root = Ref(None, "config")
        nested = root.app.database.cluster.primary.host
        assert isinstance(nested, Ref)
        assert nested._name == "host"

    def test_multiple_branches(self):
        """Test creating multiple reference branches."""
        root = Ref(None, "config")
        db_host = root.database.host
        db_port = root.database.port
        web_port = root.web.port

        # All are Ref objects
        assert isinstance(db_host, Ref)
        assert isinstance(db_port, Ref)
        assert isinstance(web_port, Ref)

        # database ref is shared
        assert db_host._parent is db_port._parent

    def test_repr(self):
        """Test string representation."""
        root = Ref(None, "config")
        nested = root.database.host
        assert repr(nested) == "Ref(config.database.host)"


class TestGetRefPath:
    """Tests for get_ref_path function."""

    def test_root_ref_path(self):
        """Test path extraction for root reference."""
        ref = Ref(None, "config")
        path = get_ref_path(ref)
        assert path == ["config"]

    def test_single_level_path(self):
        """Test path extraction for one level."""
        root = Ref(None, "config")
        child = root.database
        path = get_ref_path(child)
        assert path == ["config", "database"]

    def test_nested_path(self):
        """Test path extraction for deeply nested reference."""
        root = Ref(None, "config")
        nested = root.app.database.cluster.host
        path = get_ref_path(nested)
        assert path == ["config", "app", "database", "cluster", "host"]

    def test_path_order(self):
        """Test that path is in correct order."""
        root = Ref(None, "a")
        ref = root.b.c.d
        path = get_ref_path(ref)
        assert path == ["a", "b", "c", "d"]


class TestGetNestedValue:
    """Tests for get_nested_value function."""

    def test_single_level_access(self):
        """Test accessing a single level."""
        data = {"key": "value"}
        result = get_nested_value(data, ["key"])
        assert result == "value"

    def test_nested_access(self):
        """Test accessing nested data."""
        data = {"app": {"database": {"host": "localhost"}}}
        result = get_nested_value(data, ["app", "database", "host"])
        assert result == "localhost"

    def test_empty_path(self):
        """Test empty path returns original data."""
        data = {"key": "value"}
        result = get_nested_value(data, [])
        assert result == data

    def test_partial_path(self):
        """Test accessing partial path returns dict."""
        data = {"app": {"db": {"host": "localhost", "port": 5432}}}
        result = get_nested_value(data, ["app", "db"])
        assert result == {"host": "localhost", "port": 5432}

    def test_missing_key_raises(self):
        """Test that missing key raises KeyError."""
        data = {"app": {"db": {}}}
        with pytest.raises(KeyError):
            get_nested_value(data, ["app", "missing"])

    def test_non_dict_intermediate_raises(self):
        """Test that non-dict intermediate value raises TypeError."""
        data = {"app": "string_value"}
        with pytest.raises(TypeError):
            get_nested_value(data, ["app", "db"])

    def test_various_value_types(self):
        """Test accessing various value types."""
        data = {
            "string": "text",
            "number": 42,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "bool": True,
            "none": None,
        }
        assert get_nested_value(data, ["string"]) == "text"
        assert get_nested_value(data, ["number"]) == 42
        assert get_nested_value(data, ["list"]) == [1, 2, 3]
        assert get_nested_value(data, ["dict"]) == {"nested": "value"}
        assert get_nested_value(data, ["bool"]) is True
        assert get_nested_value(data, ["none"]) is None


class TestDeref:
    """Tests for deref function."""

    def test_deref_literal_value(self):
        """Test that literal values pass through unchanged."""
        assert deref({}, "string") == "string"
        assert deref({}, 42) == 42
        assert deref({}, [1, 2, 3]) == [1, 2, 3]
        assert deref({}, {"key": "value"}) == {"key": "value"}
        assert deref({}, True) is True
        assert deref({}, None) is None

    def test_deref_simple_ref(self):
        """Test dereferencing a simple reference."""
        host = {"config": {"host": "localhost"}}
        ref = Ref(None, "config").host
        result = deref(host, ref)
        assert result == "localhost"

    def test_deref_nested_ref(self):
        """Test dereferencing a nested reference."""
        host = {
            "app": {"database": {"cluster": {"primary": {"host": "db.example.com"}}}}
        }
        ref = Ref(None, "app").database.cluster.primary.host
        result = deref(host, ref)
        assert result == "db.example.com"

    def test_deref_multiple_refs(self):
        """Test dereferencing multiple refs from same data."""
        host = {
            "config": {"database": {"host": "localhost", "port": 5432}, "web": {"port": 8080}}
        }

        db_host = Ref(None, "config").database.host
        db_port = Ref(None, "config").database.port
        web_port = Ref(None, "config").web.port

        assert deref(host, db_host) == "localhost"
        assert deref(host, db_port) == 5432
        assert deref(host, web_port) == 8080

    def test_deref_missing_key(self):
        """Test that missing key raises KeyError."""
        host = {"config": {}}
        ref = Ref(None, "config").missing
        with pytest.raises(KeyError):
            deref(host, ref)

    def test_deref_dict_result(self):
        """Test dereferencing to a dict value."""
        host = {"config": {"database": {"host": "localhost", "port": 5432}}}
        ref = Ref(None, "config").database
        result = deref(host, ref)
        assert result == {"host": "localhost", "port": 5432}


class TestRefIntegration:
    """Integration tests for Ref system."""

    def test_ansible_style_vars(self):
        """Test with Ansible-style host variables."""
        host = {
            "ansible_host": "192.168.1.100",
            "ansible_user": "deploy",
            "app_config": {
                "database": {"host": "db.example.com", "port": 5432, "name": "myapp"},
                "cache": {"host": "redis.example.com", "port": 6379},
            },
        }

        # Create references
        config = Ref(None, "app_config")
        db_host = config.database.host
        db_port = config.database.port
        cache_host = config.cache.host

        # Dereference
        assert deref(host, db_host) == "db.example.com"
        assert deref(host, db_port) == 5432
        assert deref(host, cache_host) == "redis.example.com"

    def test_mixed_values_in_dict(self):
        """Test dereferencing mixed literal and ref values."""
        host = {"config": {"src_dir": "/opt/app", "dest_dir": "/var/app"}}

        # Mix of literals and refs
        config = Ref(None, "config")
        module_args = {
            "src": config.src_dir,
            "dest": config.dest_dir,
            "mode": "0755",  # Literal
            "owner": "deploy",  # Literal
        }

        # Dereference all values
        resolved = {k: deref(host, v) for k, v in module_args.items()}

        assert resolved == {
            "src": "/opt/app",
            "dest": "/var/app",
            "mode": "0755",
            "owner": "deploy",
        }

    def test_ref_reuse_across_hosts(self):
        """Test that same ref works with different host data."""
        # Create reference once
        config = Ref(None, "app_config")
        db_host = config.database.host

        # Use with different hosts
        host1 = {"app_config": {"database": {"host": "db1.example.com"}}}
        host2 = {"app_config": {"database": {"host": "db2.example.com"}}}
        host3 = {"app_config": {"database": {"host": "db3.example.com"}}}

        assert deref(host1, db_host) == "db1.example.com"
        assert deref(host2, db_host) == "db2.example.com"
        assert deref(host3, db_host) == "db3.example.com"

    def test_complex_nested_structure(self):
        """Test with complex nested data structure."""
        host = {
            "deployment": {
                "environments": {
                    "production": {
                        "region": "us-east-1",
                        "vpc": {"id": "vpc-123", "cidr": "10.0.0.0/16"},
                        "subnets": {
                            "public": ["subnet-a", "subnet-b"],
                            "private": ["subnet-c", "subnet-d"],
                        },
                    }
                }
            }
        }

        deploy = Ref(None, "deployment")
        prod = deploy.environments.production
        region = prod.region
        vpc_id = prod.vpc.id
        public_subnets = prod.subnets.public

        assert deref(host, region) == "us-east-1"
        assert deref(host, vpc_id) == "vpc-123"
        assert deref(host, public_subnets) == ["subnet-a", "subnet-b"]
