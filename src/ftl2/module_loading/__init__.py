"""Module loading for FTL2.

Provides functionality to load and execute Ansible modules with
better performance by separating bundle building from param passing.
"""

from ftl2.module_loading.bundle import (
    Bundle,
    BundleCache,
    BundleInfo,
    build_bundle,
    build_bundle_from_fqcn,
    list_bundle_contents,
    verify_bundle,
)
from ftl2.module_loading.dependencies import (
    DependencyResult,
    ModuleUtilsImport,
    find_all_dependencies,
    find_module_utils_imports,
    find_module_utils_imports_from_file,
    resolve_module_util_import,
)
from ftl2.module_loading.executor import (
    ExecutionResult,
    ModuleExecutor,
    execute_bundle_local,
    execute_local,
    execute_local_fqcn,
    execute_remote,
    execute_remote_with_staging,
    get_module_utils_pythonpath,
    stage_bundle_remote,
)
from ftl2.module_loading.fqcn import (
    find_ansible_builtin_path,
    find_ansible_module_utils_path,
    get_collection_paths,
    parse_fqcn,
    resolve_fqcn,
)

__all__ = [
    # FQCN parsing
    "parse_fqcn",
    "get_collection_paths",
    "resolve_fqcn",
    "find_ansible_builtin_path",
    "find_ansible_module_utils_path",
    # Dependency detection
    "find_module_utils_imports",
    "find_module_utils_imports_from_file",
    "find_all_dependencies",
    "resolve_module_util_import",
    "ModuleUtilsImport",
    "DependencyResult",
    # Bundle building
    "build_bundle",
    "build_bundle_from_fqcn",
    "verify_bundle",
    "list_bundle_contents",
    "Bundle",
    "BundleInfo",
    "BundleCache",
    # Execution
    "ExecutionResult",
    "execute_local",
    "execute_local_fqcn",
    "execute_bundle_local",
    "execute_remote",
    "execute_remote_with_staging",
    "stage_bundle_remote",
    "get_module_utils_pythonpath",
    "ModuleExecutor",
]
