"""Module requirements checker for Ansible modules.

Parses DOCUMENTATION from Ansible module source files to extract
Python package requirements and check if they're installed.

This provides helpful error messages when modules fail due to missing
dependencies, rather than cryptic import errors at runtime.

Supports auto-installation of missing dependencies using uv.
"""

import importlib.util
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import yaml

logger = logging.getLogger(__name__)


class ModuleRequirements(NamedTuple):
    """Requirements extracted from a module's DOCUMENTATION."""

    requirements: list[str]
    check_mode_support: str  # "full", "partial", "none", or ""
    short_description: str


class MissingRequirement(NamedTuple):
    """A missing Python package requirement."""

    requirement: str  # Original requirement string (e.g., "linode_api4 >= 2.0.0")
    package_name: str  # Just the package name (e.g., "linode_api4")
    import_name: str  # Python import name (e.g., "linode_api4")


# Regex to extract DOCUMENTATION string from module source
# Matches: DOCUMENTATION = '''...''' or DOCUMENTATION = """..."""
DOCUMENTATION_PATTERN = re.compile(
    r"^DOCUMENTATION\s*=\s*['\"]"
    r"(?:['\"]['\"])?"  # Optional triple quote
    r"(.*?)"
    r"['\"](?:['\"]['\"])?",
    re.MULTILINE | re.DOTALL,
)

# Alternative pattern for raw strings
DOCUMENTATION_PATTERN_RAW = re.compile(
    r"^DOCUMENTATION\s*=\s*r?['\"]['\"]['\"]"
    r"(.*?)"
    r"['\"]['\"]['\"]",
    re.MULTILINE | re.DOTALL,
)


def extract_documentation(module_path: Path) -> str | None:
    """Extract the DOCUMENTATION string from a module file.

    Args:
        module_path: Path to the Ansible module Python file

    Returns:
        The DOCUMENTATION YAML string, or None if not found
    """
    try:
        content = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # Try different patterns
    for pattern in [DOCUMENTATION_PATTERN_RAW, DOCUMENTATION_PATTERN]:
        match = pattern.search(content)
        if match:
            return match.group(1)

    # Try a simpler approach - find DOCUMENTATION = and extract until next module var
    lines = content.split("\n")
    in_doc = False
    doc_lines = []
    quote_char = None

    for line in lines:
        if not in_doc:
            if line.strip().startswith("DOCUMENTATION"):
                # Find the quote character
                if "'''" in line:
                    quote_char = "'''"
                elif '"""' in line:
                    quote_char = '"""'
                elif "'" in line:
                    quote_char = "'"
                elif '"' in line:
                    quote_char = '"'
                in_doc = True
                # Get content after the opening quote
                parts = line.split(quote_char, 1)
                if len(parts) > 1:
                    rest = parts[1]
                    if quote_char in rest:
                        # Single line doc
                        return rest.split(quote_char)[0]
                    doc_lines.append(rest)
        else:
            if quote_char and quote_char in line:
                # End of documentation
                doc_lines.append(line.split(quote_char)[0])
                break
            doc_lines.append(line)

    if doc_lines:
        return "\n".join(doc_lines)

    return None


def parse_requirements(documentation: str) -> ModuleRequirements:
    """Parse DOCUMENTATION YAML to extract requirements.

    Args:
        documentation: The DOCUMENTATION YAML string

    Returns:
        ModuleRequirements with extracted info
    """
    try:
        doc = yaml.safe_load(documentation)
    except yaml.YAMLError:
        return ModuleRequirements([], "", "")

    if not isinstance(doc, dict):
        return ModuleRequirements([], "", "")

    # Extract requirements list
    requirements = doc.get("requirements", [])
    if not isinstance(requirements, list):
        requirements = []

    # Extract check_mode support
    check_mode_support = ""
    attributes = doc.get("attributes", {})
    if isinstance(attributes, dict):
        check_mode = attributes.get("check_mode", {})
        if isinstance(check_mode, dict):
            check_mode_support = check_mode.get("support", "")

    # Extract short description
    short_description = doc.get("short_description", "")

    return ModuleRequirements(
        requirements=requirements,
        check_mode_support=check_mode_support,
        short_description=short_description,
    )


def get_module_requirements(module_path: Path) -> ModuleRequirements:
    """Get requirements for a module from its DOCUMENTATION.

    Args:
        module_path: Path to the module file

    Returns:
        ModuleRequirements with extracted info
    """
    documentation = extract_documentation(module_path)
    if documentation is None:
        return ModuleRequirements([], "", "")

    return parse_requirements(documentation)


def normalize_package_name(requirement: str) -> tuple[str, str]:
    """Normalize a requirement string to package and import names.

    Args:
        requirement: Requirement string like "linode_api4 >= 2.0.0"

    Returns:
        Tuple of (package_name, import_name)
    """
    # Remove version specifiers
    package = re.split(r"[<>=!~\s]", requirement)[0].strip()

    # Common package name to import name mappings
    # (pip name -> import name)
    PACKAGE_TO_IMPORT = {
        "linode-api4": "linode_api4",
        "linode_api4": "linode_api4",
        "boto3": "boto3",
        "botocore": "botocore",
        "google-auth": "google.auth",
        "google-cloud-compute": "google.cloud.compute",
        "azure-mgmt-compute": "azure.mgmt.compute",
        "psycopg2": "psycopg2",
        "psycopg2-binary": "psycopg2",
        "pymysql": "pymysql",
        "dnspython": "dns",
        "python-dateutil": "dateutil",
        "pyyaml": "yaml",
        "requests": "requests",
        "paramiko": "paramiko",
        "jmespath": "jmespath",
        "netaddr": "netaddr",
        "xmltodict": "xmltodict",
    }

    # Normalize package name (lowercase, underscores)
    package_lower = package.lower().replace("-", "_")

    # Look up import name, or derive from package name
    import_name = PACKAGE_TO_IMPORT.get(package.lower())
    if import_name is None:
        import_name = PACKAGE_TO_IMPORT.get(package_lower)
    if import_name is None:
        # Default: use package name with dashes replaced by underscores
        import_name = package.replace("-", "_")

    return package, import_name


