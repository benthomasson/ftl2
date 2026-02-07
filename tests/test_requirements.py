"""Tests for module requirements checker."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ftl2.module_loading.requirements import (
    extract_documentation,
    parse_requirements,
    get_module_requirements,
    normalize_package_name,
    is_package_installed,
    check_module_requirements,
    format_missing_requirements_error,
    install_missing_requirements,
    check_and_install_requirements,
    ModuleRequirements,
    MissingRequirement,
    InstallResult,
)


class TestExtractDocumentation:
    """Tests for extract_documentation function."""

    def test_extract_triple_single_quotes(self):
        """Test extracting DOCUMENTATION with triple single quotes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = \'\'\'
---
module: test_module
short_description: Test module
requirements:
  - boto3
\'\'\'
''')
            doc = extract_documentation(module)
            assert doc is not None
            assert "test_module" in doc
            assert "boto3" in doc

    def test_extract_triple_double_quotes(self):
        """Test extracting DOCUMENTATION with triple double quotes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
short_description: Another test
requirements:
  - requests
"""
''')
            doc = extract_documentation(module)
            assert doc is not None
            assert "requests" in doc

    def test_extract_raw_string(self):
        """Test extracting DOCUMENTATION with raw string."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = r"""
---
module: test_module
short_description: Raw string test
requirements:
  - paramiko
"""
''')
            doc = extract_documentation(module)
            assert doc is not None
            assert "paramiko" in doc

    def test_extract_no_documentation(self):
        """Test module without DOCUMENTATION."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
# No DOCUMENTATION here
def main():
    pass
''')
            doc = extract_documentation(module)
            assert doc is None

    def test_extract_nonexistent_file(self):
        """Test extracting from nonexistent file."""
        doc = extract_documentation(Path("/nonexistent/path/module.py"))
        assert doc is None


class TestParseRequirements:
    """Tests for parse_requirements function."""

    def test_parse_requirements_list(self):
        """Test parsing requirements list."""
        doc = """
---
module: test_module
short_description: Test description
requirements:
  - boto3
  - botocore
  - requests >= 2.0.0
"""
        reqs = parse_requirements(doc)
        assert len(reqs.requirements) == 3
        assert "boto3" in reqs.requirements
        assert "botocore" in reqs.requirements
        assert "requests >= 2.0.0" in reqs.requirements

    def test_parse_check_mode_support(self):
        """Test parsing check_mode support."""
        doc = """
---
module: test_module
attributes:
  check_mode:
    support: full
"""
        reqs = parse_requirements(doc)
        assert reqs.check_mode_support == "full"

    def test_parse_short_description(self):
        """Test parsing short_description."""
        doc = """
---
module: test_module
short_description: Manage cloud instances
"""
        reqs = parse_requirements(doc)
        assert reqs.short_description == "Manage cloud instances"

    def test_parse_empty_requirements(self):
        """Test parsing when no requirements."""
        doc = """
---
module: test_module
short_description: No deps needed
"""
        reqs = parse_requirements(doc)
        assert reqs.requirements == []

    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML."""
        reqs = parse_requirements("not: valid: yaml: {{")
        assert reqs.requirements == []
        assert reqs.check_mode_support == ""

    def test_parse_non_dict_yaml(self):
        """Test parsing YAML that's not a dict."""
        reqs = parse_requirements("just a string")
        assert reqs.requirements == []


class TestGetModuleRequirements:
    """Tests for get_module_requirements function."""

    def test_get_requirements_from_module(self):
        """Test getting requirements from a module file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
short_description: Cloud integration
requirements:
  - linode_api4 >= 2.0.0
  - requests
attributes:
  check_mode:
    support: none
"""

def main():
    pass
''')
            reqs = get_module_requirements(module)
            assert len(reqs.requirements) == 2
            assert "linode_api4 >= 2.0.0" in reqs.requirements
            assert reqs.check_mode_support == "none"
            assert reqs.short_description == "Cloud integration"


