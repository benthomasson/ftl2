"""Tests for become_method in _BECOME_KWARGS (issue #26).

Verifies that become_method is intercepted as a become override
and not passed through as a module parameter.
"""

from ftl2.automation.become import _BECOME_KWARGS, _extract_become_overrides
from ftl2.types import BecomeConfig


class TestBecomeKwargs:
    """Test _BECOME_KWARGS contains all become-related keys."""

    def test_become_method_in_kwargs(self):
        """become_method must be in _BECOME_KWARGS (the actual bug fix)."""
        assert "become_method" in _BECOME_KWARGS

    def test_become_in_kwargs(self):
        assert "become" in _BECOME_KWARGS

    def test_become_user_in_kwargs(self):
        assert "become_user" in _BECOME_KWARGS

    def test_contains_required_keys(self):
        """_BECOME_KWARGS should contain at least the three core become keys."""
        required = {"become", "become_user", "become_method"}
        assert required <= _BECOME_KWARGS


class TestExtractBecomeOverrides:
    """Test _extract_become_overrides separates become kwargs from module params."""

    def test_become_method_extracted(self):
        """become_method should be extracted as an override, not a module param."""
        overrides, params = _extract_become_overrides(
            {"become_method": "doas", "name": "nginx", "state": "started"}
        )
        assert overrides == {"become_method": "doas"}
        assert params == {"name": "nginx", "state": "started"}

    def test_all_become_kwargs_extracted(self):
        overrides, params = _extract_become_overrides(
            {"become": True, "become_user": "admin", "become_method": "su", "path": "/tmp"}
        )
        assert overrides == {"become": True, "become_user": "admin", "become_method": "su"}
        assert params == {"path": "/tmp"}

    def test_no_become_kwargs(self):
        overrides, params = _extract_become_overrides(
            {"name": "httpd", "state": "present"}
        )
        assert overrides == {}
        assert params == {"name": "httpd", "state": "present"}

    def test_empty_kwargs(self):
        overrides, params = _extract_become_overrides({})
        assert overrides == {}
        assert params == {}

    def test_only_become_kwargs(self):
        overrides, params = _extract_become_overrides(
            {"become": True, "become_method": "doas"}
        )
        assert overrides == {"become": True, "become_method": "doas"}
        assert params == {}

    def test_unknown_become_like_key_not_extracted(self):
        """Keys like become_flags should NOT be extracted."""
        overrides, params = _extract_become_overrides(
            {"become_flags": "-H", "name": "test"}
        )
        assert overrides == {}
        assert params == {"become_flags": "-H", "name": "test"}

    def test_does_not_mutate_input(self):
        """_extract_become_overrides should not modify the input dict."""
        original = {"become_method": "doas", "name": "nginx", "state": "started"}
        snapshot = original.copy()
        _extract_become_overrides(original)
        assert original == snapshot


class TestBecomeConfigWithOverrides:
    """Test BecomeConfig.with_overrides applies become_method."""

    def test_override_become_method(self):
        """become_method override should be applied to the new config."""
        cfg = BecomeConfig(become=True, become_user="root", become_method="sudo")
        overridden = cfg.with_overrides(become_method="doas")
        assert overridden.become_method == "doas"
        assert overridden.become is True
        assert overridden.become_user == "root"

    def test_override_all_fields(self):
        cfg = BecomeConfig()
        overridden = cfg.with_overrides(become=True, become_user="admin", become_method="su")
        assert overridden.become is True
        assert overridden.become_user == "admin"
        assert overridden.become_method == "su"

    def test_no_overrides_returns_equivalent(self):
        cfg = BecomeConfig(become=True, become_user="deploy", become_method="doas")
        overridden = cfg.with_overrides()
        assert overridden == cfg

    def test_none_overrides_preserve_originals(self):
        cfg = BecomeConfig(become=True, become_user="deploy", become_method="doas")
        overridden = cfg.with_overrides(become=None, become_user=None, become_method=None)
        assert overridden == cfg
