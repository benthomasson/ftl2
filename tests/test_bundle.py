"""Tests for bundle builder."""

import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

from ftl2.module_loading.bundle import (
    build_bundle,
    verify_bundle,
    list_bundle_contents,
    get_archive_path,
    Bundle,
    BundleInfo,
    BundleCache,
)


class TestGetArchivePath:
    """Tests for get_archive_path function."""

    def test_core_module_utils_path(self):
        """Test path extraction for core module_utils."""
        path = Path("/usr/lib/python3/site-packages/ansible/module_utils/basic.py")
        result = get_archive_path(path)
        assert result == "ansible/module_utils/basic.py"

    def test_nested_core_module_utils_path(self):
        """Test path extraction for nested core module_utils."""
        path = Path("/usr/lib/python3/site-packages/ansible/module_utils/common/text/converters.py")
        result = get_archive_path(path)
        assert result == "ansible/module_utils/common/text/converters.py"

    def test_collection_module_utils_path(self):
        """Test path extraction for collection module_utils."""
        path = Path("/home/user/.ansible/collections/ansible_collections/amazon/aws/plugins/module_utils/ec2.py")
        result = get_archive_path(path)
        assert result == "ansible_collections/amazon/aws/plugins/module_utils/ec2.py"

    def test_fallback_to_filename(self):
        """Test fallback to filename for unknown paths."""
        path = Path("/some/random/path/myutil.py")
        result = get_archive_path(path)
        assert result == "myutil.py"


class TestBundleInfo:
    """Tests for BundleInfo dataclass."""

    def test_bundle_info_str(self):
        """Test BundleInfo string representation."""
        info = BundleInfo(
            fqcn="test.module.name",
            content_hash="abc123def456",
            size=1024,
            module_path=Path("/path/to/module.py"),
            dependency_count=5,
        )
        result = str(info)
        assert "test.module.name" in result
        assert "abc123def456" in result
        assert "1024" in result
        assert "5" in result


class TestBuildBundle:
    """Tests for build_bundle function."""

    def test_build_simple_bundle(self):
        """Test building a bundle with no dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mymodule.py"
            module.write_text("""
def main(args):
    return {"msg": "hello", "changed": False}
""")

            bundle = build_bundle(module, dependencies=[])

            assert bundle.info.size > 0
            assert len(bundle.info.content_hash) == 12
            assert bundle.info.dependency_count == 0

    def test_bundle_contains_module(self):
        """Test that bundle contains the module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "testmod.py"
            module.write_text("def main(args): return {'ok': True}")

            bundle = build_bundle(module, dependencies=[])
            contents = list_bundle_contents(bundle)

            assert "ftl2_module.py" in contents
            assert "__main__.py" in contents

    def test_bundle_contains_dependencies(self):
        """Test that bundle contains dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create module
            module = base / "module.py"
            module.write_text("def main(args): pass")

            # Create fake dependency structure
            dep_dir = base / "ansible" / "module_utils"
            dep_dir.mkdir(parents=True)
            dep_file = dep_dir / "helper.py"
            dep_file.write_text("def help_func(): pass")

            bundle = build_bundle(module, dependencies=[dep_file])
            contents = list_bundle_contents(bundle)

            assert "ftl2_module.py" in contents
            assert "ansible/module_utils/helper.py" in contents

    def test_bundle_is_valid_zip(self):
        """Test that bundle is a valid ZIP file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])

            # Should be valid ZIP
            buffer = io.BytesIO(bundle.data)
            with zipfile.ZipFile(buffer, "r") as zf:
                assert zf.testzip() is None

    def test_bundle_hash_is_deterministic(self):
        """Test that content hash is deterministic."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {'x': 1}")

            bundle1 = build_bundle(module, dependencies=[])
            bundle2 = build_bundle(module, dependencies=[])

            assert bundle1.info.content_hash == bundle2.info.content_hash

    def test_bundle_hash_changes_with_content(self):
        """Test that hash changes when content changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"

            module.write_text("def main(args): return {'version': 1}")
            bundle1 = build_bundle(module, dependencies=[])

            module.write_text("def main(args): return {'version': 2}")
            bundle2 = build_bundle(module, dependencies=[])

            assert bundle1.info.content_hash != bundle2.info.content_hash

    def test_bundle_with_fqcn(self):
        """Test building bundle with FQCN metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            bundle = build_bundle(module, dependencies=[], fqcn="test.collection.mymod")

            assert bundle.info.fqcn == "test.collection.mymod"

    def test_bundle_auto_detects_dependencies(self):
        """Test that bundle auto-detects dependencies when not provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create collection structure
            module_utils_dir = (
                base / "ansible_collections" / "testns" / "testcoll" /
                "plugins" / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)
            helper = module_utils_dir / "helper.py"
            helper.write_text("def help(): pass")

            # Create module that imports the helper
            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.helper import help
def main(args): return {}
""")

            bundle = build_bundle(module, collection_paths=[base])

            assert bundle.info.dependency_count == 1

    def test_bundle_adds_init_files(self):
        """Test that bundle adds __init__.py for packages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create nested dependency
            dep_dir = base / "ansible" / "module_utils" / "common"
            dep_dir.mkdir(parents=True)
            dep_file = dep_dir / "text.py"
            dep_file.write_text("# text utils")

            module = base / "mod.py"
            module.write_text("def main(args): pass")

            bundle = build_bundle(module, dependencies=[dep_file])
            contents = list_bundle_contents(bundle)

            # Should have __init__.py files
            assert "ansible/__init__.py" in contents
            assert "ansible/module_utils/__init__.py" in contents
            assert "ansible/module_utils/common/__init__.py" in contents


