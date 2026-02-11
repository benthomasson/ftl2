"""Bundle builder for Ansible modules.

Creates executable ZIP bundles containing a module and all its
dependencies, with content-addressed caching for efficient transfer.
"""

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from ftl2.module_loading.dependencies import find_all_dependencies, DependencyResult
from ftl2.module_loading.fqcn import resolve_fqcn, find_ansible_builtin_path

logger = logging.getLogger(__name__)


def _get_ftl2_package_root() -> Path:
    """Get the root path of the ftl2 package."""
    import ftl2
    return Path(ftl2.__file__).parent


# FTL2 modules to include in bundles for module use
# These enable features like event streaming from bundled Ansible modules
FTL2_BUNDLE_INCLUDES = [
    "events.py",  # Event emission support for progress/log/data
]

# Entry point template for the bundle
# This makes the ZIP executable via: python bundle.zip
#
# Ansible modules read their arguments from AnsibleModule which reads from
# sys.argv[1] (file path) or stdin. We write the args to a temp file and
# pass that path as sys.argv[1].
MAIN_TEMPLATE = '''#!/usr/bin/env python
"""FTL2 module bundle entry point."""
import sys
import json
import tempfile
import os
import inspect

# Add bundle to path for imports
if sys.argv[0].endswith('.zip') or sys.argv[0].endswith('.pyz'):
    sys.path.insert(0, sys.argv[0])

if __name__ == "__main__":
    # Read params from stdin
    input_data = sys.stdin.read()

    try:
        data = json.loads(input_data if input_data else '{"ANSIBLE_MODULE_ARGS": {}}')
    except (json.JSONDecodeError, ValueError):
        data = {"ANSIBLE_MODULE_ARGS": {}}

    args = data.get("ANSIBLE_MODULE_ARGS", data)

    # Import the module
    from ftl2_module import main

    try:
        # Check if main() accepts arguments
        sig = inspect.signature(main)
        if sig.parameters:
            # FTL2-style: main(args) returns result dict
            result = main(args)
            if result is not None:
                print(json.dumps(result))
        else:
            # Ansible-style: main() reads args from file via sys.argv[1]
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write(input_data if input_data else '{"ANSIBLE_MODULE_ARGS": {}}')
                args_file = f.name
            sys.argv = [sys.argv[0], args_file]
            try:
                main()
            finally:
                try:
                    os.unlink(args_file)
                except OSError:
                    pass

    except Exception as e:
        print(json.dumps({"failed": True, "msg": str(e)}))
        sys.exit(1)
'''


@dataclass
class BundleInfo:
    """Information about a built bundle.

    Attributes:
        fqcn: The module's fully qualified collection name
        content_hash: SHA256 hash of bundle contents (first 12 chars)
        size: Size of bundle in bytes
        module_path: Path to the original module file
        dependency_count: Number of dependencies included
    """

    fqcn: str
    content_hash: str
    size: int
    module_path: Path
    dependency_count: int

    def __str__(self) -> str:
        return f"Bundle({self.fqcn}, hash={self.content_hash}, {self.size} bytes, {self.dependency_count} deps)"


@dataclass
class Bundle:
    """A built module bundle.

    Attributes:
        info: Bundle metadata
        data: The ZIP bundle as bytes
    """

    info: BundleInfo
    data: bytes

    def write_to_file(self, path: Path) -> None:
        """Write bundle to a file."""
        path.write_bytes(self.data)
        logger.info(f"Wrote bundle to {path}")

    def write_to_stream(self, stream: BinaryIO) -> None:
        """Write bundle to a binary stream."""
        stream.write(self.data)


def get_archive_path(file_path: Path, base_type: str = "core") -> str:
    """Get the archive path for a dependency file.

    Preserves the module_utils directory structure so imports work.

    Args:
        file_path: Path to the dependency file
        base_type: "core" for ansible core, "collection" for collections

    Returns:
        Path within the ZIP archive
    """
    path_str = str(file_path)

    # For core ansible module_utils
    if "ansible/module_utils" in path_str or "ansible\\module_utils" in path_str:
        # Find the ansible/ part and preserve from there
        parts = file_path.parts
        for i, part in enumerate(parts):
            if part == "ansible":
                return str(Path(*parts[i:]))

    # For collection module_utils
    if "ansible_collections" in path_str:
        parts = file_path.parts
        for i, part in enumerate(parts):
            if part == "ansible_collections":
                return str(Path(*parts[i:]))

    # Fallback: just use the filename
    return file_path.name


