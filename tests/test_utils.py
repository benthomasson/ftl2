"""Tests for utility functions."""

import tempfile
from pathlib import Path

import pytest

from ftl2.exceptions import ModuleNotFound
from ftl2.utils import (
    chunk,
    ensure_directory,
    find_module,
    is_binary_module,
    module_wants_json,
    read_module,
)


class TestFindModule:
    """Tests for find_module function."""

    def test_find_python_module(self):
        """Test finding a Python module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)
            module_file = module_dir / "test_module.py"
            module_file.write_text("# test module")

            result = find_module([module_dir], "test_module")

            assert result == module_file
            assert result.exists()

    def test_find_binary_module(self):
        """Test finding a binary module (no .py extension)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)
            module_file = module_dir / "binary_module"
            module_file.write_bytes(b"\x00\x01\x02")

            result = find_module([module_dir], "binary_module")

            assert result == module_file

    def test_module_not_found(self):
        """Test when module doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)

            result = find_module([module_dir], "nonexistent")

            assert result is None

    def test_search_multiple_directories(self):
        """Test searching multiple directories in order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir1 = Path(tmpdir) / "dir1"
            dir2 = Path(tmpdir) / "dir2"
            dir1.mkdir()
            dir2.mkdir()

            # Module in second directory
            module_file = dir2 / "test.py"
            module_file.write_text("# test")

            result = find_module([dir1, dir2], "test")

            assert result == module_file

    def test_python_module_preferred_over_binary(self):
        """Test that .py files are found before binary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)

            # Create both versions
            py_module = module_dir / "module.py"
            binary_module = module_dir / "module"
            py_module.write_text("# python")
            binary_module.write_bytes(b"\x00\x01")

            result = find_module([module_dir], "module")

            assert result == py_module


class TestReadModule:
    """Tests for read_module function."""

    def test_read_existing_module(self):
        """Test reading an existing module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)
            module_file = module_dir / "test.py"
            content = b"# test module content"
            module_file.write_bytes(content)

            result = read_module([module_dir], "test")

            assert result == content

    def test_read_nonexistent_module(self):
        """Test reading a module that doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_dir = Path(tmpdir)

            with pytest.raises(ModuleNotFound) as exc_info:
                read_module([module_dir], "nonexistent")

            assert "nonexistent" in str(exc_info.value)


class TestChunk:
    """Tests for chunk function."""

    def test_chunk_even_division(self):
        """Test chunking with even division."""
        result = list(chunk([1, 2, 3, 4], 2))

        assert result == [[1, 2], [3, 4]]

    def test_chunk_uneven_division(self):
        """Test chunking with remainder."""
        result = list(chunk([1, 2, 3, 4, 5], 2))

        assert result == [[1, 2], [3, 4], [5]]

    def test_chunk_size_larger_than_list(self):
        """Test chunk size larger than list."""
        result = list(chunk([1, 2, 3], 10))

        assert result == [[1, 2, 3]]

    def test_chunk_empty_list(self):
        """Test chunking empty list."""
        result = list(chunk([], 2))

        assert result == []

    def test_chunk_size_one(self):
        """Test chunk size of 1."""
        result = list(chunk([1, 2, 3], 1))

        assert result == [[1], [2], [3]]

    def test_chunk_zero_raises_valueerror(self):
        """Test that chunk size of 0 raises ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be positive, got 0"):
            list(chunk([1, 2, 3], 0))

    def test_chunk_negative_raises_valueerror(self):
        """Test that negative chunk size raises ValueError."""
        with pytest.raises(ValueError, match="Chunk size must be positive, got -1"):
            list(chunk([1, 2, 3], -1))


class TestEnsureDirectory:
    """Tests for ensure_directory function."""

    def test_create_new_directory(self):
        """Test creating a new directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "new_directory"
            assert not new_dir.exists()

            result = ensure_directory(new_dir)

            assert result.exists()
            assert result.is_dir()
            assert result == new_dir.resolve()

    def test_existing_directory(self):
        """Test with existing directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            existing_dir = Path(tmpdir)

            result = ensure_directory(existing_dir)

            assert result.exists()
            assert result.is_dir()

    def test_create_nested_directories(self):
        """Test creating nested directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = Path(tmpdir) / "a" / "b" / "c"

            result = ensure_directory(nested_dir)

            assert result.exists()
            assert result.is_dir()


class TestIsBinaryModule:
    """Tests for is_binary_module function."""

    def test_text_module(self):
        """Test with a text-based Python module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "text.py"
            module_file.write_text("# Python module\nprint('hello')")

            result = is_binary_module(module_file)

            assert result is False

    def test_binary_module(self):
        """Test with a binary module."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "binary"
            module_file.write_bytes(b"\x00\x01\x02\x03\xff")

            result = is_binary_module(module_file)

            assert result is True


class TestModuleWantsJson:
    """Tests for module_wants_json function."""

    def test_module_with_want_json(self):
        """Test module containing WANT_JSON marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "module.py"
            module_file.write_text("#!/usr/bin/python\nWANT_JSON = True\n")

            result = module_wants_json(module_file)

            assert result is True

    def test_module_without_want_json(self):
        """Test module without WANT_JSON marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "module.py"
            module_file.write_text("#!/usr/bin/python\nprint('hello')\n")

            result = module_wants_json(module_file)

            assert result is False

    def test_binary_module(self):
        """Test binary module (no WANT_JSON)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module_file = Path(tmpdir) / "binary"
            module_file.write_bytes(b"\x00\x01\x02")

            result = module_wants_json(module_file)

            assert result is False
