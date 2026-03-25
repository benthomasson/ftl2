"""Tests for become_method in _BECOME_KWARGS (issue #26).

Verifies that become_method is intercepted as a become override
and not passed through as a module parameter.
"""

import importlib.util
import os
import sys

# Load proxy.py directly from this workspace to avoid editable-install conflicts
_proxy_path = os.path.join(
    os.path.dirname(__file__), "..", "src", "ftl2", "automation", "proxy.py"
)
_spec = importlib.util.spec_from_file_location("proxy_under_test", _proxy_path)
_mod = importlib.util.module_from_spec(_spec)

# We need the parent packages minimally available for the TYPE_CHECKING import
# but the actual runtime code only uses _BECOME_KWARGS and _extract_become_overrides
# which have no heavy dependencies. Patch just enough to let the module load.
# Stub out imports that proxy.py needs at module level
_stubs = {}
for name in [
    "ftl2", "ftl2.module_loading", "ftl2.module_loading.excluded",
    "ftl2.module_loading.shadowed", "ftl2.exceptions",
    "ftl2.automation", "ftl2.automation.context",
]:
    if name not in sys.modules:
        stub = type(sys)("stub_" + name)
        # Provide dummy callables for functions imported at module level
        stub.get_excluded = lambda *a, **k: None
        stub.is_shadowed = lambda *a, **k: False
        stub.get_native_method = lambda *a, **k: None
        stub.ExcludedModuleError = Exception
        _stubs[name] = stub
        sys.modules[name] = stub

try:
    _spec.loader.exec_module(_mod)
finally:
    # Clean up stubs
    for name in _stubs:
        sys.modules.pop(name, None)

_BECOME_KWARGS = _mod._BECOME_KWARGS
_extract_become_overrides = _mod._extract_become_overrides


class TestBecomeKwargs:
    """Test _BECOME_KWARGS contains all become-related keys."""

    def test_become_method_in_kwargs(self):
        """become_method must be in _BECOME_KWARGS (the actual bug fix)."""
        assert "become_method" in _BECOME_KWARGS

    def test_become_in_kwargs(self):
        assert "become" in _BECOME_KWARGS

    def test_become_user_in_kwargs(self):
        assert "become_user" in _BECOME_KWARGS

    def test_complete_set(self):
        """_BECOME_KWARGS should contain exactly the three become keys."""
        assert _BECOME_KWARGS == frozenset({"become", "become_user", "become_method"})


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
