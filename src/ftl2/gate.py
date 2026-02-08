"""Gate building system for creating self-contained execution environments.

This module provides functionality for building FTL "gates" - self-contained
Python executable archives (.pyz) that enable remote automation execution.
Gates package modules, dependencies, and runtime components into portable
executables that can be deployed via SSH.

Key features:
- Hash-based caching to avoid redundant builds
- Self-contained .pyz executable creation using zipapp
- Dependency installation with pip
- Module packaging for remote execution
"""

import hashlib
import logging
import shutil
import sys
import tempfile
import zipapp
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CalledProcessError, check_output

from .exceptions import GateError, ModuleNotFound
from .utils import ensure_directory, find_module, read_module

logger = logging.getLogger(__name__)


def module_path_name(fqcn: str) -> str:
    """Extract the module name from a FQCN for use as a filename.

    Examples:
        "community.general.slack" -> "slack"
        "ansible.builtin.service" -> "service"
    """
    return fqcn.rsplit(".", 1)[-1]


@dataclass
class GateBuildConfig:
    """Configuration for building a gate executable.

    Bundles all parameters needed for gate construction, reducing
    function parameters and improving testability.

    Attributes:
        modules: List of module names to include in the gate
        module_dirs: List of directory paths to search for modules
        dependencies: List of Python packages to install via pip
        interpreter: Python interpreter path for target system (shebang)
        local_interpreter: Python interpreter for build operations

    Example:
        >>> config = GateBuildConfig(
        ...     modules=["ping", "setup"],
        ...     module_dirs=[Path("/opt/modules")],
        ...     dependencies=["requests>=2.0"],
        ...     interpreter="/usr/bin/python3"
        ... )
    """

    modules: list[str] = field(default_factory=list)
    module_dirs: list[Path] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    interpreter: str = sys.executable
    local_interpreter: str = sys.executable

    def __post_init__(self):
        """Convert string paths to Path objects."""
        self.module_dirs = [Path(d) if isinstance(d, str) else d for d in self.module_dirs]

    def compute_hash(self) -> str:
        """Compute SHA256 hash of gate configuration for caching.

        Hash includes configuration parameters AND gate source files
        to ensure cache invalidation when the gate infrastructure changes.

        Returns:
            Hex string of SHA256 hash
        """
        h = hashlib.sha256()

        # Config inputs
        for m in self.modules:
            h.update(m.encode())
        for d in self.module_dirs:
            h.update(str(d).encode())
        for dep in self.dependencies:
            h.update(dep.encode())
        h.update(self.interpreter.encode())

        # Gate source files — invalidate cache when gate code changes
        try:
            import ftl2
            ftl2_dir = Path(ftl2.__file__).parent
            for source_file in [
                ftl2_dir / "ftl_gate" / "__main__.py",
                ftl2_dir / "message.py",
                ftl2_dir / "ftl_modules" / "exceptions.py",
            ]:
                if source_file.exists():
                    h.update(source_file.read_bytes())
        except Exception:
            pass  # If we can't read source files, hash config only

        return h.hexdigest()


