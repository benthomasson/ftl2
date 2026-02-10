"""FQCN (Fully Qualified Collection Name) parser for Ansible modules.

Parses FQCNs like "amazon.aws.ec2_instance" or "ansible.builtin.copy"
and resolves them to absolute file paths.
"""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# Pattern for valid FQCN: namespace.collection.module_name
# Each component must be a valid Python identifier
FQCN_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)$")

# Default collection search paths (in priority order after playbook-adjacent)
DEFAULT_COLLECTION_PATHS = [
    Path.home() / ".ansible" / "collections",
    Path("/usr/share/ansible/collections"),
]

# Cache for ansible core modules path
_ansible_builtin_path_cache: Path | None = None
_ansible_module_utils_path_cache: Path | None = None


class ParsedFQCN(NamedTuple):
    """Parsed FQCN components."""

    namespace: str
    collection: str
    module_name: str

    def __str__(self) -> str:
        return f"{self.namespace}.{self.collection}.{self.module_name}"


class FQCNError(Exception):
    """Base exception for FQCN-related errors."""

    pass


class InvalidFQCNError(FQCNError):
    """Raised when FQCN format is invalid."""

    def __init__(self, fqcn: str, reason: str = ""):
        self.fqcn = fqcn
        self.reason = reason
        msg = f"Invalid FQCN: {fqcn}"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class ModuleNotFoundError(FQCNError):
    """Raised when a module cannot be found."""

    def __init__(self, fqcn: str, searched_paths: list[Path] | None = None):
        self.fqcn = fqcn
        self.searched_paths = searched_paths or []
        msg = f"Module not found: {fqcn}"
        if searched_paths:
            paths_str = "\n  ".join(str(p) for p in searched_paths)
            msg += f"\nSearched paths:\n  {paths_str}"
        super().__init__(msg)


def parse_fqcn(fqcn: str) -> ParsedFQCN:
    """Parse a Fully Qualified Collection Name into components.

    Args:
        fqcn: The FQCN string (e.g., "amazon.aws.ec2_instance")

    Returns:
        ParsedFQCN with namespace, collection, and module_name

    Raises:
        InvalidFQCNError: If the FQCN format is invalid

    Examples:
        >>> parse_fqcn("amazon.aws.ec2_instance")
        ParsedFQCN(namespace='amazon', collection='aws', module_name='ec2_instance')

        >>> parse_fqcn("ansible.builtin.copy")
        ParsedFQCN(namespace='ansible', collection='builtin', module_name='copy')
    """
    if not fqcn:
        raise InvalidFQCNError(fqcn, "empty string")

    match = FQCN_PATTERN.match(fqcn)
    if not match:
        # Provide helpful error message
        parts = fqcn.split(".")
        if len(parts) != 3:
            hint = ""
            # If 2 parts and the first contains a dash, it's likely a hostname
            # that wasn't registered in inventory (e.g., "hello-ai3.shell")
            if len(parts) == 2 and "-" in parts[0]:
                hint = (
                    f". It looks like '{parts[0]}' may be a hostname — "
                    f"if so, register it with add_host before targeting it"
                )
            raise InvalidFQCNError(
                fqcn,
                f"expected 3 parts (namespace.collection.module), got {len(parts)}{hint}",
            )
        raise InvalidFQCNError(fqcn, "invalid characters in one or more parts")

    return ParsedFQCN(
        namespace=match.group(1),
        collection=match.group(2),
        module_name=match.group(3),
    )