def is_package_installed(import_name: str) -> bool:
    """Check if a Python package is installed.

    Args:
        import_name: The Python import name (e.g., "linode_api4")

    Returns:
        True if the package can be imported
    """
    # Handle dotted imports (e.g., "google.auth")
    top_level = import_name.split(".")[0]

    spec = importlib.util.find_spec(top_level)
    return spec is not None


def check_module_requirements(module_path: Path) -> list[MissingRequirement]:
    """Check if all requirements for a module are installed.

    Args:
        module_path: Path to the module file

    Returns:
        List of missing requirements (empty if all installed)
    """
    reqs = get_module_requirements(module_path)
    missing = []

    for req in reqs.requirements:
        package_name, import_name = normalize_package_name(req)

        if not is_package_installed(import_name):
            missing.append(MissingRequirement(
                requirement=req,
                package_name=package_name,
                import_name=import_name,
            ))

    return missing


def format_missing_requirements_error(
    fqcn: str,
    missing: list[MissingRequirement],
) -> str:
    """Format a helpful error message for missing requirements.

    Args:
        fqcn: The module's fully qualified name
        missing: List of missing requirements

    Returns:
        Formatted error message with installation instructions
    """
    if not missing:
        return ""

    if len(missing) == 1:
        req = missing[0]
        return (
            f"Module '{fqcn}' requires Python package '{req.package_name}' "
            f"which is not installed.\n\n"
            f"Install it with:\n"
            f"  pip install {req.package_name}"
        )

    packages = [m.package_name for m in missing]
    return (
        f"Module '{fqcn}' requires Python packages that are not installed:\n"
        + "\n".join(f"  - {m.requirement}" for m in missing)
        + "\n\nInstall them with:\n"
        f"  pip install {' '.join(packages)}"
    )


class InstallResult(NamedTuple):
    """Result of installing missing requirements."""

    success: bool
    installed: list[str]  # Package names that were installed
    failed: list[str]  # Package names that failed to install
    error: str  # Error message if any


def install_missing_requirements(
    missing: list[MissingRequirement],
    quiet: bool = False,
) -> InstallResult:
    """Install missing requirements using uv.

    Uses `uv pip install` to install missing packages into the current
    environment. Requires uv to be installed and available in PATH.

    Args:
        missing: List of missing requirements to install
        quiet: If True, suppress installation output

    Returns:
        InstallResult with success status and details
    """
    if not missing:
        return InstallResult(success=True, installed=[], failed=[], error="")

    # Check if uv is available
    uv_path = shutil.which("uv")
    if uv_path is None:
        return InstallResult(
            success=False,
            installed=[],
            failed=[m.package_name for m in missing],
            error="uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh",
        )

    # Build the install command
    packages = [m.package_name for m in missing]
    cmd = [uv_path, "pip", "install"] + packages

    logger.info(f"Installing missing packages with uv: {' '.join(packages)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"Successfully installed: {', '.join(packages)}")
            # Clear the import cache so newly installed packages can be found
            importlib.invalidate_caches()
            return InstallResult(
                success=True,
                installed=packages,
                failed=[],
                error="",
            )
        else:
            error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            logger.error(f"Failed to install packages: {error_msg}")
            return InstallResult(
                success=False,
                installed=[],
                failed=packages,
                error=f"uv pip install failed: {error_msg}",
            )

    except subprocess.TimeoutExpired:
        return InstallResult(
            success=False,
            installed=[],
            failed=packages,
            error="Installation timed out after 5 minutes",
        )
    except Exception as e:
        return InstallResult(
            success=False,
            installed=[],
            failed=packages,
            error=f"Installation failed: {e}",
        )


def check_and_install_requirements(
    module_path: Path,
    fqcn: str,
    auto_install: bool = False,
    quiet: bool = False,
) -> tuple[bool, str]:
    """Check module requirements and optionally install missing ones.

    Args:
        module_path: Path to the module file
        fqcn: Fully qualified collection name (for error messages)
        auto_install: If True, automatically install missing packages
        quiet: If True, suppress installation output

    Returns:
        Tuple of (success, error_message). Success is True if all
        requirements are satisfied (either already installed or
        successfully auto-installed).
    """
    missing = check_module_requirements(module_path)

    if not missing:
        return True, ""

    if not auto_install:
        # Just return the error message
        return False, format_missing_requirements_error(fqcn, missing)

    # Try to install missing packages
    install_result = install_missing_requirements(missing, quiet=quiet)

    if install_result.success:
        # Verify packages are now importable
        still_missing = check_module_requirements(module_path)
        if still_missing:
            return False, (
                f"Installed packages but they're still not importable:\n"
                + "\n".join(f"  - {m.package_name}" for m in still_missing)
            )
        return True, ""

    # Installation failed
    return False, install_result.error
