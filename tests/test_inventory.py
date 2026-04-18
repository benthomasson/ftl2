"""Tests for inventory management."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ftl2.inventory import (
    HostGroup,
    Inventory,
    expand_host_range,
    load_inventory,
    load_inventory_ini,
    load_inventory_json,
    load_inventory_script,
    load_localhost,
    unique_hosts,
)
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


class TestLoadInventoryJson:
    """Tests for load_inventory_json with _meta.hostvars."""

    def test_meta_hostvars_applied_to_hosts(self):
        data = {
            "_meta": {
                "hostvars": {
                    "host1": {"ansible_host": "192.168.1.10", "var1": "value1"},
                    "host2": {"var2": "value2"},
                }
            },
            "webservers": {"hosts": ["host1", "host2"], "vars": {"http_port": 80}},
        }
        inventory = load_inventory_json(data)

        ws = inventory.get_group("webservers")
        assert ws is not None
        host1 = ws.get_host("host1")
        assert host1.ansible_host == "192.168.1.10"
        assert host1.vars == {"var1": "value1"}

        host2 = ws.get_host("host2")
        assert host2.ansible_host == "host2"
        assert host2.vars == {"var2": "value2"}

    def test_group_vars_parsed(self):
        data = {
            "_meta": {"hostvars": {"h1": {}}},
            "app": {"hosts": ["h1"], "vars": {"port": 8080}},
        }
        inventory = load_inventory_json(data)
        assert inventory.get_group("app").vars == {"port": 8080}

    def test_children_list_parsed(self):
        data = {
            "_meta": {"hostvars": {"h1": {}}},
            "all": {"children": ["webservers"]},
            "webservers": {"hosts": ["h1"]},
        }
        inventory = load_inventory_json(data)
        assert "webservers" in inventory.get_group("all").children

    def test_minimum_skeleton_require_hosts_false(self):
        data = {
            "_meta": {"hostvars": {}},
            "all": {"children": ["ungrouped"]},
            "ungrouped": {"children": []},
        }
        inventory = load_inventory_json(data, require_hosts=False)
        assert inventory.get_group("all") is not None
        assert inventory.get_group("ungrouped") is not None

    def test_minimum_skeleton_require_hosts_true_raises(self):
        import pytest

        data = {
            "_meta": {"hostvars": {}},
            "all": {"children": ["ungrouped"]},
            "ungrouped": {"children": []},
        }
        with pytest.raises(ValueError, match="No hosts loaded"):
            load_inventory_json(data, require_hosts=True)


class TestLoadInventoryScript:
    """Tests for load_inventory_script with --host fallback."""

    def _make_mock_run(self, list_output, host_outputs=None):
        """Return a side_effect function for subprocess.run."""
        def mock_run(cmd, **kwargs):
            class Result:
                def __init__(self, stdout):
                    self.stdout = stdout
                    self.returncode = 0

            if cmd[1] == "--list":
                return Result(json.dumps(list_output))
            elif cmd[1] == "--host":
                hostname = cmd[2]
                return Result(json.dumps(host_outputs.get(hostname, {})))
            raise ValueError(f"Unexpected command: {cmd}")

        return mock_run

    def test_meta_present_skips_host_calls(self):
        list_data = {
            "_meta": {"hostvars": {"h1": {"ansible_host": "10.0.0.1"}}},
            "web": {"hosts": ["h1"]},
        }

        with patch("ftl2.inventory.subprocess.run") as mock_run:
            mock_run.side_effect = self._make_mock_run(list_data)
            inventory = load_inventory_script("/fake/script.py")

        assert mock_run.call_count == 1
        assert mock_run.call_args_list[0][0][0][1] == "--list"

        h1 = inventory.get_group("web").get_host("h1")
        assert h1.ansible_host == "10.0.0.1"

    def test_host_fallback_when_meta_absent(self):
        list_data = {
            "web": {"hosts": ["h1", "h2"]},
        }
        host_outputs = {
            "h1": {"ansible_host": "10.0.0.1", "role": "primary"},
            "h2": {"ansible_host": "10.0.0.2"},
        }

        with patch("ftl2.inventory.subprocess.run") as mock_run:
            mock_run.side_effect = self._make_mock_run(list_data, host_outputs)
            inventory = load_inventory_script("/fake/script.py")

        assert mock_run.call_count == 3
        host_calls = [c for c in mock_run.call_args_list if c[0][0][1] == "--host"]
        assert len(host_calls) == 2
        called_hosts = {c[0][0][2] for c in host_calls}
        assert called_hosts == {"h1", "h2"}

        h1 = inventory.get_group("web").get_host("h1")
        assert h1.ansible_host == "10.0.0.1"
        assert h1.vars == {"role": "primary"}

        h2 = inventory.get_group("web").get_host("h2")
        assert h2.ansible_host == "10.0.0.2"

    def test_host_fallback_multiple_groups_deduplicates(self):
        """Same host in two groups only triggers one --host call."""
        list_data = {
            "web": {"hosts": ["shared"]},
            "app": {"hosts": ["shared"]},
        }
        host_outputs = {"shared": {"ansible_host": "10.0.0.5"}}

        with patch("ftl2.inventory.subprocess.run") as mock_run:
            mock_run.side_effect = self._make_mock_run(list_data, host_outputs)
            inventory = load_inventory_script("/fake/script.py")

        host_calls = [c for c in mock_run.call_args_list if c[0][0][1] == "--host"]
        assert len(host_calls) == 1

        assert inventory.get_group("web").get_host("shared").ansible_host == "10.0.0.5"
        assert inventory.get_group("app").get_host("shared").ansible_host == "10.0.0.5"

    def test_host_fallback_empty_host_response(self):
        """--host returning {} still produces a valid host with defaults."""
        list_data = {"grp": {"hosts": ["bare"]}}
        host_outputs = {"bare": {}}

        with patch("ftl2.inventory.subprocess.run") as mock_run:
            mock_run.side_effect = self._make_mock_run(list_data, host_outputs)
            inventory = load_inventory_script("/fake/script.py")

        host = inventory.get_group("grp").get_host("bare")
        assert host is not None
        assert host.ansible_host == "bare"
        assert host.vars == {}

    def test_host_fallback_no_hosts_require_false(self):
        """--list with no hosts and no _meta, require_hosts=False succeeds."""
        list_data = {"all": {"children": ["ungrouped"]}, "ungrouped": {}}

        with patch("ftl2.inventory.subprocess.run") as mock_run:
            mock_run.side_effect = self._make_mock_run(list_data)
            inventory = load_inventory_script("/fake/script.py", require_hosts=False)

        assert mock_run.call_count == 1
        assert inventory.get_all_hosts() == {}

    def test_load_inventory_json_file_format(self):
        """load_inventory auto-detects JSON files and parses _meta.hostvars."""
        data = {
            "_meta": {
                "hostvars": {
                    "host1": {"ansible_host": "192.168.1.10", "var1": "value1"},
                    "host2": {"var2": "value2"},
                }
            },
            "all": {"children": ["ungrouped", "webservers"]},
            "webservers": {"hosts": ["host1", "host2"], "vars": {"http_port": 80}},
        }
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)
            ws = inventory.get_group("webservers")
            assert ws.get_host("host1").ansible_host == "192.168.1.10"
            assert ws.get_host("host1").vars == {"var1": "value1"}
            assert ws.get_host("host2").vars == {"var2": "value2"}
            assert ws.vars == {"http_port": 80}
        finally:
            path.unlink()


# ---------------------------------------------------------------------------
# Host range expansion
# ---------------------------------------------------------------------------


class TestExpandHostRange:
    """Tests for expand_host_range()."""

    def test_numeric_range(self):
        result = expand_host_range("www[01:03].example.com")
        assert result == [
            "www01.example.com",
            "www02.example.com",
            "www03.example.com",
        ]

    def test_numeric_range_leading_zeros(self):
        result = expand_host_range("node[001:003]")
        assert result == ["node001", "node002", "node003"]

    def test_alphabetic_range(self):
        result = expand_host_range("db-[a:f].example.com")
        assert result == [
            "db-a.example.com",
            "db-b.example.com",
            "db-c.example.com",
            "db-d.example.com",
            "db-e.example.com",
            "db-f.example.com",
        ]

    def test_numeric_stride(self):
        result = expand_host_range("www[01:10:3].example.com")
        assert result == [
            "www01.example.com",
            "www04.example.com",
            "www07.example.com",
            "www10.example.com",
        ]

    def test_alpha_stride(self):
        result = expand_host_range("db-[a:f:2].local")
        assert result == ["db-a.local", "db-c.local", "db-e.local"]

    def test_no_range_passthrough(self):
        assert expand_host_range("plain-host") == ["plain-host"]

    def test_multiple_ranges_cartesian(self):
        result = expand_host_range("rack[1:2]-node[a:b]")
        assert result == [
            "rack1-nodea",
            "rack1-nodeb",
            "rack2-nodea",
            "rack2-nodeb",
        ]

    def test_single_value_range(self):
        result = expand_host_range("host[5:5]")
        assert result == ["host5"]

    def test_non_range_brackets_passthrough(self):
        """Brackets without colon are not range syntax — pass through literally."""
        assert expand_host_range("host[tag]") == ["host[tag]"]

    def test_multi_char_alpha_passthrough(self):
        """Multi-character alpha brackets are not valid ranges — pass through."""
        assert expand_host_range("host[tag:value]") == ["host[tag:value]"]

    def test_ipv6_like_passthrough(self):
        """IPv6-like brackets are not valid ranges — pass through."""
        assert expand_host_range("host[2001:db8]") == ["host[2001:db8]"]

    def test_descending_range_empty(self):
        """Descending range produces no values — returns empty list."""
        assert expand_host_range("host[5:1]") == []

    def test_zero_stride_raises(self):
        """Stride of 0 raises ValueError."""
        with pytest.raises(ValueError, match="stride of 0"):
            expand_host_range("host[1:5:0]")

    def test_width_from_longer_side(self):
        """Zero-padding uses the wider of start/end."""
        result = expand_host_range("host[1:003]")
        assert result == ["host001", "host002", "host003"]


class TestHostRangeIntegration:
    """Test host range expansion through YAML inventory loading."""

    def test_yaml_inventory_with_ranges(self, tmp_path):
        inv_file = tmp_path / "inventory.yaml"
        inv_file.write_text(
            "webservers:\n"
            "  hosts:\n"
            "    www[01:03].example.com:\n"
            "      ansible_user: deploy\n"
        )
        inventory = load_inventory(str(inv_file))
        ws = inventory.get_group("webservers")
        assert ws is not None
        hosts = sorted(ws.hosts.keys())
        assert hosts == [
            "www01.example.com",
            "www02.example.com",
            "www03.example.com",
        ]
        for h in ws.hosts.values():
            assert h.ansible_user == "deploy"

    def test_yaml_inventory_alpha_range(self, tmp_path):
        inv_file = tmp_path / "inventory.yaml"
        inv_file.write_text(
            "databases:\n"
            "  hosts:\n"
            "    db-[a:c].internal:\n"
        )
        inventory = load_inventory(str(inv_file))
        db = inventory.get_group("databases")
        assert sorted(db.hosts.keys()) == [
            "db-a.internal",
            "db-b.internal",
            "db-c.internal",
        ]

    def test_mixed_range_and_plain_hosts(self, tmp_path):
        inv_file = tmp_path / "inventory.yaml"
        inv_file.write_text(
            "cluster:\n"
            "  hosts:\n"
            "    node[1:3]:\n"
            "    bastion:\n"
        )
        inventory = load_inventory(str(inv_file))
        grp = inventory.get_group("cluster")
        assert sorted(grp.hosts.keys()) == [
            "bastion", "node1", "node2", "node3",
        ]


class TestLoadInventoryIni:
    """Tests for INI inventory format parsing."""

    def test_full_example_from_issue(self):
        content = """\
