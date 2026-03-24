"""Tests for dependency detection."""

import tempfile
from pathlib import Path

import pytest

from ftl2.module_loading.dependencies import (
    DependencyResult,
    find_module_utils_imports,
    find_module_utils_imports_from_file,
    find_all_dependencies,
    resolve_module_util_import,
    resolve_core_module_util,
    resolve_collection_module_util,
    get_dependency_tree,
    ModuleUtilsImport,
    ModuleUtilsFinder,
)
from ftl2.module_loading.fqcn import find_ansible_builtin_path


class TestModuleUtilsImport:
    """Tests for ModuleUtilsImport dataclass."""

    def test_parse_core_import(self):
        """Test parsing core ansible module_utils import."""
        imp = ModuleUtilsImport("ansible.module_utils.basic")
        assert imp.is_collection is False
        assert imp.module_path == "basic"

    def test_parse_core_import_nested(self):
        """Test parsing nested core import."""
        imp = ModuleUtilsImport("ansible.module_utils.common.text.converters")
        assert imp.is_collection is False
        assert imp.module_path == "common.text.converters"

    def test_parse_collection_import(self):
        """Test parsing collection module_utils import."""
        imp = ModuleUtilsImport(
            "ansible_collections.amazon.aws.plugins.module_utils.ec2"
        )
        assert imp.is_collection is True
        assert imp.namespace == "amazon"
        assert imp.collection == "aws"
        assert imp.module_path == "ec2"

    def test_parse_collection_import_nested(self):
        """Test parsing nested collection import."""
        imp = ModuleUtilsImport(
            "ansible_collections.amazon.aws.plugins.module_utils.core.waiters"
        )
        assert imp.is_collection is True
        assert imp.namespace == "amazon"
        assert imp.collection == "aws"
        assert imp.module_path == "core.waiters"


class TestFindModuleUtilsImports:
    """Tests for find_module_utils_imports function."""

    def test_find_basic_import(self):
        """Test finding basic AnsibleModule import."""
        source = """
from ansible.module_utils.basic import AnsibleModule

def main():
    module = AnsibleModule(argument_spec={})
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 1
        assert imports[0].import_path == "ansible.module_utils.basic"

    def test_find_multiple_imports(self):
        """Test finding multiple module_utils imports."""
        source = """
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common.text.converters import to_text
from ansible.module_utils.urls import fetch_url
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 3
        paths = [i.import_path for i in imports]
        assert "ansible.module_utils.basic" in paths
        assert "ansible.module_utils.common.text.converters" in paths
        assert "ansible.module_utils.urls" in paths

    def test_find_collection_import(self):
        """Test finding collection module_utils import."""
        source = """
from ansible_collections.amazon.aws.plugins.module_utils.ec2 import AWSRetry
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 1
        assert imports[0].is_collection is True
        assert imports[0].namespace == "amazon"
        assert imports[0].collection == "aws"

    def test_find_import_statement(self):
        """Test finding 'import X' style imports."""
        source = """
import ansible.module_utils.basic
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 1
        assert imports[0].import_path == "ansible.module_utils.basic"

    def test_ignore_non_module_utils(self):
        """Test that non-module_utils imports are ignored."""
        source = """
import os
import json
from pathlib import Path
from ansible.plugins.callback import CallbackBase
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 0

    def test_handle_syntax_error(self):
        """Test handling of syntax errors gracefully."""
        source = """