class GateBuilder:
    """Builder for creating self-contained gate executables.

    Manages gate construction with intelligent caching to avoid
    redundant builds. Uses composition pattern for clean separation
    of concerns.

    Attributes:
        cache_dir: Directory for storing built gates

    Example:
        >>> builder = GateBuilder()
        >>> config = GateBuildConfig(modules=["ping"])
        >>> gate_path, gate_hash = builder.build(config)
        >>> print(f"Built gate: {gate_path}")
    """

    def __init__(self, cache_dir: str | Path = "~/.ftl"):
        """Initialize the gate builder.

        Args:
            cache_dir: Directory for caching built gates
        """
        self.cache_dir = ensure_directory(Path(cache_dir))
        logger.debug(f"GateBuilder initialized with cache_dir={self.cache_dir}")

    def build(self, config: GateBuildConfig) -> tuple[str, str]:
        """Build a gate executable from configuration.

        Creates a self-contained Python executable archive containing
        modules, dependencies, and FTL runtime. Uses caching to avoid
        rebuilding identical configurations.

        Args:
            config: Gate build configuration

        Returns:
            Tuple of (gate_path, gate_hash) containing:
            - gate_path: Path to the built .pyz executable
            - gate_hash: SHA256 hash identifying this configuration

        Raises:
            ModuleNotFound: If a specified module cannot be found
            GateError: If gate construction fails

        Example:
            >>> builder = GateBuilder()
            >>> config = GateBuildConfig(
            ...     modules=["ping"],
            ...     module_dirs=[Path("/opt/modules")]
            ... )
            >>> gate_path, gate_hash = builder.build(config)
        """
        logger.debug(
            f"Building gate: modules={config.modules}, "
            f"module_dirs={config.module_dirs}, "
            f"dependencies={config.dependencies}, "
            f"interpreter={config.interpreter}"
        )

        # Compute configuration hash for caching
        gate_hash = config.compute_hash()
        cached_gate = self.cache_dir / f"ftl_gate_{gate_hash}.pyz"

        # Check cache first
        if cached_gate.exists():
            logger.info(f"Reusing cached gate: {cached_gate}")
            return str(cached_gate), gate_hash

        # Build new gate
        try:
            gate_path = self._build_new_gate(config, cached_gate)
            logger.info(f"Built new gate: {gate_path}")
            return gate_path, gate_hash

        except Exception as e:
            logger.exception(f"Failed to build gate: {e}")
            raise GateError(f"Gate construction failed: {e}") from e

    def _build_new_gate(self, config: GateBuildConfig, target_path: Path) -> str:
        """Build a new gate executable.

        Args:
            config: Gate build configuration
            target_path: Path where gate should be stored

        Returns:
            Path to the built gate

        Raises:
            ModuleNotFound: If module cannot be found
            GateError: If construction fails
        """
        tempdir = Path(tempfile.mkdtemp())

        try:
            # Create gate directory structure
            gate_dir = tempdir / "ftl_gate"
            gate_dir.mkdir()
            module_dir = gate_dir / "ftl_gate"
            module_dir.mkdir()

            # Create ftl2 package directory for message protocol
            ftl2_dir = gate_dir / "ftl2"
            ftl2_dir.mkdir()

            # Create __main__.py entry point
            self._create_main_entry(gate_dir)

            # Create package __init__.py files
            (module_dir / "__init__.py").write_text("")
            (ftl2_dir / "__init__.py").write_text("")

            # Copy message protocol module
            self._copy_message_module(ftl2_dir)

            # Copy FTL module exceptions (needed by FTL modules sent via FTLModule messages)
            self._copy_ftl_module_exceptions(ftl2_dir)

            # Install modules and their ansible dependencies
            if config.modules:
                self._install_modules(config, module_dir, gate_dir)

            # Install dependencies
            if config.dependencies:
                self._install_dependencies(config, gate_dir, tempdir)

            # Create executable archive
            archive_path = tempdir / "ftl_gate.pyz"
            zipapp.create_archive(str(gate_dir), str(archive_path), config.interpreter)

            # Copy to cache
            shutil.copy(archive_path, target_path)

            return str(target_path)

        finally:
            # Clean up temporary directory
            shutil.rmtree(tempdir, ignore_errors=True)

    def _create_main_entry(self, gate_dir: Path) -> None:
        """Create __main__.py entry point for gate.

        Args:
            gate_dir: Gate directory path

        Raises:
            GateError: If entry point creation fails
        """
        try:
            # Import the ftl_gate __main__.py from package resources
            try:
                # Python 3.9+
                from importlib.resources import files
            except ImportError:
                # Python 3.8 and earlier
                from importlib_resources import files  # type: ignore

            import ftl2.ftl_gate

            main_content = files(ftl2.ftl_gate).joinpath("__main__.py").read_text()
            (gate_dir / "__main__.py").write_text(main_content)

        except Exception as e:
            raise GateError(f"Failed to create gate entry point: {e}") from e

    def _install_modules(self, config: GateBuildConfig, module_dir: Path, gate_dir: Path) -> None:
        """Install modules and their dependencies into gate.

        Simple modules (found in module_dirs) are copied directly.
        Ansible modules (resolved via FQCN) have their module_utils
        dependencies resolved and merged into the gate's top-level
        directory so imports work without nested ZIPs.

        Args:
            config: Gate build configuration
            module_dir: Directory to install modules into (ftl_gate/)
            gate_dir: Top-level gate directory for dependencies

        Raises:
            ModuleNotFound: If module cannot be found
        """
        from .module_loading.fqcn import resolve_fqcn
        from .module_loading.dependencies import find_all_dependencies
        from .module_loading.bundle import get_archive_path

        # Collect all dependencies across all Ansible modules
        all_deps: dict[str, Path] = {}  # archive_path -> source_path

        for module in config.modules:
            # Try simple name lookup first (FTL2 built-in modules)
            module_path = find_module(config.module_dirs, module)

            if module_path is not None:
                # Simple module - copy directly
                target_path = module_dir / module_path.name
                target_path.write_bytes(module_path.read_bytes())
                logger.debug(f"Installed module {module} to {target_path}")
                continue

            # FQCN resolution (explicit or ansible.builtin fallback)
            try:
                fqcn = module if "." in module else f"ansible.builtin.{module}"
                module_path = resolve_fqcn(fqcn)
                logger.debug(f"Resolved {module} via FQCN {fqcn} to {module_path}")
            except Exception as e:
                raise ModuleNotFound(f"Cannot find {module}: {e}") from e

            # Copy module directly into gate
            target_name = f"{module_path_name(fqcn)}.py"
            target_path = module_dir / target_name
            target_path.write_bytes(module_path.read_bytes())
            logger.debug(f"Installed module {module} to {target_path}")

            # Resolve dependencies
            dep_result = find_all_dependencies(module_path)
            for dep_path in dep_result.dependencies:
                archive_path = get_archive_path(dep_path)
                if archive_path not in all_deps:
                    all_deps[archive_path] = dep_path

            logger.debug(
                f"Module {module}: {len(dep_result.dependencies)} deps, "
                f"{len(dep_result.unresolved)} unresolved"
            )

        # Install merged dependencies into the gate directory
        if all_deps:
            self._install_module_deps(gate_dir, all_deps)
            logger.info(f"Installed {len(all_deps)} merged module_utils dependencies")

    def _install_module_deps(self, gate_dir: Path, deps: dict[str, Path]) -> None:
        """Install module_utils dependencies into the gate directory.

        Creates the proper directory structure so imports like
        'from ansible.module_utils.basic import AnsibleModule' work.

        Args:
            gate_dir: Top-level gate directory
            deps: Mapping of archive_path -> source_path
        """
        # Track directories that need __init__.py
        dirs_needing_init: set[Path] = set()

        for archive_path, source_path in deps.items():
            target = gate_dir / archive_path
            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                target.write_bytes(source_path.read_bytes())
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to copy dependency {source_path}: {e}")
                continue

            # Track parent dirs for __init__.py creation
            rel = Path(archive_path)
            for i in range(len(rel.parts) - 1):
                dirs_needing_init.add(gate_dir / Path(*rel.parts[: i + 1]))

        # Add __init__.py for package directories
        for dir_path in dirs_needing_init:
            init_file = dir_path / "__init__.py"
            if not init_file.exists():
                init_file.write_text("")

    def _copy_message_module(self, ftl2_dir: Path) -> None:
        """Copy message protocol module into gate.

        Args:
            ftl2_dir: ftl2 package directory in gate

        Raises:
            GateError: If copy fails
        """
        try:
            # Get path to message.py in the installed package
            import ftl2

            ftl2_package_dir = Path(ftl2.__file__).parent
            message_path = ftl2_package_dir / "message.py"

            if not message_path.exists():
                raise GateError(f"message.py not found at {message_path}")

            # Copy message.py to gate
            shutil.copy(message_path, ftl2_dir / "message.py")
            logger.debug(f"Copied message module to {ftl2_dir}")

        except Exception as e:
            raise GateError(f"Failed to copy message module: {e}") from e

    def _copy_ftl_module_exceptions(self, ftl2_dir: Path) -> None:
        """Copy FTL module exceptions into gate.

        FTL modules sent via FTLModule messages import from
        ftl2.ftl_modules.exceptions. This makes that import
        available inside the gate.

        Args:
            ftl2_dir: ftl2 package directory in gate
        """
        try:
            import ftl2

            ftl2_package_dir = Path(ftl2.__file__).parent
            exceptions_path = ftl2_package_dir / "ftl_modules" / "exceptions.py"

            if not exceptions_path.exists():
                logger.warning(f"ftl_modules/exceptions.py not found at {exceptions_path}")
                return

            # Create ftl_modules subpackage
            ftl_modules_dir = ftl2_dir / "ftl_modules"
            ftl_modules_dir.mkdir(exist_ok=True)
            (ftl_modules_dir / "__init__.py").write_text("")

            shutil.copy(exceptions_path, ftl_modules_dir / "exceptions.py")
            logger.debug(f"Copied ftl_modules/exceptions to {ftl_modules_dir}")

        except Exception as e:
            logger.warning(f"Failed to copy ftl_modules exceptions: {e}")

    def _install_dependencies(self, config: GateBuildConfig, gate_dir: Path, tempdir: Path) -> None:
        """Install Python dependencies into gate using pip.

        Args:
            config: Gate build configuration
            gate_dir: Gate directory path
            tempdir: Temporary directory for build artifacts

        Raises:
            GateError: If dependency installation fails
        """
        # Create requirements.txt
        requirements_file = tempdir / "requirements.txt"
        requirements_file.write_text("\n".join(config.dependencies))

        # Run pip install
        command = [
            config.local_interpreter,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements_file),
            "--target",
            str(gate_dir),
        ]

        logger.debug(f"Installing dependencies: {' '.join(command)}")

        try:
            output = check_output(command, text=True)
            logger.debug(f"Pip output: {output}")

        except CalledProcessError as e:
            raise GateError(f"Failed to install dependencies: {e.output}") from e