mail.example.com

[webservers]
foo.example.com
bar.example.com http_port=80 maxRequestsPerChild=808

[dbservers]
one.example.com
two.example.com

[dbservers:vars]
ansible_port=5432

[southeast:children]
atlanta
raleigh

[southeast:vars]
some_server=foo.southeast.example.com

[usa:children]
southeast
northeast
"""
        inv = load_inventory_ini(content, require_hosts=True)

        ungrouped = inv.get_group("ungrouped")
        assert ungrouped is not None
        assert "mail.example.com" in ungrouped.hosts

        ws = inv.get_group("webservers")
        assert sorted(ws.hosts.keys()) == ["bar.example.com", "foo.example.com"]

        bar = ws.get_host("bar.example.com")
        assert bar.vars["http_port"] == 80
        assert bar.vars["maxRequestsPerChild"] == 808

        db = inv.get_group("dbservers")
        assert sorted(db.hosts.keys()) == ["one.example.com", "two.example.com"]
        assert db.vars["ansible_port"] == "5432"

        se = inv.get_group("southeast")
        assert set(se.children) == {"atlanta", "raleigh"}
        assert se.vars["some_server"] == "foo.southeast.example.com"

        usa = inv.get_group("usa")
        assert set(usa.children) == {"southeast", "northeast"}

    def test_host_variables_parsed_as_literals(self):
        content = """\
