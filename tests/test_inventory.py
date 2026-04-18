"""Tests for inventory management."""

import tempfile
from pathlib import Path

from ftl2.inventory import HostGroup, Inventory, load_inventory, load_localhost, unique_hosts
from ftl2.types import HostConfig


class TestHostGroup:
    """Tests for HostGroup dataclass."""

    def test_create_empty_group(self):
        """Test creating an empty host group."""
        group = HostGroup(name="webservers")

        assert group.name == "webservers"
        assert group.hosts == {}
        assert group.vars == {}
        assert group.children == []

    def test_add_host(self):
        """Test adding a host to a group."""
        group = HostGroup(name="webservers")
        host = HostConfig(name="web01", ansible_host="192.168.1.10")

        group.add_host(host)

        assert "web01" in group.hosts
        assert group.hosts["web01"] == host

    def test_get_host(self):
        """Test getting a host by name."""
        group = HostGroup(name="webservers")
        host = HostConfig(name="web01", ansible_host="192.168.1.10")
        group.add_host(host)

        result = group.get_host("web01")
        assert result == host

        result = group.get_host("nonexistent")
        assert result is None

    def test_list_hosts(self):
        """Test listing all hosts in a group."""
        group = HostGroup(name="webservers")
        host1 = HostConfig(name="web01", ansible_host="192.168.1.10")
        host2 = HostConfig(name="web02", ansible_host="192.168.1.11")

        group.add_host(host1)
        group.add_host(host2)

        hosts = group.list_hosts()
        assert len(hosts) == 2
        assert host1 in hosts
        assert host2 in hosts


class TestInventory:
    """Tests for Inventory dataclass."""

    def test_create_empty_inventory(self):
        """Test creating an empty inventory."""
        inventory = Inventory()

        assert inventory.groups == {}
        assert inventory.get_all_hosts() == {}

    def test_add_group(self):
        """Test adding a group to inventory."""
        inventory = Inventory()
        group = HostGroup(name="webservers")

        inventory.add_group(group)

        assert "webservers" in inventory.groups
        assert inventory.groups["webservers"] == group

    def test_get_group(self):
        """Test getting a group by name."""
        inventory = Inventory()
        group = HostGroup(name="webservers")
        inventory.add_group(group)

        result = inventory.get_group("webservers")
        assert result == group

        result = inventory.get_group("nonexistent")
        assert result is None

    def test_list_groups(self):
        """Test listing all groups."""
        inventory = Inventory()
        group1 = HostGroup(name="webservers")
        group2 = HostGroup(name="databases")

        inventory.add_group(group1)
        inventory.add_group(group2)

        groups = inventory.list_groups()
        assert len(groups) == 2
        assert group1 in groups
        assert group2 in groups

    def test_get_all_hosts_single_group(self):
        """Test getting all hosts from a single group."""
        inventory = Inventory()
        group = HostGroup(name="webservers")
        host1 = HostConfig(name="web01", ansible_host="192.168.1.10")
        host2 = HostConfig(name="web02", ansible_host="192.168.1.11")

        group.add_host(host1)
        group.add_host(host2)
        inventory.add_group(group)

        all_hosts = inventory.get_all_hosts()
        assert len(all_hosts) == 2
        assert "web01" in all_hosts
        assert "web02" in all_hosts
        assert all_hosts["web01"] == host1
        assert all_hosts["web02"] == host2

    def test_get_all_hosts_multiple_groups(self):
        """Test getting all hosts from multiple groups."""
        inventory = Inventory()

        web_group = HostGroup(name="webservers")
        web_host = HostConfig(name="web01", ansible_host="192.168.1.10")
        web_group.add_host(web_host)

        db_group = HostGroup(name="databases")
        db_host = HostConfig(name="db01", ansible_host="192.168.1.20")
        db_group.add_host(db_host)

        inventory.add_group(web_group)
        inventory.add_group(db_group)

        all_hosts = inventory.get_all_hosts()
        assert len(all_hosts) == 2
        assert "web01" in all_hosts
        assert "db01" in all_hosts

    def test_get_all_hosts_deduplication(self):
        """Test that duplicate hosts across groups are deduplicated."""
        inventory = Inventory()

        # Same host in multiple groups
        host = HostConfig(name="shared01", ansible_host="192.168.1.50")

        group1 = HostGroup(name="group1")
        group1.add_host(host)

        group2 = HostGroup(name="group2")
        group2.add_host(host)

        inventory.add_group(group1)
        inventory.add_group(group2)

        all_hosts = inventory.get_all_hosts()
        assert len(all_hosts) == 1
        assert "shared01" in all_hosts


