"""Tests for become privilege escalation support (sudo, su, doas)."""

import pytest
from ftl2.types import BecomeConfig, HostConfig, gate_cache_key


class TestBecomeConfig:
    """Tests for the BecomeConfig dataclass."""

    def test_defaults(self):
        bc = BecomeConfig()
        assert bc.become is False
        assert bc.become_user == "root"
        assert bc.become_method == "sudo"

    def test_effective_when_disabled(self):
        bc = BecomeConfig()
        assert bc.effective is False

    def test_effective_when_enabled(self):
        bc = BecomeConfig(become=True)
        assert bc.effective is True

    def test_become_prefix_disabled(self):
        bc = BecomeConfig(become=False)
        assert bc.become_prefix("whoami") == "whoami"

    def test_become_prefix_root(self):
        bc = BecomeConfig(become=True)
        assert bc.become_prefix("whoami") == "sudo -n whoami"

    def test_become_prefix_nonroot_user(self):
        bc = BecomeConfig(become=True, become_user="catbeez")
        assert bc.become_prefix("whoami") == "sudo -n -u catbeez whoami"

    def test_become_prefix_preserves_command(self):
        bc = BecomeConfig(become=True)
        cmd = "/bin/sh -c 'firewall-cmd --reload'"
        assert bc.become_prefix(cmd) == f"sudo -n {cmd}"

    def test_become_prefix_doas_root(self):
        bc = BecomeConfig(become=True, become_method="doas")
        assert bc.become_prefix("whoami") == "doas -n whoami"

    def test_become_prefix_doas_user(self):
        bc = BecomeConfig(become=True, become_method="doas", become_user="catbeez")
        assert bc.become_prefix("whoami") == "doas -n -u catbeez whoami"

    def test_become_prefix_su_root(self):
        bc = BecomeConfig(become=True, become_method="su")
        assert bc.become_prefix("whoami") == "su - root -c whoami"

    def test_become_prefix_su_user(self):
        bc = BecomeConfig(become=True, become_method="su", become_user="catbeez")
        assert bc.become_prefix("whoami") == "su - catbeez -c whoami"

    def test_become_prefix_unsupported_method(self):
        bc = BecomeConfig(become=True, become_method="pbrun")
        with pytest.raises(ValueError, match="Unsupported become_method"):
            bc.become_prefix("whoami")

    def test_with_overrides_become(self):
        bc = BecomeConfig(become=True, become_user="root")
        bc2 = bc.with_overrides(become=False)
        assert bc2.become is False
        assert bc2.become_user == "root"  # kept from original

    def test_with_overrides_user(self):
        bc = BecomeConfig(become=True, become_user="root")
        bc2 = bc.with_overrides(become_user="catbeez")
        assert bc2.become is True  # kept from original
        assert bc2.become_user == "catbeez"

    def test_with_overrides_none_keeps_original(self):
        bc = BecomeConfig(become=True, become_user="admin")
        bc2 = bc.with_overrides(become=None, become_user=None)
        assert bc2.become is True
        assert bc2.become_user == "admin"

    def test_with_overrides_both(self):
        bc = BecomeConfig(become=False, become_user="root")
        bc2 = bc.with_overrides(become=True, become_user="catbeez")
        assert bc2.become is True
        assert bc2.become_user == "catbeez"

    def test_frozen(self):
        bc = BecomeConfig(become=True)
        with pytest.raises(AttributeError):
            bc.become = False  # type: ignore[misc]


class TestHostConfigBecome:
    """Tests for HostConfig become integration."""

    def test_become_config_defaults(self):
        host = HostConfig(name="web01", ansible_host="1.2.3.4")
        bc = host.become_config
        assert bc.become is False
        assert bc.become_user == "root"

    def test_become_config_enabled(self):
        host = HostConfig(
            name="web01",
            ansible_host="1.2.3.4",
            ansible_become=True,
            ansible_become_user="admin",
        )
        bc = host.become_config
        assert bc.become is True
        assert bc.become_user == "admin"

    def test_become_config_with_overrides(self):
        host = HostConfig(
            name="web01",
            ansible_host="1.2.3.4",
            ansible_become=True,
        )
        bc = host.become_config.with_overrides(become_user="catbeez")
        assert bc.become is True
        assert bc.become_user == "catbeez"

    def test_become_config_override_disable(self):
        host = HostConfig(
            name="web01",
            ansible_host="1.2.3.4",
            ansible_become=True,
        )
        bc = host.become_config.with_overrides(become=False)
        assert bc.become is False


class TestExtractBecomeOverrides:
    """Tests for _extract_become_overrides helper."""

    def test_no_become_kwargs(self):
        from ftl2.automation.proxy import _extract_become_overrides

        overrides, params = _extract_become_overrides({
            "name": "nginx",
            "state": "started",
        })
        assert overrides == {}
        assert params == {"name": "nginx", "state": "started"}

    def test_become_only(self):
        from ftl2.automation.proxy import _extract_become_overrides

        overrides, params = _extract_become_overrides({
            "name": "nginx",
            "become": True,
        })
        assert overrides == {"become": True}
        assert params == {"name": "nginx"}

    def test_become_and_user(self):
        from ftl2.automation.proxy import _extract_become_overrides

        overrides, params = _extract_become_overrides({
            "cmd": "whoami",
            "become": True,
            "become_user": "catbeez",
        })
        assert overrides == {"become": True, "become_user": "catbeez"}
        assert params == {"cmd": "whoami"}

    def test_empty_kwargs(self):
        from ftl2.automation.proxy import _extract_become_overrides

        overrides, params = _extract_become_overrides({})
        assert overrides == {}
        assert params == {}


class TestGateCacheKey:
    """Tests for gate_cache_key function."""

    def test_no_become(self):
        assert gate_cache_key("web01") == "web01"

    def test_become_none(self):
        assert gate_cache_key("web01", None) == "web01"

    def test_become_disabled(self):
        bc = BecomeConfig(become=False)
        assert gate_cache_key("web01", bc) == "web01"

    def test_become_root(self):
        bc = BecomeConfig(become=True, become_user="root")
        assert gate_cache_key("web01", bc) == "web01:become=root:method=sudo"

    def test_become_user(self):
        bc = BecomeConfig(become=True, become_user="catbeez")
        assert gate_cache_key("web01", bc) == "web01:become=catbeez:method=sudo"
