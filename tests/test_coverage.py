"""Tests for FTL2 production coverage collection (GH-102)."""

import os
from unittest.mock import patch

import pytest

from ftl2.coverage import ControllerCoverage, coverage_dir, is_coverage_enabled


class TestIsCoverageEnabled:
    def test_default_off(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FTL2_COVERAGE", None)
            assert not is_coverage_enabled()

    def test_enabled(self):
        with patch.dict(os.environ, {"FTL2_COVERAGE": "1"}):
            assert is_coverage_enabled()

    def test_other_values_off(self):
        for val in ("0", "true", "yes", ""):
            with patch.dict(os.environ, {"FTL2_COVERAGE": val}):
                assert not is_coverage_enabled(), f"Expected False for {val!r}"


class TestCoverageDir:
    def test_default_path(self, tmp_path):
        with patch.dict(os.environ, {"FTL2_COVERAGE_DIR": str(tmp_path / "cov")}):
            d = coverage_dir()
            assert d == tmp_path / "cov"
            assert d.is_dir()

    def test_creates_directory(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "cov"
        with patch.dict(os.environ, {"FTL2_COVERAGE_DIR": str(target)}):
            d = coverage_dir()
            assert d == target
            assert d.is_dir()


class TestControllerCoverage:
    def test_no_coverage_installed(self):
        """ControllerCoverage is a no-op when coverage package is absent."""
        with patch.dict("sys.modules", {"coverage": None}):
            with patch("builtins.__import__", side_effect=_make_import_raiser("coverage")):
                cc = ControllerCoverage()
                cc.__enter__()
                assert cc._cov is None
                cc.__exit__(None, None, None)

    def test_writes_data_file(self, tmp_path):
        """ControllerCoverage creates a .coverage data file."""
        pytest.importorskip("coverage")
        cov_dir = tmp_path / "cov"
        with patch.dict(os.environ, {"FTL2_COVERAGE_DIR": str(cov_dir)}):
            with ControllerCoverage():
                pass  # coverage is running during this block
            # Data file should exist
            files = list(cov_dir.glob(".coverage.controller.*"))
            assert len(files) == 1


class TestMessageTypes:
    def test_coverage_messages_registered(self):
        from ftl2.message import GateProtocol

        assert "GetCoverage" in GateProtocol.MESSAGE_TYPES
        assert "GetCoverageResult" in GateProtocol.MESSAGE_TYPES


class TestGateBuildDeps:
    def test_includes_coverage_when_enabled(self):
        """When FTL2_COVERAGE=1, gate build config includes coverage dep."""
        from ftl2.gate import GateBuildConfig

        base_deps = ["inotify_simple"]
        with patch.dict(os.environ, {"FTL2_COVERAGE": "1"}):
            # Simulate what runners._build_and_upload_gate does
            deps = list(base_deps)
            if is_coverage_enabled():
                deps.append("coverage")
            config = GateBuildConfig(dependencies=deps)
            assert "coverage" in config.dependencies

    def test_excludes_coverage_when_disabled(self):
        """When FTL2_COVERAGE is not set, gate build config omits coverage."""
        from ftl2.gate import GateBuildConfig

        base_deps = ["inotify_simple"]
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("FTL2_COVERAGE", None)
            deps = list(base_deps)
            if is_coverage_enabled():
                deps.append("coverage")
            config = GateBuildConfig(dependencies=deps)
            assert "coverage" not in config.dependencies


def _make_import_raiser(blocked_module):
    """Create an __import__ replacement that raises ImportError for a specific module."""
    real_import = __import__

    def _import(name, *args, **kwargs):
        if name == blocked_module:
            raise ImportError(f"No module named '{blocked_module}'")
        return real_import(name, *args, **kwargs)

    return _import
