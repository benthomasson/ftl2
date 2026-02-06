"""Automatic backup functionality for FTL2.

Provides automatic backup creation before destructive operations,
with support for backup discovery, creation, listing, and restoration.
"""

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default backup directory for central storage
DEFAULT_BACKUP_DIR = Path.home() / ".ftl2" / "backups"


@dataclass
class BackupPath:
    """Information about a path that needs backup.

    Attributes:
        path: The file or directory path
        operation: Type of operation (delete, modify, create)
        exists: Whether the path currently exists
        size: Size in bytes if file exists
    """

    path: str
    operation: str  # "delete", "modify", "create"
    exists: bool = False
    size: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "path": self.path,
            "operation": self.operation,
            "exists": self.exists,
            "size": self.size,
        }


@dataclass
class BackupResult:
    """Result of a backup operation.

    Attributes:
        original: Original file path
        backup: Backup file path
        size: Size of backed up file in bytes
        timestamp: When the backup was created
        success: Whether backup was successful
        error: Error message if backup failed
    """

    original: str
    backup: str
    size: int = 0
    timestamp: str = ""
    success: bool = True
    error: str = ""

    def __post_init__(self) -> None:
        """Set timestamp if not provided."""
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "original": self.original,
            "backup": self.backup,
            "size": self.size,
            "timestamp": self.timestamp,
            "success": self.success,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class BackupInfo:
    """Information about an existing backup.

    Attributes:
        original: Original file path
        backup: Backup file path
        size: Size in bytes
        timestamp: When backup was created (from filename)
        is_directory: Whether this is a directory backup
    """

    original: str
    backup: str
    size: int
    timestamp: datetime
    is_directory: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "original": self.original,
            "backup": self.backup,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "is_directory": self.is_directory,
        }