def build_bundle(
    module_path: Path,
    dependencies: list[Path] | DependencyResult | None = None,
    fqcn: str = "",
    collection_paths: list[Path] | None = None,
    strict_dependencies: bool = False,
) -> Bundle:
    """Build an executable ZIP bundle for a module.

    Args:
        module_path: Path to the module file
        dependencies: List of dependency paths, DependencyResult, or None to auto-detect
        fqcn: Optional FQCN for the module (used in metadata)
        collection_paths: Optional collection paths for dependency resolution
        strict_dependencies: If True, raise RuntimeError on unresolved dependencies

    Returns:
        Bundle containing the ZIP data and metadata
    """
    # Auto-detect dependencies if not provided
    if dependencies is None:
        dep_result = find_all_dependencies(module_path, collection_paths)
        dep_list = dep_result.dependencies
        if dep_result.unresolved:
            names = [u.import_path for u in dep_result.unresolved]
            msg = (
                f"Unresolved module_utils dependencies for "
                f"{fqcn or module_path.name}: {', '.join(names)}"
            )
            if strict_dependencies:
                raise RuntimeError(msg)
            logger.warning(msg)
    elif isinstance(dependencies, DependencyResult):
        dep_list = dependencies.dependencies
    else:
        dep_list = dependencies

    logger.debug(f"Building bundle for {module_path} with {len(dep_list)} dependencies")

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add the module itself as ftl2_module.py
        module_source = module_path.read_text()
        zf.writestr("ftl2_module.py", module_source)

        # Add dependencies with correct paths for imports
        added_paths: set[str] = set()
        for dep_path in dep_list:
            archive_path = get_archive_path(dep_path)

            # Avoid duplicates
            if archive_path in added_paths:
                continue
            added_paths.add(archive_path)

            try:
                dep_source = dep_path.read_text()
                zf.writestr(archive_path, dep_source)
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to add dependency {dep_path}: {e}")

        # Add FTL2 support modules (e.g., events.py for progress reporting)
        _add_ftl2_support_modules(zf, added_paths)

        # Add __init__.py files for package structure
        _add_package_init_files(zf, added_paths)

        # Add entry point
        zf.writestr("__main__.py", MAIN_TEMPLATE)

    zip_bytes = buffer.getvalue()
    content_hash = hashlib.sha256(zip_bytes).hexdigest()[:12]

    info = BundleInfo(
        fqcn=fqcn or module_path.stem,
        content_hash=content_hash,
        size=len(zip_bytes),
        module_path=module_path,
        dependency_count=len(dep_list),
    )

    logger.info(f"Built bundle: {info}")

    return Bundle(info=info, data=zip_bytes)


def _add_ftl2_support_modules(zf: zipfile.ZipFile, added_paths: set[str]) -> None:
    """Add FTL2 support modules to the bundle.

    These modules enable features like event streaming from within
    bundled Ansible modules. They are added under the ftl2/ namespace.
    """
    ftl2_root = _get_ftl2_package_root()

    for module_file in FTL2_BUNDLE_INCLUDES:
        source_path = ftl2_root / module_file
        if not source_path.exists():
            logger.warning(f"FTL2 support module not found: {source_path}")
            continue

        # Add under ftl2/ namespace
        archive_path = f"ftl2/{module_file}"
        if archive_path in added_paths:
            continue

        try:
            content = source_path.read_text()
            zf.writestr(archive_path, content)
            added_paths.add(archive_path)
            logger.debug(f"Added FTL2 support module: {archive_path}")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to add FTL2 module {module_file}: {e}")


def _add_package_init_files(zf: zipfile.ZipFile, added_paths: set[str]) -> None:
    """Add __init__.py files for all package directories.

    This ensures that imports like 'from ansible.module_utils.X import Y'
    work correctly from within the ZIP.
    """
    # Collect all directories that need __init__.py
    dirs_needing_init: set[str] = set()

    for path in added_paths:
        parts = Path(path).parts[:-1]  # All parent directories
        for i in range(len(parts)):
            dir_path = str(Path(*parts[: i + 1]))
            dirs_needing_init.add(dir_path)

    # Add __init__.py for each directory if not already present
    for dir_path in sorted(dirs_needing_init):
        init_path = f"{dir_path}/__init__.py"
        if init_path not in added_paths:
            # Check if it already exists in the archive
            try:
                zf.getinfo(init_path)
            except KeyError:
                zf.writestr(init_path, "# Auto-generated package init\n")


def build_bundle_from_fqcn(
    fqcn: str,
    playbook_dir: Path | None = None,
    extra_paths: list[Path] | None = None,
    strict_dependencies: bool = False,
) -> Bundle:
    """Build a bundle from a FQCN.

    Convenience function that resolves the FQCN and builds the bundle.

    Args:
        fqcn: Fully qualified collection name
        playbook_dir: Optional playbook directory for collection search
        extra_paths: Optional additional collection paths
        strict_dependencies: If True, raise RuntimeError on unresolved dependencies

    Returns:
        Bundle containing the ZIP data and metadata
    """
    module_path = resolve_fqcn(fqcn, playbook_dir, extra_paths)

    # Use same paths for dependency resolution
    collection_paths = []
    if playbook_dir:
        collection_paths.append(playbook_dir / "collections")
    if extra_paths:
        collection_paths.extend(extra_paths)

    return build_bundle(
        module_path,
        fqcn=fqcn,
        collection_paths=collection_paths if collection_paths else None,
        strict_dependencies=strict_dependencies,
    )