def broken(
    # missing closing paren
"""
        imports = find_module_utils_imports(source)
        assert imports == []

    def test_mixed_imports(self):
        """Test mixed module_utils and regular imports."""
        source = """
import os
import json
from ansible.module_utils.basic import AnsibleModule
from pathlib import Path
from ansible.module_utils.urls import fetch_url
import sys
"""
        imports = find_module_utils_imports(source)
        assert len(imports) == 2


class TestFindModuleUtilsImportsFromFile:
    """Tests for find_module_utils_imports_from_file function."""

    def test_read_file_and_find_imports(self):
        """Test reading file and finding imports."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("""
from ansible.module_utils.basic import AnsibleModule

def main():
    pass
""")
            f.flush()

            imports = find_module_utils_imports_from_file(Path(f.name))
            assert len(imports) == 1
            assert imports[0].import_path == "ansible.module_utils.basic"

    def test_handle_missing_file(self):
        """Test handling of missing file."""
        imports = find_module_utils_imports_from_file(Path("/nonexistent/file.py"))
        assert imports == []


class TestResolveCoreModuleUtil:
    """Tests for resolve_core_module_util function."""

    def test_resolve_basic(self):
        """Test resolving ansible.module_utils.basic."""
        if find_ansible_builtin_path() is None:
            pytest.skip("Ansible not installed")

        path = resolve_core_module_util("basic")
        assert path is not None
        assert path.exists()
        assert "basic" in path.name or "basic" in str(path)

    def test_resolve_nested(self):
        """Test resolving nested module_utils."""
        if find_ansible_builtin_path() is None:
            pytest.skip("Ansible not installed")

        # Try to resolve a common nested module
        path = resolve_core_module_util("common")
        # This might be a package or module, either is valid
        if path is not None:
            assert path.exists()

    def test_resolve_nonexistent(self):
        """Test resolving nonexistent module_utils returns None."""
        path = resolve_core_module_util("nonexistent_module_xyz")
        assert path is None


class TestResolveCollectionModuleUtil:
    """Tests for resolve_collection_module_util function."""

    def test_resolve_existing_module_util(self):
        """Test resolving an existing collection module_util."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)
            util_file = module_utils_dir / "myutil.py"
            util_file.write_text("# test util")

            result = resolve_collection_module_util(
                "testns", "testcoll", "myutil", [base]
            )
            assert result == util_file

    def test_resolve_nested_module_util(self):
        """Test resolving a nested collection module_util."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
                / "core"
            )
            module_utils_dir.mkdir(parents=True)
            util_file = module_utils_dir / "helpers.py"
            util_file.write_text("# nested util")

            result = resolve_collection_module_util(
                "testns", "testcoll", "core.helpers", [base]
            )
            assert result == util_file

    def test_resolve_package_init(self):
        """Test resolving a package __init__.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
                / "mypackage"
            )
            module_utils_dir.mkdir(parents=True)
            init_file = module_utils_dir / "__init__.py"
            init_file.write_text("# package init")

            result = resolve_collection_module_util(
                "testns", "testcoll", "mypackage", [base]
            )
            assert result == init_file

    def test_resolve_nonexistent_returns_none(self):
        """Test resolving nonexistent module_util returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = resolve_collection_module_util(
                "nonexistent", "collection", "util", [Path(tmpdir)]
            )
            assert result is None


class TestResolveModuleUtilImport:
    """Tests for resolve_module_util_import function."""

    def test_resolve_core_import(self):
        """Test resolving core import."""
        if find_ansible_builtin_path() is None:
            pytest.skip("Ansible not installed")

        imp = ModuleUtilsImport("ansible.module_utils.basic")
        path = resolve_module_util_import(imp)
        assert path is not None
        assert path.exists()

    def test_resolve_collection_import(self):
        """Test resolving collection import."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)
            util_file = module_utils_dir / "myutil.py"
            util_file.write_text("# test")

            imp = ModuleUtilsImport(
                "ansible_collections.testns.testcoll.plugins.module_utils.myutil"
            )
            path = resolve_module_util_import(imp, [base])
            assert path == util_file


class TestFindAllDependencies:
    """Tests for find_all_dependencies function."""

    def test_find_direct_dependencies(self):
        """Test finding direct dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create a module with imports
            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.helper import func

def main():
    pass
""")

            # Create the module_util
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)
            helper = module_utils_dir / "helper.py"
            helper.write_text("def func(): pass")

            result = find_all_dependencies(module, [base])
            assert len(result.dependencies) == 1
            assert helper in result.dependencies

    def test_find_transitive_dependencies(self):
        """Test finding transitive dependencies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create collection structure
            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)

            # Module imports helper
            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.helper import func
""")

            # Helper imports base
            helper = module_utils_dir / "helper.py"
            helper.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.base import BaseClass