class TestVerifyBundle:
    """Tests for verify_bundle function."""

    def test_verify_valid_bundle(self):
        """Test verifying a valid bundle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])
            assert verify_bundle(bundle) is True

    def test_verify_invalid_bundle(self):
        """Test verifying an invalid bundle."""
        info = BundleInfo(
            fqcn="test",
            content_hash="invalid",
            size=10,
            module_path=Path("/fake"),
            dependency_count=0,
        )
        bundle = Bundle(info=info, data=b"not a zip file")
        assert verify_bundle(bundle) is False


class TestListBundleContents:
    """Tests for list_bundle_contents function."""

    def test_list_contents(self):
        """Test listing bundle contents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            bundle = build_bundle(module, dependencies=[])
            contents = list_bundle_contents(bundle)

            assert isinstance(contents, list)
            assert len(contents) >= 2  # At least __main__.py and ftl2_module.py


class TestBundleWriteMethods:
    """Tests for Bundle write methods."""

    def test_write_to_file(self):
        """Test writing bundle to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {}")

            bundle = build_bundle(module, dependencies=[])

            output_path = Path(tmpdir) / "bundle.zip"
            bundle.write_to_file(output_path)

            assert output_path.exists()
            assert output_path.read_bytes() == bundle.data

    def test_write_to_stream(self):
        """Test writing bundle to stream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {'stream': True}")

            bundle = build_bundle(module, dependencies=[])

            stream = io.BytesIO()
            bundle.write_to_stream(stream)

            assert stream.getvalue() == bundle.data


class TestBundleCache:
    """Tests for BundleCache class."""

    def test_cache_add_and_get(self):
        """Test adding and getting from cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            cache = BundleCache()
            bundle = build_bundle(module, dependencies=[], fqcn="test.mod")

            cache.add(bundle)

            assert cache.get("test.mod") is bundle
            assert cache.get_by_hash(bundle.info.content_hash) is bundle

    def test_cache_get_or_build_from_path(self):
        """Test get_or_build_from_path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): return {'cached': True}")

            cache = BundleCache()

            # First call builds
            bundle1 = cache.get_or_build_from_path(module, fqcn="test.cached")

            # Second call returns cached
            bundle2 = cache.get_or_build_from_path(module, fqcn="test.cached")

            assert bundle1 is bundle2
            assert len(cache) == 1

    def test_cache_contains(self):
        """Test __contains__ method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            cache = BundleCache()
            bundle = build_bundle(module, dependencies=[], fqcn="test.check")
            cache.add(bundle)

            assert "test.check" in cache
            assert "nonexistent" not in cache

    def test_cache_clear(self):
        """Test clearing cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            cache = BundleCache()
            bundle = build_bundle(module, dependencies=[], fqcn="test.clear")
            cache.add(bundle)

            assert len(cache) == 1
            cache.clear()
            assert len(cache) == 0

    def test_cache_total_size(self):
        """Test total_size property."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = BundleCache()

            for i in range(3):
                module = Path(tmpdir) / f"mod{i}.py"
                module.write_text(f"def main(args): return {{'id': {i}}}")
                bundle = build_bundle(module, dependencies=[], fqcn=f"test.mod{i}")
                cache.add(bundle)

            assert cache.total_size > 0

    def test_cache_bundles_property(self):
        """Test bundles property returns copy."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("def main(args): pass")

            cache = BundleCache()
            bundle = build_bundle(module, dependencies=[], fqcn="test.prop")
            cache.add(bundle)

            bundles = cache.bundles
            assert "test.prop" in bundles

            # Modifying returned dict shouldn't affect cache
            bundles.clear()
            assert "test.prop" in cache


class TestBundleExecution:
    """Tests for bundle execution."""

    def test_bundle_is_executable(self):
        """Test that bundle can be executed with Python."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("""
def main(args):
    return {"msg": "executed", "args": args, "changed": False}
""")

            bundle = build_bundle(module, dependencies=[])

            # Write to file
            bundle_path = Path(tmpdir) / "bundle.pyz"
            bundle.write_to_file(bundle_path)

            # Execute with params via stdin
            params = json.dumps({"ANSIBLE_MODULE_ARGS": {"key": "value"}})
            result = subprocess.run(
                [sys.executable, str(bundle_path)],
                input=params,
                capture_output=True,
                text=True,
                timeout=10,
            )

            assert result.returncode == 0
            output = json.loads(result.stdout)
            assert output["msg"] == "executed"
            assert output["args"]["key"] == "value"

    def test_bundle_handles_empty_stdin(self):
        """Test that bundle handles empty stdin."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("""
def main(args):
    return {"received_args": args}
""")

            bundle = build_bundle(module, dependencies=[])
            bundle_path = Path(tmpdir) / "bundle.pyz"
            bundle.write_to_file(bundle_path)

            result = subprocess.run(
                [sys.executable, str(bundle_path)],
                input="",
                capture_output=True,
                text=True,
                timeout=10,
            )

            assert result.returncode == 0
            output = json.loads(result.stdout)
            assert output["received_args"] == {}

    def test_bundle_handles_exception(self):
        """Test that bundle handles exceptions gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "mod.py"
            module.write_text("""
def main(args):
    raise ValueError("Test error")
""")

            bundle = build_bundle(module, dependencies=[])
            bundle_path = Path(tmpdir) / "bundle.pyz"
            bundle.write_to_file(bundle_path)

            result = subprocess.run(
                [sys.executable, str(bundle_path)],
                input="{}",
                capture_output=True,
                text=True,
                timeout=10,
            )

            assert result.returncode == 1
            output = json.loads(result.stdout)
            assert output["failed"] is True
            assert "Test error" in output["msg"]
