"""Tests for FTL modules package."""

import pytest

from ftl2.ftl_modules import (
    FTLModuleError,
    FTLModuleCheckModeError,
    FTLModuleNotFoundError,
    FTL_MODULES,
    ANSIBLE_COMPAT,
    get_module,
    has_ftl_module,
    list_modules,
    list_ansible_compat,
    ftl_file,
    ftl_copy,
    ftl_template,
    ftl_uri,
    ftl_get_url,
    ftl_command,
    ftl_shell,
    ftl_pip,
    ftl_ec2_instance,
)


class TestFTLModuleError:
    """Tests for FTLModuleError exception."""

    def test_basic_error(self):
        """Test creating a basic error."""
        error = FTLModuleError("Something went wrong")

        assert str(error) == "Something went wrong"
        assert error.msg == "Something went wrong"
        assert error.result["failed"] is True
        assert error.result["msg"] == "Something went wrong"

    def test_error_with_extra_fields(self):
        """Test error with additional result fields."""
        error = FTLModuleError(
            "File not found",
            path="/tmp/missing.txt",
            errno=2,
        )

        assert error.result["failed"] is True
        assert error.result["msg"] == "File not found"
        assert error.result["path"] == "/tmp/missing.txt"
        assert error.result["errno"] == 2

    def test_error_is_exception(self):
        """Test that FTLModuleError is a proper exception."""
        with pytest.raises(FTLModuleError) as exc_info:
            raise FTLModuleError("Test error")

        assert exc_info.value.msg == "Test error"


class TestFTLModuleCheckModeError:
    """Tests for FTLModuleCheckModeError."""

    def test_check_mode_error(self):
        """Test check mode error."""
        error = FTLModuleCheckModeError("my_module")

        assert "my_module" in str(error)
        assert error.result["failed"] is True
        assert error.result["module"] == "my_module"
        assert error.result["check_mode_supported"] is False


class TestFTLModuleNotFoundError:
    """Tests for FTLModuleNotFoundError."""

    def test_not_found_error(self):
        """Test module not found error."""
        error = FTLModuleNotFoundError("nonexistent_module")

        assert "nonexistent_module" in str(error)
        assert error.result["failed"] is True
        assert error.result["module"] == "nonexistent_module"


class TestModuleRegistry:
    """Tests for module registry."""

    def test_ftl_modules_contains_core_modules(self):
        """Test that FTL_MODULES contains core modules."""
        expected_modules = [
            "file",
            "copy",
            "template",
            "uri",
            "get_url",
            "command",
            "shell",
            "pip",
            "ec2_instance",
        ]

        for module in expected_modules:
            assert module in FTL_MODULES, f"Missing module: {module}"

    def test_ansible_compat_contains_fqcns(self):
        """Test that ANSIBLE_COMPAT contains expected FQCNs."""
        expected_fqcns = [
            "ansible.builtin.file",
            "ansible.builtin.copy",
            "ansible.builtin.template",
            "ansible.builtin.uri",
            "ansible.builtin.get_url",
            "ansible.builtin.command",
            "ansible.builtin.shell",
            "ansible.builtin.pip",
            "amazon.aws.ec2_instance",
        ]

        for fqcn in expected_fqcns:
            assert fqcn in ANSIBLE_COMPAT, f"Missing FQCN: {fqcn}"

    def test_get_module_by_short_name(self):
        """Test getting module by short name."""
        module = get_module("file")
        assert module is ftl_file

        module = get_module("copy")
        assert module is ftl_copy

    def test_get_module_by_fqcn(self):
        """Test getting module by Ansible FQCN."""
        module = get_module("ansible.builtin.file")
        assert module is ftl_file

        module = get_module("ansible.builtin.copy")
        assert module is ftl_copy

        module = get_module("amazon.aws.ec2_instance")
        assert module is ftl_ec2_instance

    def test_get_module_not_found(self):
        """Test getting nonexistent module returns None."""
        module = get_module("nonexistent_module")
        assert module is None

        module = get_module("ansible.builtin.nonexistent")
        assert module is None

    def test_has_ftl_module(self):
        """Test has_ftl_module function."""
        assert has_ftl_module("file") is True
        assert has_ftl_module("ansible.builtin.file") is True
        assert has_ftl_module("nonexistent") is False

    def test_list_modules(self):
        """Test listing all module short names."""
        modules = list_modules()

        assert isinstance(modules, list)
        assert "file" in modules
        assert "copy" in modules
        assert "uri" in modules

    def test_list_ansible_compat(self):
        """Test listing all Ansible FQCNs."""
        fqcns = list_ansible_compat()

        assert isinstance(fqcns, list)
        assert "ansible.builtin.file" in fqcns
        assert "ansible.builtin.copy" in fqcns
        assert "amazon.aws.ec2_instance" in fqcns


class TestModuleStubs:
    """Tests that module stubs exist and have correct signatures."""

    def test_ftl_file_exists(self):
        """Test ftl_file function exists."""
        assert callable(ftl_file)

    def test_ftl_copy_exists(self):
        """Test ftl_copy function exists."""
        assert callable(ftl_copy)

    def test_ftl_template_exists(self):
        """Test ftl_template function exists."""
        assert callable(ftl_template)

    def test_ftl_uri_exists(self):
        """Test ftl_uri function exists."""
        assert callable(ftl_uri)

    def test_ftl_get_url_exists(self):
        """Test ftl_get_url function exists."""
        assert callable(ftl_get_url)

    def test_ftl_command_exists(self):
        """Test ftl_command function exists."""
        assert callable(ftl_command)

    def test_ftl_shell_exists(self):
        """Test ftl_shell function exists."""
        assert callable(ftl_shell)

    def test_ftl_pip_exists(self):
        """Test ftl_pip function exists."""
        assert callable(ftl_pip)

    def test_ftl_ec2_instance_exists(self):
        """Test ftl_ec2_instance function exists."""
        assert callable(ftl_ec2_instance)

    def test_stubs_raise_not_implemented(self):
        """Test that stubs raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            ftl_file(path="/tmp/test")

        with pytest.raises(NotImplementedError):
            ftl_copy(src="/tmp/a", dest="/tmp/b")

        with pytest.raises(NotImplementedError):
            ftl_command(cmd="echo hello")