def func(): pass
""")

            # Base has no imports
            base_util = module_utils_dir / "base.py"
            base_util.write_text("class BaseClass: pass")

            result = find_all_dependencies(module, [base])
            assert len(result.dependencies) == 2
            assert helper in result.dependencies
            assert base_util in result.dependencies

    def test_handle_circular_imports(self):
        """Test handling of circular imports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)

            # A imports B
            a_util = module_utils_dir / "a.py"
            a_util.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.b import b_func
def a_func(): pass
""")

            # B imports A (circular)
            b_util = module_utils_dir / "b.py"
            b_util.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.a import a_func
def b_func(): pass
""")

            # Module imports A
            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.a import a_func
""")

            # Should not infinite loop
            result = find_all_dependencies(module, [base])
            assert len(result.dependencies) == 2
            assert a_util in result.dependencies
            assert b_util in result.dependencies

    def test_track_unresolved_imports(self):
        """Test tracking of unresolved imports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "module.py"
            module.write_text("""
from ansible_collections.nonexistent.coll.plugins.module_utils.util import func
""")

            result = find_all_dependencies(module, [Path(tmpdir)])
            assert len(result.dependencies) == 0
            assert len(result.unresolved) == 1

    def test_strict_raises_on_unresolved(self):
        """Test that strict=True raises RuntimeError on unresolved deps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "module.py"
            module.write_text("""
from ansible_collections.nonexistent.coll.plugins.module_utils.util import func
""")

            with pytest.raises(RuntimeError, match="Unresolved dependencies"):
                find_all_dependencies(module, [Path(tmpdir)], strict=True)

    def test_strict_passes_when_all_resolved(self):
        """Test that strict=True does not raise when all deps resolve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)

            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.helper import func
""")

            helper = module_utils_dir / "helper.py"
            helper.write_text("def func(): pass")

            # Should not raise
            result = find_all_dependencies(module, [base], strict=True)
            assert len(result.dependencies) == 1

    def test_dependency_result_iteration(self):
        """Test DependencyResult iteration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)

            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.util import func
""")

            util = module_utils_dir / "util.py"
            util.write_text("def func(): pass")

            result = find_all_dependencies(module, [base])

            # Test iteration
            deps = list(result)
            assert len(deps) == 1

            # Test len
            assert len(result) == 1


class TestDependencyResult:
    """Tests for DependencyResult dataclass."""

    def test_raise_if_unresolved_with_unresolved(self):
        """Test raise_if_unresolved raises when there are unresolved deps."""
        result = DependencyResult(
            module_path=Path("/fake/module.py"),
            unresolved=[ModuleUtilsImport("ansible.module_utils.missing")],
        )
        with pytest.raises(RuntimeError, match="Unresolved dependencies.*missing"):
            result.raise_if_unresolved()

    def test_raise_if_unresolved_passes_when_empty(self):
        """Test raise_if_unresolved does nothing when no unresolved deps."""
        result = DependencyResult(module_path=Path("/fake/module.py"))
        result.raise_if_unresolved()  # Should not raise


class TestGetDependencyTree:
    """Tests for get_dependency_tree function."""

    def test_build_dependency_tree(self):
        """Test building a dependency tree."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            module_utils_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "module_utils"
            )
            module_utils_dir.mkdir(parents=True)

            # Module -> helper -> base
            module = base / "module.py"
            module.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.helper import func
""")

            helper = module_utils_dir / "helper.py"
            helper.write_text("""
from ansible_collections.testns.testcoll.plugins.module_utils.base import BaseClass
def func(): pass
""")

            base_util = module_utils_dir / "base.py"
            base_util.write_text("class BaseClass: pass")

            tree = get_dependency_tree(module, [base])

            assert str(module) in tree
            assert str(helper) in tree
            assert str(base_util) in tree

            # Module depends on helper
            assert str(helper) in tree[str(module)]
            # Helper depends on base
            assert str(base_util) in tree[str(helper)]
            # Base has no deps
            assert tree[str(base_util)] == []
