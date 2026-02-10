"""Dependency detection for Ansible modules.

Uses AST analysis to find module_utils imports and resolve them
to file paths, including transitive dependencies.
"""

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ftl2.module_loading.fqcn import (
    get_collection_paths,
    find_ansible_builtin_path,
    find_ansible_module_utils_path,
)

logger = logging.getLogger(__name__)


@dataclass
class ModuleUtilsImport:
    """Represents a module_utils import.

    Attributes:
        import_path: The full import path (e.g., "ansible.module_utils.basic")
        is_collection: Whether this is a collection module_utils
        namespace: Collection namespace (if collection import)
        collection: Collection name (if collection import)
        module_path: The module_utils path within the package
    """

    import_path: str
    is_collection: bool = False
    namespace: str = ""
    collection: str = ""
    module_path: str = ""

    def __post_init__(self) -> None:
        """Parse the import path to extract components."""
        if self.import_path.startswith("ansible_collections."):
            self._parse_collection_import()
        elif self.import_path.startswith("ansible.module_utils."):
            self._parse_core_import()

    def _parse_collection_import(self) -> None:
        """Parse a collection module_utils import."""
        # Format: ansible_collections.<ns>.<coll>.plugins.module_utils.<path>
        parts = self.import_path.split(".")
        if len(parts) >= 6 and parts[4] == "module_utils":
            self.is_collection = True
            self.namespace = parts[1]
            self.collection = parts[2]
            self.module_path = ".".join(parts[5:])

    def _parse_core_import(self) -> None:
        """Parse a core ansible module_utils import."""
        # Format: ansible.module_utils.<path>
        parts = self.import_path.split(".")
        if len(parts) >= 3:
            self.is_collection = False
            self.module_path = ".".join(parts[2:])


