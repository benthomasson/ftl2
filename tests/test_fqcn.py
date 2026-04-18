"""Tests for FQCN parser."""

import os
import tempfile
from pathlib import Path

import pytest

from ftl2.module_loading.fqcn import (
    InvalidFQCNError,
    ModuleNotFoundError,
    find_ansible_builtin_path,
    get_collection_paths,
    is_valid_fqcn,
    parse_fqcn,
    resolve_collection_module,
    resolve_fqcn,
)


class TestParseFQCN:
    """Tests for parse_fqcn function."""

    def test_parse_valid_fqcn(self):
        """Test parsing a valid FQCN."""
        result = parse_fqcn("amazon.aws.ec2_instance")
        assert result.namespace == "amazon"
        assert result.collection == "aws"
        assert result.module_name == "ec2_instance"

    def test_parse_ansible_builtin(self):
        """Test parsing ansible.builtin FQCN."""
        result = parse_fqcn("ansible.builtin.copy")
        assert result.namespace == "ansible"
        assert result.collection == "builtin"
        assert result.module_name == "copy"

    def test_parse_with_underscores(self):
        """Test parsing FQCN with underscores."""
        result = parse_fqcn("my_namespace.my_collection.my_module")
        assert result.namespace == "my_namespace"
        assert result.collection == "my_collection"
        assert result.module_name == "my_module"

    def test_parsed_fqcn_str(self):
        """Test ParsedFQCN string representation."""
        parsed = parse_fqcn("amazon.aws.ec2_instance")
        assert str(parsed) == "amazon.aws.ec2_instance"

    def test_reject_empty_string(self):
        """Test rejection of empty string."""
        with pytest.raises(InvalidFQCNError) as exc:
            parse_fqcn("")
        assert "empty string" in str(exc.value)

    def test_reject_too_few_parts(self):
        """Test rejection of FQCN with too few parts."""
        with pytest.raises(InvalidFQCNError) as exc:
            parse_fqcn("amazon.aws")
        assert "expected 3 parts" in str(exc.value)

    def test_reject_too_many_parts(self):
        """Test rejection of FQCN with too many parts."""
        with pytest.raises(InvalidFQCNError) as exc:
            parse_fqcn("amazon.aws.ec2.instance")
        assert "expected 3 parts" in str(exc.value)

    def test_reject_invalid_characters(self):
        """Test rejection of FQCN with invalid characters."""
        with pytest.raises(InvalidFQCNError):
            parse_fqcn("amazon.aws.ec2-instance")  # hyphen not allowed

    def test_reject_starting_with_number(self):
        """Test rejection of FQCN part starting with number."""
        with pytest.raises(InvalidFQCNError):
            parse_fqcn("1amazon.aws.ec2_instance")

    def test_reject_spaces(self):
        """Test rejection of FQCN with spaces."""
        with pytest.raises(InvalidFQCNError):
            parse_fqcn("amazon.aws.ec2 instance")


class TestIsValidFQCN:
    """Tests for is_valid_fqcn function."""

    def test_valid_fqcn(self):
        """Test valid FQCN returns True."""
        assert is_valid_fqcn("amazon.aws.ec2_instance")
        assert is_valid_fqcn("ansible.builtin.copy")

    def test_invalid_fqcn(self):
        """Test invalid FQCN returns False."""
        assert not is_valid_fqcn("")
        assert not is_valid_fqcn("amazon.aws")
        assert not is_valid_fqcn("not-a-fqcn")


class TestGetCollectionPaths:
    """Tests for get_collection_paths function."""

    def test_default_paths(self):
        """Test default collection paths are included."""
        # Clear environment variable
        old_env = os.environ.pop("ANSIBLE_COLLECTIONS_PATH", None)
        try:
            paths = get_collection_paths()
            # Should include default paths
            home_collections = Path.home() / ".ansible" / "collections"
            assert home_collections in paths
        finally:
            if old_env:
                os.environ["ANSIBLE_COLLECTIONS_PATH"] = old_env

    def test_environment_variable_override(self):
        """Test ANSIBLE_COLLECTIONS_PATH environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_env = os.environ.get("ANSIBLE_COLLECTIONS_PATH")
            try:
                os.environ["ANSIBLE_COLLECTIONS_PATH"] = tmpdir
                paths = get_collection_paths()
                assert Path(tmpdir) in paths
            finally:
                if old_env:
                    os.environ["ANSIBLE_COLLECTIONS_PATH"] = old_env
                else:
                    os.environ.pop("ANSIBLE_COLLECTIONS_PATH", None)

    def test_multiple_env_paths(self):
        """Test multiple paths in environment variable."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                old_env = os.environ.get("ANSIBLE_COLLECTIONS_PATH")
                try:
                    os.environ["ANSIBLE_COLLECTIONS_PATH"] = f"{tmpdir1}:{tmpdir2}"
                    paths = get_collection_paths()
                    assert Path(tmpdir1) in paths
                    assert Path(tmpdir2) in paths
                finally:
                    if old_env:
                        os.environ["ANSIBLE_COLLECTIONS_PATH"] = old_env
                    else:
                        os.environ.pop("ANSIBLE_COLLECTIONS_PATH", None)

    def test_playbook_dir_collections(self):
        """Test playbook-adjacent collections have highest priority."""
        with tempfile.TemporaryDirectory() as tmpdir:
            playbook_dir = Path(tmpdir)
            collections_dir = playbook_dir / "collections"
            collections_dir.mkdir()

            paths = get_collection_paths(playbook_dir=playbook_dir)
            assert paths[0] == collections_dir

    def test_extra_paths(self):
        """Test extra paths are included."""
        with tempfile.TemporaryDirectory() as tmpdir:
            extra = Path(tmpdir)
            paths = get_collection_paths(extra_paths=[extra])
            assert extra in paths