[web]
host1 http_port=80 active=True name="quoted"
"""
        inv = load_inventory_ini(content)
        h = inv.get_group("web").get_host("host1")
        assert h.vars["http_port"] == 80
        assert h.vars["active"] is True
        assert h.vars["name"] == "quoted"

    def test_vars_section_values_are_strings(self):
        content = """\
[db]
db1

[db:vars]
ansible_port=5432
flag=True
"""
        inv = load_inventory_ini(content)
        db = inv.get_group("db")
        assert db.vars["ansible_port"] == "5432"
        assert db.vars["flag"] == "True"

    def test_comments_and_blank_lines(self):
        content = """\
# This is a comment
; This is also a comment

[web]
host1
# another comment
host2
"""
        inv = load_inventory_ini(content)
        ws = inv.get_group("web")
        assert sorted(ws.hosts.keys()) == ["host1", "host2"]

    def test_host_range_expansion(self):
        content = """\
[web]
www[01:03].example.com
"""
        inv = load_inventory_ini(content)
        ws = inv.get_group("web")
        assert sorted(ws.hosts.keys()) == [
            "www01.example.com",
            "www02.example.com",
            "www03.example.com",
        ]

    def test_empty_ini_require_hosts_raises(self):
        content = "# just a comment\n"
        with pytest.raises(ValueError, match="No hosts loaded"):
            load_inventory_ini(content, require_hosts=True)

    def test_empty_ini_require_hosts_false(self):
        content = "# just a comment\n"
        inv = load_inventory_ini(content, require_hosts=False)
        assert inv.get_all_hosts() == {}

    def test_autodetect_ini_extension(self, tmp_path):
        ini_file = tmp_path / "hosts.ini"
        ini_file.write_text("[web]\nhost1\n")
        inv = load_inventory(str(ini_file))
        assert inv.get_group("web").get_host("host1") is not None

    def test_autodetect_ini_content_no_extension(self, tmp_path):
        ini_file = tmp_path / "hosts"
        ini_file.write_text("[web]\nhost1\n")
        inv = load_inventory(str(ini_file))
        assert inv.get_group("web").get_host("host1") is not None

    def test_quoted_values_with_spaces(self):
        content = """\
[app]
server1 description="web server" port=8080
"""
        inv = load_inventory_ini(content)
        h = inv.get_group("app").get_host("server1")
        assert h.vars["description"] == "web server"
        assert h.vars["port"] == 8080

    def test_empty_variable_value(self):
        content = """\
[web]
host1 flag= port=80
"""
        inv = load_inventory_ini(content)
        h = inv.get_group("web").get_host("host1")
        assert h.vars["flag"] == ""
        assert h.vars["port"] == 80