class ModuleUtilsFinder(ast.NodeVisitor):
    """AST visitor to find module_utils imports."""

    def __init__(self, current_package: str = "") -> None:
        """Initialize the finder.

        Args:
            current_package: The package path of the file being parsed,
                used to resolve relative imports. For example, if parsing
                ansible/module_utils/basic.py, this would be "ansible.module_utils".
        """
        self.imports: list[ModuleUtilsImport] = []
        self.current_package = current_package

    def _resolve_relative_import(self, module: str, level: int) -> str | None:
        """Resolve a relative import to an absolute module path.

        Args:
            module: The module name (e.g., "_internal" for "from ._internal import ...")
            level: The number of dots (1 for ".", 2 for "..", etc.)

        Returns:
            Absolute module path, or None if cannot resolve
        """
        if not self.current_package:
            return None

        parts = self.current_package.split(".")

        # Go up 'level - 1' directories (level=1 means current package)
        if level > len(parts):
            return None

        base_parts = parts[: len(parts) - level + 1]
        base = ".".join(base_parts)

        if module:
            return f"{base}.{module}"
        return base

    def visit_Import(self, node: ast.Import) -> None:
        """Handle 'import X' statements."""
        for alias in node.names:
            if "module_utils" in alias.name:
                self.imports.append(ModuleUtilsImport(alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Handle 'from X import Y' statements.

        Handles both absolute and relative imports:
        - Absolute: from ansible.module_utils.basic import AnsibleModule
        - Relative: from ._internal import _traceback (within module_utils)

        For each imported name, we check if it could be a submodule:
        - Names starting with underscore are likely private submodules
        - Names that are lowercase and don't look like classes might be modules
        - We add them all as potential submodules; resolution will filter out non-existent ones
        """
        module = node.module or ""
        level = node.level  # 0 for absolute, 1+ for relative

        # Handle relative imports
        if level > 0:
            resolved = self._resolve_relative_import(module, level)
            if resolved and "module_utils" in resolved:
                # Add the base module
                self.imports.append(ModuleUtilsImport(resolved))

        # Handle absolute imports
        elif module and "module_utils" in module:
            # Add the base module
            self.imports.append(ModuleUtilsImport(module))

        self.generic_visit(node)


def find_module_utils_imports(
    source: str,
    current_package: str = "",
) -> list[ModuleUtilsImport]:
    """Find all module_utils imports in Python source code.

    Args:
        source: Python source code as string
        current_package: The package path of the source file, used to
            resolve relative imports

    Returns:
        List of ModuleUtilsImport objects

    Example:
        >>> source = '''
        ... from ansible.module_utils.basic import AnsibleModule
        ... from ansible.module_utils.common.text.converters import to_text
        ... '''
        >>> imports = find_module_utils_imports(source)
        >>> [i.import_path for i in imports]
        ['ansible.module_utils.basic', 'ansible.module_utils.common.text.converters']
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logger.warning(f"Failed to parse Python source: {e}")
        return []

    finder = ModuleUtilsFinder(current_package)
    finder.visit(tree)
    return finder.imports


def _get_package_from_path(file_path: Path) -> str:
    """Determine the package path from a file path.

    For files in module_utils, extracts the package path so relative
    imports can be resolved.

    Args:
        file_path: Path to a Python file

    Returns:
        Package path (e.g., "ansible.module_utils" for basic.py)
    """
    parts = file_path.parts

    # Look for module_utils in the path
    try:
        mu_idx = parts.index("module_utils")
    except ValueError:
        return ""

    # Build package path from ansible/ansible_collections to the file's directory
    # Find the start (ansible or ansible_collections)
    start_idx = None
    for i, part in enumerate(parts):
        if part in ("ansible", "ansible_collections"):
            start_idx = i
            break

    if start_idx is None:
        return ""

    # Build the package path up to and including the file's parent directory
    # For basic.py in ansible/module_utils/, this gives "ansible.module_utils"
    # For __init__.py in ansible/module_utils/_internal/, this gives "ansible.module_utils._internal"
    file_name = file_path.name
    if file_name == "__init__.py":
        # Package init file - package is the directory
        package_parts = parts[start_idx:-1]
    else:
        # Regular module - package is the parent directory
        package_parts = parts[start_idx:-1]

    return ".".join(package_parts)


def find_module_utils_imports_from_file(file_path: Path) -> list[ModuleUtilsImport]:
    """Find all module_utils imports in a Python file.

    Args:
        file_path: Path to Python file

    Returns:
        List of ModuleUtilsImport objects
    """
    try:
        source = file_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return []

    current_package = _get_package_from_path(file_path)
    return find_module_utils_imports(source, current_package)


def resolve_core_module_util(module_path: str) -> Path | None:
    """Resolve a core ansible module_utils import to file path.

    Args:
        module_path: The module path (e.g., "basic" or "common.text.converters")

    Returns:
        Path to the module_utils file, or None if not found
    """
    module_utils_base = find_ansible_module_utils_path()
    if module_utils_base is None:
        return None

    # Special case: six.moves.* is a virtual module created at runtime by six
    # The "moves" module doesn't exist as a file - it's generated dynamically
    # to provide Python 2/3 compatibility. Just return the base six package.
    if module_path.startswith("six.moves"):
        six_init = module_utils_base / "six" / "__init__.py"
        if six_init.exists():
            return six_init
        return None

    # Convert dotted path to file path
    parts = module_path.split(".")

    # Try as package (__init__.py)
    package_path = module_utils_base / "/".join(parts) / "__init__.py"
    if package_path.exists():
        return package_path

    # Try as module (.py file)
    module_file = module_utils_base / "/".join(parts[:-1]) / f"{parts[-1]}.py" if len(parts) > 1 else module_utils_base / f"{parts[0]}.py"
    if module_file.exists():
        return module_file

    # Try direct path
    direct_path = module_utils_base / f"{'/'.join(parts)}.py"
    if direct_path.exists():
        return direct_path

    return None


def resolve_collection_module_util(
    namespace: str,
    collection: str,
    module_path: str,
    collection_paths: list[Path] | None = None,
) -> Path | None:
    """Resolve a collection module_utils import to file path.

    Args:
        namespace: Collection namespace
        collection: Collection name
        module_path: The module path within module_utils
        collection_paths: Optional list of collection paths to search

    Returns:
        Path to the module_utils file, or None if not found
    """
    if collection_paths is None:
        collection_paths = get_collection_paths()

    parts = module_path.split(".")

    for base_path in collection_paths:
        module_utils_base = (
            base_path
            / "ansible_collections"
            / namespace
            / collection
            / "plugins"
            / "module_utils"
        )

        if not module_utils_base.exists():
            continue

        # Try as package (__init__.py)
        package_path = module_utils_base / "/".join(parts) / "__init__.py"
        if package_path.exists():
            return package_path

        # Try as module (.py file)
        if len(parts) > 1:
            module_file = module_utils_base / "/".join(parts[:-1]) / f"{parts[-1]}.py"
        else:
            module_file = module_utils_base / f"{parts[0]}.py"

        if module_file.exists():
            return module_file

    return None


def resolve_module_util_import(
    imp: ModuleUtilsImport,
    collection_paths: list[Path] | None = None,
) -> Path | None:
    """Resolve a module_utils import to its file path.

    Args:
        imp: The ModuleUtilsImport to resolve
        collection_paths: Optional list of collection paths

    Returns:
        Path to the module_utils file, or None if not found
    """
    if imp.is_collection:
        return resolve_collection_module_util(
            imp.namespace,
            imp.collection,
            imp.module_path,
            collection_paths,
        )
    else:
        return resolve_core_module_util(imp.module_path)


@dataclass
class DependencyResult:
    """Result of dependency detection.

    Attributes:
        module_path: Path to the original module
        dependencies: List of resolved dependency paths
        unresolved: List of imports that could not be resolved
        all_imports: All imports found (including transitive)
    """

    module_path: Path
    dependencies: list[Path] = field(default_factory=list)
    unresolved: list[ModuleUtilsImport] = field(default_factory=list)
    all_imports: list[ModuleUtilsImport] = field(default_factory=list)

    def __iter__(self) -> Iterator[Path]:
        """Iterate over resolved dependencies."""
        return iter(self.dependencies)

    def __len__(self) -> int:
        """Return number of resolved dependencies."""
        return len(self.dependencies)


def find_all_dependencies(
    module_path: Path,
    collection_paths: list[Path] | None = None,
    max_depth: int = 50,
) -> DependencyResult:
    """Find all module_utils dependencies for a module (transitive).

    Uses AST analysis to detect imports and resolves them recursively.
    Handles circular imports by tracking visited files.

    Args:
        module_path: Path to the module file
        collection_paths: Optional list of collection paths
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        DependencyResult with resolved and unresolved dependencies

    Example:
        >>> result = find_all_dependencies(Path("/path/to/module.py"))
        >>> for dep in result.dependencies:
        ...     print(dep)
    """
    result = DependencyResult(module_path=module_path)

    seen_paths: set[Path] = set()
    seen_imports: set[str] = set()
    to_process: list[tuple[Path, int]] = [(module_path, 0)]

    while to_process:
        current_path, depth = to_process.pop()

        if depth > max_depth:
            logger.warning(f"Max dependency depth ({max_depth}) reached")
            continue

        if current_path in seen_paths:
            continue

        seen_paths.add(current_path)

        # Find imports in current file
        imports = find_module_utils_imports_from_file(current_path)

        for imp in imports:
            # Skip if we've already processed this import
            if imp.import_path in seen_imports:
                continue

            seen_imports.add(imp.import_path)
            result.all_imports.append(imp)

            # Resolve the import to a file path
            dep_path = resolve_module_util_import(imp, collection_paths)

            if dep_path is None:
                result.unresolved.append(imp)
                logger.debug(f"Could not resolve: {imp.import_path}")
            elif dep_path not in seen_paths:
                result.dependencies.append(dep_path)
                # Queue for transitive dependency scanning
                to_process.append((dep_path, depth + 1))

    return result


def get_dependency_tree(
    module_path: Path,
    collection_paths: list[Path] | None = None,
) -> dict[str, list[str]]:
    """Get a dependency tree showing which files import which.

    Args:
        module_path: Path to the module file
        collection_paths: Optional list of collection paths

    Returns:
        Dictionary mapping file paths to their direct imports
    """
    tree: dict[str, list[str]] = {}
    seen: set[Path] = set()
    to_process = [module_path]

    while to_process:
        current = to_process.pop()

        if current in seen:
            continue
        seen.add(current)

        imports = find_module_utils_imports_from_file(current)
        direct_deps = []

        for imp in imports:
            dep_path = resolve_module_util_import(imp, collection_paths)
            if dep_path:
                direct_deps.append(str(dep_path))
                if dep_path not in seen:
                    to_process.append(dep_path)

        tree[str(current)] = direct_deps

    return tree
