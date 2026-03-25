"""Tests for become_method in _BECOME_KWARGS (issue #26).

Verifies that become_method is intercepted as a become override
and not passed through as a module parameter.
"""

from ftl2.automation.become import _BECOME_KWARGS, _extract_become_overrides


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
        assert _BECOME_KWARGS >= required


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