class TestResolveCollectionModule:
    """Tests for resolve_collection_module function."""

    def test_resolve_existing_module(self):
        """Test resolving an existing collection module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            base = Path(tmpdir)
            module_dir = (
                base
                / "ansible_collections"
                / "testns"
                / "testcoll"
                / "plugins"
                / "modules"
            )
            module_dir.mkdir(parents=True)
            module_file = module_dir / "testmod.py"
            module_file.write_text("# test module")

            result = resolve_collection_module(
                "testns", "testcoll", "testmod", [base]
            )
            assert result == module_file

    def test_resolve_nonexistent_module(self):
        """Test resolving a nonexistent module raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ModuleNotFoundError) as exc:
                resolve_collection_module(
                    "nonexistent", "collection", "module", [Path(tmpdir)]
                )
            assert "nonexistent.collection.module" in str(exc.value)

    def test_search_order_priority(self):
        """Test that earlier paths have priority."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                # Create module in both locations
                for i, tmpdir in enumerate([tmpdir1, tmpdir2]):
                    base = Path(tmpdir)
                    module_dir = (
                        base
                        / "ansible_collections"
                        / "ns"
                        / "coll"
                        / "plugins"
                        / "modules"
                    )
                    module_dir.mkdir(parents=True)
                    module_file = module_dir / "mod.py"
                    module_file.write_text(f"# version {i}")

                # First path should win
                result = resolve_collection_module(
                    "ns", "coll", "mod", [Path(tmpdir1), Path(tmpdir2)]
                )
                assert "version 0" in result.read_text()


class TestResolveFQCN:
    """Tests for resolve_fqcn function."""

    def test_resolve_collection_fqcn(self):
        """Test resolving a collection FQCN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create collection structure
            base = Path(tmpdir)
            module_dir = (
                base
                / "ansible_collections"
                / "amazon"
                / "aws"
                / "plugins"
                / "modules"
            )
            module_dir.mkdir(parents=True)
            module_file = module_dir / "ec2_instance.py"
            module_file.write_text("# ec2_instance module")

            result = resolve_fqcn("amazon.aws.ec2_instance", extra_paths=[base])
            assert result == module_file

    def test_resolve_invalid_fqcn(self):
        """Test resolving an invalid FQCN raises error."""
        with pytest.raises(InvalidFQCNError):
            resolve_fqcn("invalid")

    def test_resolve_nonexistent_raises_error(self):
        """Test resolving nonexistent module raises error."""
        with tempfile.TemporaryDirectory() as tmpdir, pytest.raises(ModuleNotFoundError):
            resolve_fqcn(
                "nonexistent.collection.module",
                extra_paths=[Path(tmpdir)],
            )


class TestFindAnsibleBuiltinPath:
    """Tests for find_ansible_builtin_path function."""

    def test_find_builtin_path(self):
        """Test finding ansible builtin path (if ansible installed)."""
        path = find_ansible_builtin_path()
        # This test depends on whether ansible is installed
        # If ansible is installed, path should be valid
        if path is not None:
            assert path.exists()
            assert path.is_dir()


class TestResolveBuiltinModule:
    """Tests for resolving ansible.builtin modules."""

    def test_resolve_builtin_copy(self):
        """Test resolving ansible.builtin.copy (if ansible installed)."""
        builtin_path = find_ansible_builtin_path()
        if builtin_path is None:
            pytest.skip("Ansible not installed")

        result = resolve_fqcn("ansible.builtin.copy")
        assert result.exists()
        assert result.name == "copy.py"

    def test_resolve_builtin_file(self):
        """Test resolving ansible.builtin.file (if ansible installed)."""
        builtin_path = find_ansible_builtin_path()
        if builtin_path is None:
            pytest.skip("Ansible not installed")

        result = resolve_fqcn("ansible.builtin.file")
        assert result.exists()
        assert result.name == "file.py"

    def test_resolve_builtin_ping(self):
        """Test resolving ansible.builtin.ping (if ansible installed)."""
        builtin_path = find_ansible_builtin_path()
        if builtin_path is None:
            pytest.skip("Ansible not installed")

        result = resolve_fqcn("ansible.builtin.ping")
        assert result.exists()
        assert result.name == "ping.py"
