"""Test package version and basic imports."""

import ftl2


def test_version():
    """Verify package version is set."""
    assert ftl2.__version__ == "0.1.0"


def test_package_imports():
    """Verify package can be imported."""
    assert ftl2 is not None