class TestNormalizePackageName:
    """Tests for normalize_package_name function."""

    def test_simple_package(self):
        """Test simple package name."""
        pkg, imp = normalize_package_name("boto3")
        assert pkg == "boto3"
        assert imp == "boto3"

    def test_package_with_version(self):
        """Test package with version specifier."""
        pkg, imp = normalize_package_name("requests >= 2.0.0")
        assert pkg == "requests"
        assert imp == "requests"

    def test_package_with_dashes(self):
        """Test package with dashes in name."""
        pkg, imp = normalize_package_name("linode-api4")
        assert pkg == "linode-api4"
        assert imp == "linode_api4"

    def test_known_mapping(self):
        """Test known package-to-import mappings."""
        pkg, imp = normalize_package_name("dnspython")
        assert pkg == "dnspython"
        assert imp == "dns"

        pkg, imp = normalize_package_name("python-dateutil")
        assert pkg == "python-dateutil"
        assert imp == "dateutil"

        pkg, imp = normalize_package_name("pyyaml")
        assert pkg == "pyyaml"
        assert imp == "yaml"


class TestIsPackageInstalled:
    """Tests for is_package_installed function."""

    def test_installed_package(self):
        """Test checking an installed package."""
        # These should be installed in any Python environment
        assert is_package_installed("json") is True
        assert is_package_installed("os") is True
        assert is_package_installed("sys") is True

    def test_not_installed_package(self):
        """Test checking a package that's not installed."""
        assert is_package_installed("nonexistent_fake_package_xyz123") is False

    def test_dotted_import(self):
        """Test checking dotted import name."""
        # os.path is a submodule of os
        assert is_package_installed("os.path") is True


class TestCheckModuleRequirements:
    """Tests for check_module_requirements function."""

    def test_all_installed(self):
        """Test module with all requirements installed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
requirements:
  - json
  - os
"""
''')
            missing = check_module_requirements(module)
            assert len(missing) == 0

    def test_missing_requirements(self):
        """Test module with missing requirements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
requirements:
  - nonexistent_fake_package_xyz123
  - another_fake_package_abc456
"""
''')
            missing = check_module_requirements(module)
            assert len(missing) == 2
            assert missing[0].package_name == "nonexistent_fake_package_xyz123"
            assert missing[1].package_name == "another_fake_package_abc456"

    def test_no_requirements(self):
        """Test module with no requirements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
short_description: No deps
"""
''')
            missing = check_module_requirements(module)
            assert len(missing) == 0


class TestFormatMissingRequirementsError:
    """Tests for format_missing_requirements_error function."""

    def test_single_missing(self):
        """Test error message for single missing requirement."""
        missing = [
            MissingRequirement(
                requirement="boto3 >= 1.0",
                package_name="boto3",
                import_name="boto3",
            )
        ]
        msg = format_missing_requirements_error("amazon.aws.ec2_instance", missing)

        assert "amazon.aws.ec2_instance" in msg
        assert "boto3" in msg
        assert "pip install boto3" in msg

    def test_multiple_missing(self):
        """Test error message for multiple missing requirements."""
        missing = [
            MissingRequirement(
                requirement="boto3 >= 1.0",
                package_name="boto3",
                import_name="boto3",
            ),
            MissingRequirement(
                requirement="botocore",
                package_name="botocore",
                import_name="botocore",
            ),
        ]
        msg = format_missing_requirements_error("amazon.aws.ec2_instance", missing)

        assert "amazon.aws.ec2_instance" in msg
        assert "boto3" in msg
        assert "botocore" in msg
        assert "pip install boto3 botocore" in msg

    def test_empty_missing(self):
        """Test no error message when nothing missing."""
        msg = format_missing_requirements_error("test.module", [])
        assert msg == ""


class TestIntegration:
    """Integration tests for requirements checking."""

    def test_full_workflow(self):
        """Test full workflow from module file to error message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "cloud_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: cloud_module
short_description: Manage cloud resources
requirements:
  - nonexistent_cloud_sdk >= 5.0.0
  - another_missing_lib
attributes:
  check_mode:
    support: none
"""

from ansible.module_utils.basic import AnsibleModule

def main():
    pass
''')
            # Get requirements
            reqs = get_module_requirements(module)
            assert len(reqs.requirements) == 2
            assert reqs.check_mode_support == "none"

            # Check what's missing
            missing = check_module_requirements(module)
            assert len(missing) == 2

            # Format error
            error = format_missing_requirements_error("test.cloud_module", missing)
            assert "pip install" in error
            assert "nonexistent_cloud_sdk" in error


