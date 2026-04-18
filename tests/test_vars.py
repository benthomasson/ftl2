"""Tests for ftl2.vars module."""

from ftl2.inventory import HostGroup, Inventory
from ftl2.types import HostConfig
from ftl2.vars import (
    HostVariables,
    ValidationResult,
    VariableInfo,
    _format_value,
    collect_host_variables,
    format_all_hosts_json,
    format_all_hosts_text,
    get_all_host_variables,
    get_host_groups,
    validate_variables,
)


def _make_inventory():
    """Build a small inventory for testing."""
    inventory = Inventory()

    web = HostGroup(name="webservers", vars={"http_port": 80, "env": "prod"})
    web.add_host(HostConfig(
        name="web01",
        ansible_host="10.0.0.1",
        ansible_user="admin",
        vars={"role": "frontend"},
    ))
    web.add_host(HostConfig(
        name="web02",
        ansible_host="10.0.0.2",
        vars={"role": "backend"},
    ))
    inventory.add_group(web)

    db = HostGroup(name="databases", vars={"db_port": 5432})
    db.add_host(HostConfig(
        name="db01",
        ansible_host="10.0.0.10",
        vars={"primary": True},
    ))
    inventory.add_group(db)

    return inventory


class TestVariableInfo:
    def test_to_dict(self):
        v = VariableInfo(name="port", value=80, source="group", source_name="web")
        d = v.to_dict()
        assert d["name"] == "port"
        assert d["value"] == 80
        assert d["source"] == "group"
        assert d["source_name"] == "web"


class TestHostVariables:
    def test_get_var_found(self):
        hv = HostVariables(host_name="web01", variables=[
            VariableInfo(name="port", value=80, source="group"),
            VariableInfo(name="role", value="web", source="host"),
        ])
        v = hv.get_var("port")
        assert v is not None
        assert v.value == 80

    def test_get_var_not_found(self):
        hv = HostVariables(host_name="web01", variables=[])
        assert hv.get_var("missing") is None

    def test_to_dict(self):
        hv = HostVariables(
            host_name="web01",
            groups=["webservers"],
            variables=[VariableInfo(name="x", value=1, source="host")],
        )
        d = hv.to_dict()
        assert d["host_name"] == "web01"
        assert d["variable_count"] == 1
        assert d["groups"] == ["webservers"]

    def test_format_text_with_groups(self):
        hv = HostVariables(
            host_name="web01",
            groups=["webservers", "prod"],
            variables=[
                VariableInfo(name="port", value=80, source="group", source_name="webservers"),
                VariableInfo(name="role", value="web", source="host", source_name="web01"),
            ],
        )
        text = hv.format_text()
        assert "web01" in text
        assert "webservers" in text
        assert "port" in text
        assert "role" in text

    def test_format_text_empty(self):
        hv = HostVariables(host_name="web01")
        text = hv.format_text()
        assert "(no variables)" in text


class TestValidationResult:
    def test_valid_by_default(self):
        result = ValidationResult()
        assert result.valid is True

    def test_to_dict(self):
        result = ValidationResult(valid=False, errors=["bad"], missing_vars=["x"])
        d = result.to_dict()
        assert d["valid"] is False
        assert "bad" in d["errors"]
        assert "x" in d["missing_vars"]

    def test_format_text_passed(self):
        result = ValidationResult()
        assert "PASSED" in result.format_text()

    def test_format_text_failed(self):
        result = ValidationResult(
            valid=False,
            errors=["missing var"],
            warnings=["empty value"],
            missing_vars=["api_key"],
            unused_vars=["old_var"],
        )
        text = result.format_text()
        assert "FAILED" in text
        assert "missing var" in text
        assert "empty value" in text
        assert "api_key" in text
        assert "old_var" in text


class TestFormatValue:
    def test_short_string(self):
        assert _format_value("hello") == '"hello"'

    def test_long_string(self):
        result = _format_value("x" * 100)
        assert result.endswith('..."')
        assert len(result) <= 55

    def test_bool(self):
        assert _format_value(True) == "true"
        assert _format_value(False) == "false"

    def test_int(self):
        assert _format_value(42) == "42"

    def test_list(self):
        assert _format_value([1, 2]) == "[1, 2]"

    def test_long_list(self):
        result = _format_value(list(range(100)))
        assert result.endswith("...")


