"""Edge-case tests for recursive nested group hierarchy in YAML inventory."""

import tempfile
from pathlib import Path

from ftl2.inventory import _load_inventory_yaml, load_inventory


class TestNestedInventoryEdgeCases:

    def test_empty_child_groups(self):
        """Empty groups (None data) should be created without hosts or vars."""
        data = {
            "all": {
                "children": {
                    "empty_group": None,
                    "also_empty": None,
                    "has_hosts": {
                        "hosts": {"h1": {"ansible_host": "1.2.3.4"}},
                    },
                }
            }
        }
        inventory = _load_inventory_yaml(data)
        empty = inventory.get_group("empty_group")
        assert empty is not None
        assert len(empty.hosts) == 0
        assert empty.vars == {}

        also_empty = inventory.get_group("also_empty")
        assert also_empty is not None
        assert len(also_empty.hosts) == 0

    def test_children_as_list(self):
        """Children specified as a list (not dict) should be stored but not recursed."""
        data = {
            "parent": {
                "hosts": {"h1": {"ansible_host": "1.2.3.4"}},
                "children": ["child_a", "child_b"],
            }
        }
        inventory = _load_inventory_yaml(data)
        parent = inventory.get_group("parent")
        assert parent is not None
        assert parent.children == ["child_a", "child_b"]
        # List-form children are not recursed into as groups
        assert inventory.get_group("child_a") is None

    def test_require_hosts_false_with_no_hosts(self):
        """require_hosts=False should not raise even with zero hosts in nested groups."""
        data = {
            "all": {
                "children": {
                    "empty_region": {
                        "children": {
                            "empty_city": None,
                        }
                    }
                }
            }
        }
        inventory = _load_inventory_yaml(data, require_hosts=False)
        assert inventory.get_group("all") is not None
        assert inventory.get_group("empty_region") is not None
        assert inventory.get_group("empty_city") is not None
        assert len(inventory.get_all_hosts()) == 0

    def test_host_in_multiple_nested_groups(self):
        """A host appearing in multiple groups should appear in each group's hosts."""
        data = {
            "all": {
                "children": {
                    "webservers": {
                        "hosts": {"shared": {"ansible_host": "10.0.0.1"}},
                    },
                    "monitoring": {
                        "hosts": {"shared": {"ansible_host": "10.0.0.1"}},
                    },
                }
            }
        }
        inventory = _load_inventory_yaml(data)
        assert inventory.get_group("webservers").get_host("shared") is not None
        assert inventory.get_group("monitoring").get_host("shared") is not None
        # Deduplication at inventory level
        assert len(inventory.get_all_hosts()) == 1

    def test_vars_at_every_level(self):
        """Each level should preserve its own vars without merging into children."""
        data = {
            "all": {
                "vars": {"level": "all", "global": True},
                "children": {
                    "region": {
                        "vars": {"level": "region", "region_var": "yes"},
                        "children": {
                            "city": {
                                "vars": {"level": "city"},
                                "hosts": {"h1": {"ansible_host": "1.2.3.4"}},
                            }
                        },
                    }
                },
            }
        }
        inventory = _load_inventory_yaml(data)
        assert inventory.get_group("all").vars == {"level": "all", "global": True}
        assert inventory.get_group("region").vars == {"level": "region", "region_var": "yes"}
        assert inventory.get_group("city").vars == {"level": "city"}

    def test_roundtrip_exporter_format(self):
        """The all.children format (what the exporter outputs) must load correctly."""
        yaml_content = """\
all:
  children:
    webservers:
      hosts:
        web01:
          ansible_host: 10.0.0.1
        web02:
          ansible_host: 10.0.0.2
    databases:
      hosts:
        db01:
          ansible_host: 10.0.0.3
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            path = Path(f.name)

        try:
            inventory = load_inventory(path)
            assert len(inventory.get_all_hosts()) == 3
            assert inventory.get_group("webservers") is not None
            assert inventory.get_group("databases") is not None
            all_group = inventory.get_group("all")
            assert set(all_group.children) == {"webservers", "databases"}
        finally:
            path.unlink()

    def test_dag_group_with_multiple_parents(self):
        """A group can have multiple parents (DAG) — should not raise circular error."""
        data = {
            "all": {
                "children": {
                    "prod": {
                        "children": {
                            "shared_db": {
                                "hosts": {"db1": {"ansible_host": "10.0.0.1"}},
                                "vars": {"db_port": 5432},
                            },
                        }
                    },
                    "staging": {
                        "children": {
                            "shared_db": {
                                "hosts": {"db2": {"ansible_host": "10.0.0.2"}},
                            },
                        }
                    },
                }
            }
        }
        inventory = _load_inventory_yaml(data)
        shared = inventory.get_group("shared_db")
        assert shared is not None
        # Both hosts merged from both parent paths
        assert shared.get_host("db1") is not None
        assert shared.get_host("db2") is not None
        # Vars merged
        assert shared.vars.get("db_port") == 5432
        # Both parent groups list shared_db as child
        assert "shared_db" in inventory.get_group("prod").children
        assert "shared_db" in inventory.get_group("staging").children

    def test_split_definition_top_level_and_child(self):
        """A group defined at top level AND as a child should merge, not lose data."""
        data = {
            "all": {
                "children": {
                    "webservers": None,  # reference only
                }
            },
            "webservers": {
                "hosts": {"web1": {"ansible_host": "10.0.0.1"}},
                "vars": {"http_port": 80},
            },
        }
        inventory = _load_inventory_yaml(data)
        ws = inventory.get_group("webservers")
        assert ws is not None
        assert ws.get_host("web1") is not None
        assert ws.vars.get("http_port") == 80

    def test_deeply_nested_host_reachable(self):
        """Hosts 4+ levels deep should be reachable via get_all_hosts."""
        data = {
            "all": {
                "children": {
                    "l1": {
                        "children": {
                            "l2": {
                                "children": {
                                    "l3": {
                                        "children": {
                                            "l4": {
                                                "hosts": {
                                                    "deep_host": {
                                                        "ansible_host": "10.0.0.99"
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        inventory = _load_inventory_yaml(data)
        hosts = inventory.get_all_hosts()
        assert "deep_host" in hosts
        assert hosts["deep_host"].ansible_host == "10.0.0.99"
        # All intermediate groups should exist
        for name in ("all", "l1", "l2", "l3", "l4"):
            assert inventory.get_group(name) is not None