def get_collection_paths(
    playbook_dir: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> list[Path]:
    """Get collection search paths in priority order.

    The search order is:
    1. Playbook-adjacent: ./collections/
    2. Extra paths (if provided)
    3. ANSIBLE_COLLECTIONS_PATH environment variable (if set)
    4. Default paths (~/.ansible/collections, /usr/share/ansible/collections)

    Args:
        playbook_dir: Optional playbook directory for playbook-adjacent collections
        extra_paths: Optional additional paths to search

    Returns:
        List of collection paths in search order
    """
    paths: list[Path] = []

    # 1. Playbook-adjacent collections (highest priority)
    if playbook_dir:
        playbook_collections = playbook_dir / "collections"
        if playbook_collections.exists():
            paths.append(playbook_collections)

    # Also check current working directory
    cwd_collections = Path.cwd() / "collections"
    if cwd_collections.exists() and cwd_collections not in paths:
        paths.append(cwd_collections)

    # 2. Extra paths
    if extra_paths:
        for p in extra_paths:
            if p not in paths:
                paths.append(p)

    # 3. Environment variable override
    env_paths = os.environ.get("ANSIBLE_COLLECTIONS_PATH")
    if env_paths:
        for p in env_paths.split(":"):
            path = Path(p)
            if path not in paths:
                paths.append(path)
    else:
        # 4. Default paths
        for default_path in DEFAULT_COLLECTION_PATHS:
            if default_path not in paths:
                paths.append(default_path)

    return paths


def find_ansible_builtin_path() -> Path | None:
    """Find the path to Ansible core builtin modules.

    Searches for Ansible's installation and returns the path to
    the builtin modules directory.

    Returns:
        Path to ansible builtin modules, or None if not found
    """
    global _ansible_builtin_path_cache

    if _ansible_builtin_path_cache is not None:
        return _ansible_builtin_path_cache

    # Try to find ansible package location
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import ansible; print(ansible.__file__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            ansible_init = Path(result.stdout.strip())
            # ansible/__init__.py -> ansible/modules/
            modules_path = ansible_init.parent / "modules"
            if modules_path.exists():
                _ansible_builtin_path_cache = modules_path
                return modules_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try common locations
    common_locations = [
        # Site-packages
        Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "modules",
        # User site-packages
        Path.home() / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "modules",
        # Homebrew on macOS
        Path("/opt/homebrew/lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "modules",
    ]

    for location in common_locations:
        if location.exists():
            _ansible_builtin_path_cache = location
            return location

    return None


def find_ansible_module_utils_path() -> Path | None:
    """Find the path to Ansible core module_utils.

    Searches for Ansible's module_utils installation and returns the path.
    This is separate from find_ansible_builtin_path() because module_utils
    may be installed in a different package.

    Returns:
        Path to ansible module_utils directory, or None if not found
    """
    global _ansible_module_utils_path_cache

    if _ansible_module_utils_path_cache is not None:
        return _ansible_module_utils_path_cache

    # Try to find ansible.module_utils package location
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from ansible import module_utils; print(module_utils.__file__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            module_utils_init = Path(result.stdout.strip())
            # module_utils/__init__.py -> module_utils/
            module_utils_path = module_utils_init.parent
            if module_utils_path.exists():
                _ansible_module_utils_path_cache = module_utils_path
                return module_utils_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: try to derive from builtin modules path
    builtin_path = find_ansible_builtin_path()
    if builtin_path is not None:
        # ansible/modules -> ansible/module_utils
        module_utils_path = builtin_path.parent / "module_utils"
        if module_utils_path.exists():
            _ansible_module_utils_path_cache = module_utils_path
            return module_utils_path

    # Try common locations
    common_locations = [
        Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "module_utils",
        Path.home() / ".local" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "module_utils",
        Path("/opt/homebrew/lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "ansible" / "module_utils",
    ]

    for location in common_locations:
        if location.exists():
            _ansible_module_utils_path_cache = location
            return location

    return None


def resolve_builtin_module(module_name: str) -> Path:
    """Resolve an ansible.builtin module to its file path.

    Args:
        module_name: The module name (e.g., "copy", "file")

    Returns:
        Path to the module file

    Raises:
        ModuleNotFoundError: If the module is not found
    """
    builtin_path = find_ansible_builtin_path()

    if builtin_path is None:
        raise ModuleNotFoundError(
            f"ansible.builtin.{module_name}",
            [Path("(ansible package not found)")],
        )

    # Ansible organizes builtin modules in subdirectories by category
    # e.g., ansible/modules/files/copy.py, ansible/modules/system/ping.py
    # We need to search all subdirectories

    # First, try direct path (for flat structure)
    direct_path = builtin_path / f"{module_name}.py"
    if direct_path.exists():
        return direct_path

    # Search subdirectories
    searched = [direct_path]
    for subdir in builtin_path.iterdir():
        if subdir.is_dir() and not subdir.name.startswith("_"):
            module_path = subdir / f"{module_name}.py"
            searched.append(module_path)
            if module_path.exists():
                return module_path

    raise ModuleNotFoundError(f"ansible.builtin.{module_name}", searched)


def resolve_collection_module(
    namespace: str,
    collection: str,
    module_name: str,
    collection_paths: list[Path] | None = None,
) -> Path:
    """Resolve a collection module to its file path.

    Args:
        namespace: Collection namespace (e.g., "amazon")
        collection: Collection name (e.g., "aws")
        module_name: Module name (e.g., "ec2_instance")
        collection_paths: Optional list of collection paths to search

    Returns:
        Path to the module file

    Raises:
        ModuleNotFoundError: If the module is not found
    """
    if collection_paths is None:
        collection_paths = get_collection_paths()

    searched: list[Path] = []
    fqcn = f"{namespace}.{collection}.{module_name}"

    for base_path in collection_paths:
        # Collection structure: ansible_collections/<ns>/<coll>/plugins/modules/<module>.py
        module_path = (
            base_path
            / "ansible_collections"
            / namespace
            / collection
            / "plugins"
            / "modules"
            / f"{module_name}.py"
        )
        searched.append(module_path)

        if module_path.exists():
            return module_path

    raise ModuleNotFoundError(fqcn, searched)


def resolve_fqcn(
    fqcn: str,
    playbook_dir: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> Path:
    """Resolve a FQCN to its module file path.

    Args:
        fqcn: The Fully Qualified Collection Name
        playbook_dir: Optional playbook directory for collection search
        extra_paths: Optional additional paths to search

    Returns:
        Absolute path to the module file

    Raises:
        InvalidFQCNError: If the FQCN format is invalid
        ModuleNotFoundError: If the module cannot be found

    Examples:
        >>> resolve_fqcn("ansible.builtin.copy")
        PosixPath('/path/to/ansible/modules/files/copy.py')

        >>> resolve_fqcn("amazon.aws.ec2_instance")
        PosixPath('/home/user/.ansible/collections/ansible_collections/amazon/aws/plugins/modules/ec2_instance.py')
    """
    parsed = parse_fqcn(fqcn)

    # Special case: ansible.builtin
    if parsed.namespace == "ansible" and parsed.collection == "builtin":
        return resolve_builtin_module(parsed.module_name)

    # Regular collection module
    collection_paths = get_collection_paths(playbook_dir, extra_paths)
    return resolve_collection_module(
        parsed.namespace,
        parsed.collection,
        parsed.module_name,
        collection_paths,
    )


def is_valid_fqcn(fqcn: str) -> bool:
    """Check if a string is a valid FQCN.

    Args:
        fqcn: The string to check

    Returns:
        True if valid FQCN format, False otherwise
    """
    try:
        parse_fqcn(fqcn)
        return True
    except InvalidFQCNError:
        return False