class BundleCache:
    """Cache of built bundles, keyed by FQCN.

    Bundles are built once and reused for all hosts.
    """

    def __init__(self) -> None:
        self._bundles: dict[str, Bundle] = {}
        self._by_hash: dict[str, Bundle] = {}

    def get(self, fqcn: str) -> Bundle | None:
        """Get a cached bundle by FQCN."""
        return self._bundles.get(fqcn)

    def get_by_hash(self, content_hash: str) -> Bundle | None:
        """Get a cached bundle by content hash."""
        return self._by_hash.get(content_hash)

    def add(self, bundle: Bundle) -> None:
        """Add a bundle to the cache."""
        self._bundles[bundle.info.fqcn] = bundle
        self._by_hash[bundle.info.content_hash] = bundle

    def get_or_build(
        self,
        fqcn: str,
        playbook_dir: Path | None = None,
        extra_paths: list[Path] | None = None,
        strict_dependencies: bool = False,
    ) -> Bundle:
        """Get a cached bundle or build a new one.

        Args:
            fqcn: Fully qualified collection name
            playbook_dir: Optional playbook directory
            extra_paths: Optional additional collection paths
            strict_dependencies: If True, raise RuntimeError on unresolved dependencies

        Returns:
            Cached or newly built bundle
        """
        if fqcn in self._bundles:
            logger.debug(f"Cache hit for {fqcn}")
            return self._bundles[fqcn]

        logger.debug(f"Cache miss for {fqcn}, building bundle")
        bundle = build_bundle_from_fqcn(fqcn, playbook_dir, extra_paths, strict_dependencies)
        self.add(bundle)
        return bundle

    def get_or_build_from_path(
        self,
        module_path: Path,
        fqcn: str = "",
        collection_paths: list[Path] | None = None,
    ) -> Bundle:
        """Get a cached bundle or build from a module path.

        Args:
            module_path: Path to the module file
            fqcn: Optional FQCN (uses module name if not provided)
            collection_paths: Optional collection paths for dependency resolution

        Returns:
            Cached or newly built bundle
        """
        key = fqcn or str(module_path)

        if key in self._bundles:
            logger.debug(f"Cache hit for {key}")
            return self._bundles[key]

        logger.debug(f"Cache miss for {key}, building bundle")
        bundle = build_bundle(module_path, fqcn=fqcn, collection_paths=collection_paths)

        # Store under both key and FQCN
        self._bundles[key] = bundle
        if fqcn:
            self._bundles[fqcn] = bundle
        self._by_hash[bundle.info.content_hash] = bundle

        return bundle

    def clear(self) -> None:
        """Clear all cached bundles."""
        self._bundles.clear()
        self._by_hash.clear()

    def __len__(self) -> int:
        """Return number of cached bundles."""
        return len(self._bundles)

    def __contains__(self, fqcn: str) -> bool:
        """Check if FQCN is in cache."""
        return fqcn in self._bundles

    @property
    def bundles(self) -> dict[str, Bundle]:
        """Get all cached bundles."""
        return self._bundles.copy()

    @property
    def total_size(self) -> int:
        """Get total size of all cached bundles."""
        return sum(b.info.size for b in self._by_hash.values())


def verify_bundle(bundle: Bundle) -> bool:
    """Verify that a bundle is valid and executable.

    Args:
        bundle: The bundle to verify

    Returns:
        True if bundle is valid, False otherwise
    """
    try:
        buffer = io.BytesIO(bundle.data)
        with zipfile.ZipFile(buffer, "r") as zf:
            # Check required files exist
            names = zf.namelist()
            if "__main__.py" not in names:
                logger.error("Bundle missing __main__.py")
                return False
            if "ftl2_module.py" not in names:
                logger.error("Bundle missing ftl2_module.py")
                return False

            # Verify ZIP integrity
            bad_file = zf.testzip()
            if bad_file is not None:
                logger.error(f"Bundle has corrupt file: {bad_file}")
                return False

        return True

    except zipfile.BadZipFile as e:
        logger.error(f"Invalid ZIP file: {e}")
        return False


def list_bundle_contents(bundle: Bundle) -> list[str]:
    """List all files in a bundle.

    Args:
        bundle: The bundle to inspect

    Returns:
        List of file paths in the bundle
    """
    buffer = io.BytesIO(bundle.data)
    with zipfile.ZipFile(buffer, "r") as zf:
        return zf.namelist()
