"""FTL file operation modules.

These modules handle file system operations like creating, copying,
and managing files and directories. They run in-process for maximum
performance.

Supports event streaming for progress reporting on long-running operations.
"""

import grp
import os
import pwd
import shutil
from pathlib import Path
from typing import Any

from jinja2 import Template

from ftl2.ftl_modules.exceptions import FTLModuleError
from ftl2.events import emit_progress, emit_log

__all__ = ["ftl_file", "ftl_copy", "ftl_template"]


def ftl_file(
    path: str,
    state: str = "file",
    mode: str | None = None,
    owner: str | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    """Manage file properties.

    Args:
        path: Path to the file or directory
        state: Desired state - file, directory, absent, touch
        mode: File mode (e.g., "0644", "755")
        owner: File owner username
        group: File group name

    Returns:
        Result dict with changed status and path

    Raises:
        FTLModuleError: If operation fails
    """
    p = Path(path)
    changed = False

    try:
        if state == "absent":
            if p.exists():
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                changed = True

        elif state == "directory":
            if not p.exists():
                p.mkdir(parents=True)
                changed = True
            elif not p.is_dir():
                raise FTLModuleError(
                    f"Path exists but is not a directory: {path}",
                    path=path,
                )

        elif state == "touch":
            if not p.exists():
                p.touch()
                changed = True
            else:
                # Update mtime
                p.touch()
                changed = True

        elif state == "file":
            if not p.exists():
                raise FTLModuleError(
                    f"File does not exist: {path}",
                    path=path,
                )

        else:
            raise FTLModuleError(
                f"Invalid state: {state}",
                path=path,
                state=state,
            )

        # Handle mode changes
        if mode and p.exists():
            # Normalize mode string
            mode_str = mode.lstrip("0") if mode.startswith("0") else mode
            mode_int = int(mode_str, 8)
            current_mode = p.stat().st_mode & 0o7777

            if current_mode != mode_int:
                p.chmod(mode_int)
                changed = True

        # Handle owner changes
        if owner and p.exists():
            try:
                uid = pwd.getpwnam(owner).pw_uid
                current_uid = p.stat().st_uid
                if current_uid != uid:
                    os.chown(p, uid, -1)
                    changed = True
            except KeyError:
                raise FTLModuleError(
                    f"Unknown user: {owner}",
                    path=path,
                    owner=owner,
                )

        # Handle group changes
        if group and p.exists():
            try:
                gid = grp.getgrnam(group).gr_gid
                current_gid = p.stat().st_gid
                if current_gid != gid:
                    os.chown(p, -1, gid)
                    changed = True
            except KeyError:
                raise FTLModuleError(
                    f"Unknown group: {group}",
                    path=path,
                    group=group,
                )

        return {
            "changed": changed,
            "path": str(p.absolute()),
            "state": state,
        }

    except FTLModuleError:
        raise
    except PermissionError as e:
        raise FTLModuleError(
            f"Permission denied: {e}",
            path=path,
        )
    except OSError as e:
        raise FTLModuleError(
            f"OS error: {e}",
            path=path,
        )


def ftl_copy(
    src: str,
    dest: str,
    mode: str | None = None,
    force: bool = True,
    backup: bool = False,
    emit_events: bool = True,
) -> dict[str, Any]:
    """Copy a file with progress events.

    Uses chunked copy with progress reporting for large files.
    Preserves file metadata (timestamps, etc).

    Args:
        src: Source file path (absolute or relative to CWD)
        dest: Destination file path
        mode: Optional file mode for destination (e.g., "0644")
        force: Overwrite if destination exists (default True)
        backup: Create backup of destination if it exists
        emit_events: Whether to emit progress events (default True)

    Returns:
        Result dict with changed status, src, dest

    Raises:
        FTLModuleError: If copy fails

    Events:
        progress: Emitted during copy with percent, current, total bytes
    """
    # Resolve relative paths from current working directory
    # This matches Ansible's behavior where src is relative to playbook dir
    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = Path.cwd() / src_path
    dest_path = Path(dest)
    backup_path = None

    try:
        # Validate source
        if not src_path.exists():
            raise FTLModuleError(
                f"Source file not found: {src}",
                src=src,
                dest=dest,
            )

        if not src_path.is_file():
            raise FTLModuleError(
                f"Source is not a file: {src}",
                src=src,
                dest=dest,
            )

        # Check if dest is a directory
        if dest_path.is_dir():
            dest_path = dest_path / src_path.name

        # Check if we need to copy
        changed = True
        if dest_path.exists():
            if not force:
                return {
                    "changed": False,
                    "src": str(src_path),
                    "dest": str(dest_path),
                    "msg": "Destination exists and force=False",
                }

            # Check if content is identical
            if dest_path.read_bytes() == src_path.read_bytes():
                changed = False

            # Create backup if requested
            if backup and changed:
                backup_path = dest_path.with_suffix(dest_path.suffix + ".bak")
                shutil.copy2(dest_path, backup_path)

        # Copy file with progress
        if changed:
            total_size = src_path.stat().st_size
            copied = 0
            chunk_size = 65536  # 64KB chunks

            if emit_events:
                emit_progress(
                    percent=0,
                    message=f"Copying {src_path.name}",
                    current=0,
                    total=total_size,
                )

            with open(src_path, "rb") as f_in, open(dest_path, "wb") as f_out:
                while chunk := f_in.read(chunk_size):
                    f_out.write(chunk)
                    copied += len(chunk)

                    if emit_events and total_size > 0:
                        percent = int(copied * 100 / total_size)
                        emit_progress(
                            percent=percent,
                            message=f"Copying {src_path.name}",
                            current=copied,
                            total=total_size,
                        )

            # Preserve metadata (like shutil.copy2)
            shutil.copystat(src_path, dest_path)

        # Apply mode if specified
        if mode:
            mode_str = mode.lstrip("0") if mode.startswith("0") else mode
            mode_int = int(mode_str, 8)
            current_mode = dest_path.stat().st_mode & 0o7777
            if current_mode != mode_int:
                dest_path.chmod(mode_int)
                changed = True

        result: dict[str, Any] = {
            "changed": changed,
            "src": str(src_path),
            "dest": str(dest_path),
        }

        if backup_path:
            result["backup"] = str(backup_path)

        return result

    except FTLModuleError:
        raise
    except PermissionError as e:
        raise FTLModuleError(
            f"Permission denied: {e}",
            src=src,
            dest=dest,
        )
    except OSError as e:
        raise FTLModuleError(
            f"Copy failed: {e}",
            src=src,
            dest=dest,
        )


def ftl_template(
    src: str,
    dest: str,
    variables: dict[str, Any] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Render a Jinja2 template.

    Args:
        src: Source template file path (absolute or relative to CWD)
        dest: Destination file path
        variables: Template variables (dict)
        mode: Optional file mode for destination

    Returns:
        Result dict with changed status, src, dest

    Raises:
        FTLModuleError: If rendering or writing fails
    """
    # Resolve relative paths from current working directory
    src_path = Path(src)
    if not src_path.is_absolute():
        src_path = Path.cwd() / src_path
    dest_path = Path(dest)
    variables = variables or {}

    try:
        # Validate source
        if not src_path.exists():
            raise FTLModuleError(
                f"Template not found: {src}",
                src=src,
                dest=dest,
            )

        # Read and render template
        template_content = src_path.read_text()
        template = Template(template_content)
        rendered = template.render(**variables)

        # Check if content changed
        changed = True
        if dest_path.exists():
            if dest_path.read_text() == rendered:
                changed = False

        # Write output
        if changed:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(rendered)

        # Apply mode if specified
        if mode:
            mode_str = mode.lstrip("0") if mode.startswith("0") else mode
            mode_int = int(mode_str, 8)
            current_mode = dest_path.stat().st_mode & 0o7777
            if current_mode != mode_int:
                dest_path.chmod(mode_int)
                changed = True

        return {
            "changed": changed,
            "src": str(src_path),
            "dest": str(dest_path),
        }

    except FTLModuleError:
        raise
    except Exception as e:
        raise FTLModuleError(
            f"Template rendering failed: {e}",
            src=src,
            dest=dest,
        )