class TestGetHostGroups:
    def test_finds_groups(self):
        inv = _make_inventory()
        groups = get_host_groups(inv, "web01")
        assert "webservers" in groups

    def test_not_in_other_groups(self):
        inv = _make_inventory()
        groups = get_host_groups(inv, "web01")
        assert "databases" not in groups

    def test_unknown_host(self):
        inv = _make_inventory()
        groups = get_host_groups(inv, "nonexistent")
        assert groups == []


class TestCollectHostVariables:
    def test_includes_builtins(self):
        inv = _make_inventory()
        host = inv.get_all_hosts()["web01"]
        hv = collect_host_variables(inv, host)
        assert hv.get_var("ansible_host") is not None
        assert hv.get_var("ansible_host").value == "10.0.0.1"

    def test_includes_group_vars(self):
        inv = _make_inventory()
        host = inv.get_all_hosts()["web01"]
        hv = collect_host_variables(inv, host)
        port = hv.get_var("http_port")
        assert port is not None
        assert port.value == 80
        assert port.source == "group"

    def test_includes_host_vars(self):
        inv = _make_inventory()
        host = inv.get_all_hosts()["web01"]
        hv = collect_host_variables(inv, host)
        role = hv.get_var("role")
        assert role is not None
        assert role.value == "frontend"
        assert role.source == "host"

    def test_host_vars_override_group_vars(self):
        inv = Inventory()
        group = HostGroup(name="web", vars={"port": 80})
        group.add_host(HostConfig(name="h1", ansible_host="1.2.3.4", vars={"port": 8080}))
        inv.add_group(group)

        hv = collect_host_variables(inv, inv.get_all_hosts()["h1"])
        port = hv.get_var("port")
        assert port.value == 8080
        assert port.source == "host"

    def test_tracks_groups(self):
        inv = _make_inventory()
        host = inv.get_all_hosts()["web01"]
        hv = collect_host_variables(inv, host)
        assert "webservers" in hv.groups


class TestValidateVariables:
    def test_all_required_present(self):
        hv = HostVariables(
            host_name="web01",
            variables=[VariableInfo(name="port", value=80, source="host")],
        )
        result = validate_variables(hv, required_vars=["port"])
        assert result.valid

    def test_missing_required(self):
        hv = HostVariables(host_name="web01", variables=[])
        result = validate_variables(hv, required_vars=["api_key"])
        assert not result.valid
        assert "api_key" in result.missing_vars

    def test_empty_value_warning(self):
        hv = HostVariables(
            host_name="web01",
            variables=[VariableInfo(name="token", value="", source="host")],
        )
        result = validate_variables(hv)
        assert result.valid  # warnings don't fail
        assert any("token" in w for w in result.warnings)

    def test_no_required(self):
        hv = HostVariables(host_name="web01", variables=[])
        result = validate_variables(hv)
        assert result.valid


class TestGetAllHostVariables:
    def test_returns_all_hosts(self):
        inv = _make_inventory()
        all_vars = get_all_host_variables(inv)
        assert "web01" in all_vars
        assert "web02" in all_vars
        assert "db01" in all_vars


class TestFormatAllHosts:
    def test_text_empty(self):
        assert format_all_hosts_text({}) == "No hosts found."

    def test_text_with_hosts(self):
        inv = _make_inventory()
        all_vars = get_all_host_variables(inv)
        text = format_all_hosts_text(all_vars)
        assert "web01" in text
        assert "db01" in text
        assert "3 host(s)" in text

    def test_json_format(self):
        inv = _make_inventory()
        all_vars = get_all_host_variables(inv)
        data = format_all_hosts_json(all_vars)
        assert len(data) == 3
        names = {d["host_name"] for d in data}
        assert names == {"web01", "web02", "db01"}
