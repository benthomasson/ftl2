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

        Hash includes all configuration parameters to ensure cache
        uniqueness across different gate configurations.

        Returns:
            Hex string of SHA256 hash

        Example:
            >>> config = GateBuildConfig(modules=["ping"])
            >>> hash1 = config.compute_hash()
            >>> hash2 = config.compute_hash()
            >>> hash1 == hash2
            True
        """
        inputs = []
        inputs.extend(self.modules)
        inputs.extend(str(d) for d in self.module_dirs)
        inputs.extend(self.dependencies)
        inputs.append(self.interpreter)

        hash_input = "".join(str(i) for i in inputs).encode()
        return hashlib.sha256(hash_input).hexdigest()


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

            # Install modules
            if config.modules:
                self._install_modules(config, module_dir)

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

    def _install_modules(self, config: GateBuildConfig, module_dir: Path) -> None:
        """Install modules into gate.

        Supports both simple module names (looked up in module_dirs) and
        FQCNs like "community.general.slack" (resolved via Ansible
        collection paths).

        Args:
            config: Gate build configuration
            module_dir: Directory to install modules into

        Raises:
            ModuleNotFound: If module cannot be found
        """
        for module in config.modules:
            # Try simple name lookup first
            module_path = find_module(config.module_dirs, module)

            if module_path is None:
                # Try FQCN resolution (explicit or ansible.builtin fallback)
                try:
                    from .module_loading.fqcn import resolve_fqcn

                    fqcn = module if "." in module else f"ansible.builtin.{module}"
                    module_path = resolve_fqcn(fqcn)
                    logger.debug(f"Resolved {module} via FQCN {fqcn} to {module_path}")
                except Exception as e:
                    raise ModuleNotFound(f"Cannot find {module}: {e}") from e

            # Copy module to gate
            target_path = module_dir / module_path.name
            module_content = module_path.read_bytes()
            target_path.write_bytes(module_content)

            logger.debug(f"Installed module {module} to {target_path}")

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