class TestLoadInventory:
    """Tests for load_inventory function."""

    def test_load_empty_file(self):
        """Test loading an empty inventory file raises ValueError."""
        import pytest

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")
            f.flush()
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="No hosts loaded from inventory"):
                load_inventory(path)
        finally:
            path.unlink()

    def test_load_simple_inventory(self):
        """Test loading a simple inventory with hosts."""
        yaml_content = """
all:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      ansible_user: admin
    db01:
      ansible_host: 192.168.1.20
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)

            all_group = inventory.get_group("all")
            assert all_group is not None
            assert len(all_group.hosts) == 2

            web01 = all_group.get_host("web01")
            assert web01 is not None
            assert web01.name == "web01"
            assert web01.ansible_host == "192.168.1.10"
            assert web01.ansible_user == "admin"

            db01 = all_group.get_host("db01")
            assert db01 is not None
            assert db01.ansible_host == "192.168.1.20"
        finally:
            path.unlink()

    def test_load_inventory_with_groups(self):
        """Test loading inventory with multiple groups."""
        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
databases:
  hosts:
    db01:
      ansible_host: 192.168.1.20
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)

            assert len(inventory.groups) == 2

            web_group = inventory.get_group("webservers")
            assert web_group is not None
            assert len(web_group.hosts) == 1

            db_group = inventory.get_group("databases")
            assert db_group is not None
            assert len(db_group.hosts) == 1
        finally:
            path.unlink()

    def test_load_inventory_with_vars(self):
        """Test loading inventory with group variables."""
        yaml_content = """
webservers:
  hosts:
    web01:
      ansible_host: 192.168.1.10
  vars:
    http_port: 80
    max_clients: 200
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)
            web_group = inventory.get_group("webservers")

            assert web_group is not None
            assert web_group.vars == {"http_port": 80, "max_clients": 200}
        finally:
            path.unlink()

    def test_load_inventory_with_custom_vars(self):
        """Test that non-ansible_ vars are stored in host.vars."""
        yaml_content = """
all:
  hosts:
    web01:
      ansible_host: 192.168.1.10
      custom_var: value1
      app_port: 8080
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)
            all_group = inventory.get_group("all")
            web01 = all_group.get_host("web01")

            assert web01 is not None
            assert web01.vars == {"custom_var": "value1", "app_port": 8080}
        finally:
            path.unlink()

    def test_load_inventory_with_no_hosts(self):
        """Test loading inventory with groups but no hosts raises ValueError."""
        import pytest

        yaml_content = """
webservers:
  vars:
    http_port: 80
databases:
  vars:
    db_port: 5432
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="No hosts loaded from inventory"):
                load_inventory(path)
        finally:
            path.unlink()

    def test_load_inventory_with_nested_structure(self):
        """Test loading inventory with nested all.children structure."""
        yaml_content = """
all:
  children:
    webservers:
      hosts:
        web01:
          ansible_host: 192.168.1.10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)

            web_group = inventory.get_group("webservers")
            assert web_group is not None
            assert len(web_group.hosts) == 1

            web01 = web_group.get_host("web01")
            assert web01 is not None
            assert web01.ansible_host == "192.168.1.10"

            all_group = inventory.get_group("all")
            assert all_group is not None
            assert "webservers" in all_group.children
        finally:
            path.unlink()

    def test_load_inventory_deeply_nested(self):
        """Test loading inventory with deeply nested group hierarchy."""
        yaml_content = """