class TestInstallMissingRequirements:
    """Tests for install_missing_requirements function."""

    def test_empty_list_returns_success(self):
        """Test that empty list returns success."""
        result = install_missing_requirements([])
        assert result.success is True
        assert result.installed == []
        assert result.failed == []

    def test_uv_not_available(self):
        """Test error when uv is not installed."""
        missing = [
            MissingRequirement("fake_pkg", "fake_pkg", "fake_pkg"),
        ]

        with patch("shutil.which", return_value=None):
            result = install_missing_requirements(missing)

        assert result.success is False
        assert "uv is not installed" in result.error
        assert result.failed == ["fake_pkg"]

    def test_successful_install(self):
        """Test successful package installation."""
        missing = [
            MissingRequirement("test_pkg >= 1.0", "test_pkg", "test_pkg"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Successfully installed test_pkg"
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/uv"):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                result = install_missing_requirements(missing)

        assert result.success is True
        assert result.installed == ["test_pkg"]
        assert result.failed == []

        # Verify uv pip install was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["/usr/bin/uv", "pip", "install", "test_pkg"]

    def test_failed_install(self):
        """Test failed package installation."""
        missing = [
            MissingRequirement("bad_pkg", "bad_pkg", "bad_pkg"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Could not find package"

        with patch("shutil.which", return_value="/usr/bin/uv"):
            with patch("subprocess.run", return_value=mock_result):
                result = install_missing_requirements(missing)

        assert result.success is False
        assert "Could not find package" in result.error
        assert result.failed == ["bad_pkg"]

    def test_multiple_packages(self):
        """Test installing multiple packages."""
        missing = [
            MissingRequirement("pkg1", "pkg1", "pkg1"),
            MissingRequirement("pkg2 >= 2.0", "pkg2", "pkg2"),
        ]

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/uv"):
            with patch("subprocess.run", return_value=mock_result) as mock_run:
                result = install_missing_requirements(missing)

        assert result.success is True
        assert result.installed == ["pkg1", "pkg2"]

        # Verify both packages passed to uv
        call_args = mock_run.call_args
        assert "pkg1" in call_args[0][0]
        assert "pkg2" in call_args[0][0]


class TestCheckAndInstallRequirements:
    """Tests for check_and_install_requirements function."""

    def test_no_requirements_returns_success(self):
        """Test module with no requirements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
short_description: No deps
"""
''')
            success, error = check_and_install_requirements(
                module, "test.module", auto_install=False
            )
            assert success is True
            assert error == ""

    def test_missing_without_auto_install(self):
        """Test missing requirements without auto-install."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
requirements:
  - nonexistent_fake_package_xyz123
"""
''')
            success, error = check_and_install_requirements(
                module, "test.module", auto_install=False
            )
            assert success is False
            assert "pip install" in error
            assert "nonexistent_fake_package_xyz123" in error

    def test_auto_install_called_when_enabled(self):
        """Test that auto-install is attempted when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
requirements:
  - nonexistent_fake_package_xyz123
"""
''')
            # Mock the install function to return success
            mock_install_result = InstallResult(
                success=True,
                installed=["nonexistent_fake_package_xyz123"],
                failed=[],
                error="",
            )

            with patch(
                "ftl2.module_loading.requirements.install_missing_requirements",
                return_value=mock_install_result,
            ) as mock_install:
                # Still fails because package isn't really installed
                success, error = check_and_install_requirements(
                    module, "test.module", auto_install=True
                )

            # Verify install was called
            mock_install.assert_called_once()

    def test_all_satisfied_returns_success(self):
        """Test that satisfied requirements return success."""
        with tempfile.TemporaryDirectory() as tmpdir:
            module = Path(tmpdir) / "test_module.py"
            module.write_text('''
DOCUMENTATION = """
---
module: test_module
requirements:
  - json
  - os
"""
''')
            success, error = check_and_install_requirements(
                module, "test.module", auto_install=True
            )
            assert success is True
            assert error == ""