def generate_backup_path(original_path: str, backup_dir: Path | None = None) -> str:
    """Generate a timestamped backup path for a file.

    Args:
        original_path: Path to the original file
        backup_dir: Optional central backup directory

    Returns:
        Path for the backup file
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if backup_dir:
        # Central backup directory: preserve directory structure
        backup_dir.mkdir(parents=True, exist_ok=True)
        # Remove leading slash and create relative path
        rel_path = original_path.lstrip("/")
        backup_name = f"{rel_path}.ftl2-backup-{timestamp}"
        return str(backup_dir / backup_name)
    else:
        # Adjacent backup: same directory as original
        return f"{original_path}.ftl2-backup-{timestamp}"


def parse_backup_timestamp(backup_path: str) -> datetime | None:
    """Parse the timestamp from a backup filename.

    Args:
        backup_path: Path to the backup file

    Returns:
        Datetime object or None if not parseable
    """
    import re

    match = re.search(r"\.ftl2-backup-(\d{8})-(\d{6})$", backup_path)
    if match:
        date_str = match.group(1)
        time_str = match.group(2)
        try:
            return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def get_original_path(backup_path: str) -> str:
    """Get the original path from a backup filename.

    Args:
        backup_path: Path to the backup file

    Returns:
        Original file path
    """
    import re

    return re.sub(r"\.ftl2-backup-\d{8}-\d{6}$", "", backup_path)


class BackupManager:
    """Manages file backups before destructive operations."""

    def __init__(self, backup_dir: Path | None = None, enabled: bool = True):
        """Initialize the backup manager.

        Args:
            backup_dir: Optional central backup directory (None = adjacent backups)
            enabled: Whether backups are enabled
        """
        self.backup_dir = backup_dir
        self.enabled = enabled
        self._created_backups: list[BackupResult] = []

    def should_backup(
        self,
        module_backup_capable: bool,
        module_backup_triggers: list[str],
        operation: str,
    ) -> bool:
        """Determine if a backup should be created.

        Args:
            module_backup_capable: Whether the module supports backups
            module_backup_triggers: List of operations that trigger backup
            operation: The operation being performed

        Returns:
            True if backup should be created
        """
        if not self.enabled:
            return False
        if not module_backup_capable:
            return False
        return operation in module_backup_triggers

    def discover_backup_paths(
        self,
        module_args: dict[str, Any],
        backup_path_args: list[str],
        operation: str,
    ) -> list[BackupPath]:
        """Discover paths that need backup based on module arguments.

        Args:
            module_args: Arguments passed to the module
            backup_path_args: Argument names that contain paths to backup
            operation: The operation type (delete, modify, etc.)

        Returns:
            List of BackupPath objects for paths that need backup
        """
        paths = []

        for arg_name in backup_path_args:
            path_value = module_args.get(arg_name)
            if not path_value:
                continue

            # Check if path exists and get size
            try:
                path_obj = Path(path_value)
                exists = path_obj.exists()
                size = 0
                if exists and path_obj.is_file():
                    size = path_obj.stat().st_size
                elif exists and path_obj.is_dir():
                    # Sum up directory size
                    size = sum(f.stat().st_size for f in path_obj.rglob("*") if f.is_file())

                paths.append(BackupPath(
                    path=str(path_value),
                    operation=operation,
                    exists=exists,
                    size=size,
                ))
            except OSError as e:
                logger.warning(f"Failed to check path {path_value}: {e}")
                paths.append(BackupPath(
                    path=str(path_value),
                    operation=operation,
                    exists=False,
                    size=0,
                ))

        return paths

    def create_backup(self, path: str) -> BackupResult:
        """Create a backup of a file or directory.

        Args:
            path: Path to the file or directory to backup

        Returns:
            BackupResult with backup information
        """
        path_obj = Path(path)

        if not path_obj.exists():
            return BackupResult(
                original=path,
                backup="",
                success=False,
                error="Path does not exist",
            )

        backup_path = generate_backup_path(path, self.backup_dir)

        try:
            # Create parent directories if using central backup dir
            if self.backup_dir:
                Path(backup_path).parent.mkdir(parents=True, exist_ok=True)

            if path_obj.is_dir():
                shutil.copytree(path, backup_path)
                size = sum(f.stat().st_size for f in Path(backup_path).rglob("*") if f.is_file())
            else:
                shutil.copy2(path, backup_path)
                size = Path(backup_path).stat().st_size

            result = BackupResult(
                original=path,
                backup=backup_path,
                size=size,
                success=True,
            )
            self._created_backups.append(result)
            logger.info(f"Created backup: {path} -> {backup_path}")
            return result

        except OSError as e:
            logger.error(f"Failed to create backup for {path}: {e}")
            return BackupResult(
                original=path,
                backup=backup_path,
                success=False,
                error=str(e),
            )

    def create_backups(self, paths: list[BackupPath]) -> list[BackupResult]:
        """Create backups for multiple paths.

        Args:
            paths: List of BackupPath objects

        Returns:
            List of BackupResult objects
        """
        results = []
        for bp in paths:
            if bp.exists:
                result = self.create_backup(bp.path)
                results.append(result)
        return results

    def get_created_backups(self) -> list[BackupResult]:
        """Get list of backups created in this session."""
        return self._created_backups.copy()

    def clear_created_backups(self) -> None:
        """Clear the list of created backups."""
        self._created_backups.clear()


def list_backups(
    original_path: str | None = None,
    backup_dir: Path | None = None,
) -> list[BackupInfo]:
    """List all backups, optionally filtered by original path.

    Args:
        original_path: Optional path to filter backups for
        backup_dir: Optional central backup directory to search

    Returns:
        List of BackupInfo objects sorted by timestamp (newest first)
    """
    backups = []

    if backup_dir and backup_dir.exists():
        # Search central backup directory
        for backup_path in backup_dir.rglob("*.ftl2-backup-*"):
            ts = parse_backup_timestamp(str(backup_path))
            if ts is None:
                continue

            orig = get_original_path(str(backup_path))
            # Convert back to absolute path
            if not orig.startswith("/"):
                orig = "/" + orig

            if original_path and orig != original_path:
                continue

            size = backup_path.stat().st_size if backup_path.is_file() else 0
            backups.append(BackupInfo(
                original=orig,
                backup=str(backup_path),
                size=size,
                timestamp=ts,
                is_directory=backup_path.is_dir(),
            ))
    elif original_path:
        # Search for adjacent backups
        parent = Path(original_path).parent
        if parent.exists():
            pattern = f"{Path(original_path).name}.ftl2-backup-*"
            for backup_path in parent.glob(pattern):
                ts = parse_backup_timestamp(str(backup_path))
                if ts is None:
                    continue

                size = backup_path.stat().st_size if backup_path.is_file() else 0
                backups.append(BackupInfo(
                    original=original_path,
                    backup=str(backup_path),
                    size=size,
                    timestamp=ts,
                    is_directory=backup_path.is_dir(),
                ))

    # Sort by timestamp, newest first
    backups.sort(key=lambda b: b.timestamp, reverse=True)
    return backups


def restore_backup(backup_path: str, force: bool = False) -> BackupResult:
    """Restore a file from backup.

    Args:
        backup_path: Path to the backup file
        force: Whether to overwrite existing file

    Returns:
        BackupResult with restoration status
    """
    backup = Path(backup_path)

    if not backup.exists():
        return BackupResult(
            original="",
            backup=backup_path,
            success=False,
            error="Backup file does not exist",
        )

    original_path = get_original_path(backup_path)
    original = Path(original_path)

    if original.exists() and not force:
        return BackupResult(
            original=original_path,
            backup=backup_path,
            success=False,
            error="Target path exists. Use --force to overwrite.",
        )

    try:
        # Remove existing if force
        if original.exists():
            if original.is_dir():
                shutil.rmtree(original)
            else:
                original.unlink()

        # Restore from backup
        if backup.is_dir():
            shutil.copytree(backup, original)
        else:
            shutil.copy2(backup, original)

        size = original.stat().st_size if original.is_file() else 0
        logger.info(f"Restored: {backup_path} -> {original_path}")

        return BackupResult(
            original=original_path,
            backup=backup_path,
            size=size,
            success=True,
        )

    except OSError as e:
        logger.error(f"Failed to restore {backup_path}: {e}")
        return BackupResult(
            original=original_path,
            backup=backup_path,
            success=False,
            error=str(e),
        )


def delete_backup(backup_path: str) -> bool:
    """Delete a backup file.

    Args:
        backup_path: Path to the backup file

    Returns:
        True if deleted, False otherwise
    """
    backup = Path(backup_path)

    if not backup.exists():
        return False

    try:
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()
        logger.info(f"Deleted backup: {backup_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to delete backup {backup_path}: {e}")
        return False


def prune_backups(
    original_path: str | None = None,
    keep: int | None = None,
    older_than_days: int | None = None,
    backup_dir: Path | None = None,
) -> list[str]:
    """Prune old backups.

    Args:
        original_path: Optional path to prune backups for
        keep: Number of most recent backups to keep
        older_than_days: Delete backups older than this many days
        backup_dir: Optional central backup directory

    Returns:
        List of deleted backup paths
    """
    from datetime import timedelta

    backups = list_backups(original_path, backup_dir)
    deleted = []

    if not backups:
        return deleted

    cutoff_date = None
    if older_than_days is not None:
        cutoff_date = datetime.now() - timedelta(days=older_than_days)

    # Group by original path
    by_original: dict[str, list[BackupInfo]] = {}
    for b in backups:
        if b.original not in by_original:
            by_original[b.original] = []
        by_original[b.original].append(b)

    for orig, orig_backups in by_original.items():
        # Sort by timestamp, newest first
        orig_backups.sort(key=lambda b: b.timestamp, reverse=True)

        for i, backup in enumerate(orig_backups):
            should_delete = False

            # Check keep count
            if keep is not None and i >= keep:
                should_delete = True

            # Check age
            if cutoff_date is not None and backup.timestamp < cutoff_date:
                should_delete = True

            if should_delete:
                if delete_backup(backup.backup):
                    deleted.append(backup.backup)

    return deleted


def format_backup_list_text(backups: list[BackupInfo]) -> str:
    """Format backup list for text display.

    Args:
        backups: List of BackupInfo objects

    Returns:
        Formatted text string
    """
    if not backups:
        return "No backups found."

    lines = ["", "Backups:", "-" * 50]

    # Group by original path
    by_original: dict[str, list[BackupInfo]] = {}
    for b in backups:
        if b.original not in by_original:
            by_original[b.original] = []
        by_original[b.original].append(b)

    for orig, orig_backups in sorted(by_original.items()):
        lines.append(f"\n{orig}:")
        for b in orig_backups:
            size_str = _format_size(b.size)
            ts_str = b.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            dir_marker = " (dir)" if b.is_directory else ""
            lines.append(f"  - {ts_str} ({size_str}){dir_marker}")
            lines.append(f"    {b.backup}")

    total_size = sum(b.size for b in backups)
    lines.append("")
    lines.append(f"Total: {len(backups)} backup(s), {_format_size(total_size)}")
    lines.append("")

    return "\n".join(lines)


def format_backup_list_json(backups: list[BackupInfo]) -> dict[str, Any]:
    """Format backup list for JSON output.

    Args:
        backups: List of BackupInfo objects

    Returns:
        Dictionary for JSON serialization
    """
    return {
        "backups": [b.to_dict() for b in backups],
        "total_count": len(backups),
        "total_size": sum(b.size for b in backups),
    }


def _format_size(size: int) -> str:
    """Format a size in bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def determine_operation(module_name: str, module_args: dict[str, Any]) -> str:
    """Determine the operation type from module and arguments.

    Args:
        module_name: Name of the module
        module_args: Module arguments

    Returns:
        Operation type: "delete", "modify", or "create"
    """
    # File module
    if module_name == "file":
        state = module_args.get("state", "file")
        if state == "absent":
            return "delete"
        return "modify"

    # Copy module - always overwrites
    if module_name == "copy":
        return "modify"

    # Template module - always overwrites
    if module_name == "template":
        return "modify"

    # Default to modify
    return "modify"