all:
  vars:
    global_var: value
  children:
    usa:
      children:
        southeast:
          children:
            atlanta:
              hosts:
                host1:
                  http_port: 80
                host2:
            raleigh:
              hosts:
                host3:
          vars:
            some_server: foo.southeast.example.com
        northeast:
        northwest:
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)

            assert inventory.get_group("all") is not None
            assert inventory.get_group("usa") is not None
            assert inventory.get_group("southeast") is not None
            assert inventory.get_group("atlanta") is not None
            assert inventory.get_group("raleigh") is not None
            assert inventory.get_group("northeast") is not None
            assert inventory.get_group("northwest") is not None

            atlanta = inventory.get_group("atlanta")
            assert len(atlanta.hosts) == 2
            assert atlanta.get_host("host1") is not None
            assert atlanta.get_host("host1").vars == {"http_port": 80}
            assert atlanta.get_host("host2") is not None

            raleigh = inventory.get_group("raleigh")
            assert len(raleigh.hosts) == 1

            southeast = inventory.get_group("southeast")
            assert southeast.vars == {"some_server": "foo.southeast.example.com"}
            assert set(southeast.children) == {"atlanta", "raleigh"}

            usa = inventory.get_group("usa")
            assert set(usa.children) == {"southeast", "northeast", "northwest"}
        finally:
            path.unlink()

    def test_load_inventory_circular_groups(self):
        """Test that circular group references raise ValueError."""
        import pytest

        from ftl2.inventory import _load_inventory_yaml

        data = {
            "parent": {
                "children": {
                    "child": {
                        "children": {
                            "parent": {
                                "hosts": {"h1": {"ansible_host": "1.2.3.4"}}
                            }
                        }
                    }
                }
            }
        }
        with pytest.raises(ValueError, match="Circular group: parent"):
            _load_inventory_yaml(data)


class TestLoadLocalhost:
    """Tests for load_localhost function."""

    def test_load_localhost_default(self):
        """Test loading localhost with default interpreter."""
        inventory = load_localhost()

        all_group = inventory.get_group("all")
        assert all_group is not None

        localhost = all_group.get_host("localhost")
        assert localhost is not None
        assert localhost.name == "localhost"
        assert localhost.ansible_host == "127.0.0.1"
        assert localhost.ansible_connection == "local"
        assert localhost.is_local is True

    def test_load_localhost_custom_interpreter(self):
        """Test loading localhost with custom interpreter."""
        inventory = load_localhost(interpreter="/usr/bin/python3.11")

        all_group = inventory.get_group("all")
        localhost = all_group.get_host("localhost")

        assert localhost is not None
        assert localhost.ansible_python_interpreter == "/usr/bin/python3.11"


class TestUniqueHosts:
    """Tests for unique_hosts function."""

    def test_unique_hosts_single_group(self):
        """Test unique_hosts with a single group."""
        inventory = Inventory()
        group = HostGroup(name="webservers")
        host1 = HostConfig(name="web01", ansible_host="192.168.1.10")
        host2 = HostConfig(name="web02", ansible_host="192.168.1.11")

        group.add_host(host1)
        group.add_host(host2)
        inventory.add_group(group)

        hosts = unique_hosts(inventory)

        assert len(hosts) == 2
        assert "web01" in hosts
        assert "web02" in hosts

    def test_unique_hosts_multiple_groups(self):
        """Test unique_hosts across multiple groups."""
        inventory = Inventory()

        web_group = HostGroup(name="webservers")
        web_host = HostConfig(name="web01", ansible_host="192.168.1.10")
        web_group.add_host(web_host)

        db_group = HostGroup(name="databases")
        db_host = HostConfig(name="db01", ansible_host="192.168.1.20")
        db_group.add_host(db_host)

        inventory.add_group(web_group)
        inventory.add_group(db_group)

        hosts = unique_hosts(inventory)

        assert len(hosts) == 2
        assert "web01" in hosts
        assert "db01" in hosts

    def test_unique_hosts_empty_inventory(self):
        """Test unique_hosts with empty inventory."""
        inventory = Inventory()
        hosts = unique_hosts(inventory)

        assert len(hosts) == 0
