# Automatic Backups for FTL2 Modules

This document describes the design for automatic backup support in FTL2, allowing modules to safely create backups before destructive operations.

## Overview

Automatic backups provide a safety net for destructive operations by creating timestamped copies of files before they are modified or deleted. This feature is particularly valuable for AI-assisted development where operations may be executed without human review.

## Goals

1. **Safety**: Prevent data loss from accidental deletions or overwrites
2. **Transparency**: Clear indication when backups are created
3. **Reversibility**: Easy restoration from backups
4. **Opt-out**: Allow skipping backups when not needed (`--no-backup`)
5. **Module independence**: Modules declare capability; gate handles execution

## Module Metadata Extension

### Docstring Declaration

Modules declare backup support in their docstring using a new metadata field:

```python
"""
File module - Manage files and directories.

Creates, modifies, and removes files and directories on target hosts.

Arguments:
    path (str, required): Target file or directory path
    state (str, optional): Desired state (file, directory, absent, touch)
    mode (str, optional): File permissions (e.g., "0644")

Returns:
    path (str): The path that was managed
    changed (bool): Whether the file was modified

Idempotent: Yes
Backup-Capable: Yes
Backup-Paths: path
"""
```

### Metadata Fields

| Field | Type | Description |
|-------|------|-------------|
| `Backup-Capable` | `Yes/No` | Whether the module supports automatic backups |
| `Backup-Paths` | `str` | Comma-separated list of argument names that contain paths to back up |
| `Backup-Trigger` | `str` | Conditions that trigger backup (default: `modify,delete`) |

### Example Declarations

**File module** (backs up on delete):
```
Backup-Capable: Yes
Backup-Paths: path
Backup-Trigger: delete
```

**Copy module** (backs up on overwrite):
```
Backup-Capable: Yes
Backup-Paths: dest
Backup-Trigger: modify
```

**Template module** (backs up on overwrite):
```
Backup-Capable: Yes
Backup-Paths: dest
Backup-Trigger: modify
```

**Shell module** (no backup - unpredictable effects):
```
Backup-Capable: No
```

## Protocol Extensions

### Pre-execution Backup Request

Before executing a backup-capable module, the gate requests backup information from the module. This is a two-phase execution:

**Phase 1: Backup Discovery**

The module is invoked with a special `_ftl2_discover_backups` flag:

```json
{
  "module": "file",
  "args": {
    "path": "/etc/app.conf",
    "state": "absent"
  },
  "_ftl2_discover_backups": true
}
```

The module returns paths that need backup:

```json
{
  "backup_paths": [
    {
      "path": "/etc/app.conf",
      "exists": true,
      "size": 1234,
      "operation": "delete"
    }
  ],
  "backup_recommended": true
}
```

**Phase 2: Execution with Backup Confirmation**

After backups are created, the module is invoked with backup metadata:

```json
{
  "module": "file",
  "args": {
    "path": "/etc/app.conf",
    "state": "absent"
  },
  "_ftl2_backups_created": [
    {
      "original": "/etc/app.conf",
      "backup": "/etc/app.conf.ftl2-backup-20260205-113500"
    }
  ]
}
```

### Module Response Extension

Module results include backup information:

```json
{
  "success": true,
  "changed": true,
  "output": {
    "path": "/etc/app.conf",
    "state": "absent"
  },
  "backups": [
    {
      "original": "/etc/app.conf",
      "backup": "/etc/app.conf.ftl2-backup-20260205-113500",
      "size": 1234,
      "timestamp": "2026-02-05T11:35:00Z"
    }
  ]
}
```

## Backup Naming Convention

Backups follow a predictable naming pattern:

```
{original_path}.ftl2-backup-{YYYYMMDD}-{HHMMSS}
```

Examples:
- `/etc/app.conf` → `/etc/app.conf.ftl2-backup-20260205-113500`
- `/var/log/app.log` → `/var/log/app.log.ftl2-backup-20260205-113501`

For directories:
```
{original_path}.ftl2-backup-{YYYYMMDD}-{HHMMSS}/
```

## CLI Integration

### New Options

```bash
# Default behavior: backups enabled for destructive operations
ftl2 run -m file -i hosts.yml -a "path=/etc/app.conf state=absent"
# Output: Backing up /etc/app.conf to /etc/app.conf.ftl2-backup-20260205-113500

# Disable backups
ftl2 run -m file -i hosts.yml -a "path=/tmp/test state=absent" --no-backup

# Custom backup directory
ftl2 run -m file -i hosts.yml -a "path=/etc/app.conf state=absent" \
    --backup-dir /var/ftl2/backups

# List backups for a path
ftl2 backup list /etc/app.conf

# Restore from backup
ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500
```

### Output Format

**Text output:**
```
Backing up files before execution:
  /etc/app.conf → /etc/app.conf.ftl2-backup-20260205-113500 (1.2KB)

Execution Results:
  ...

Backups created: 1 file(s), 1.2KB total
Use 'ftl2 backup restore <path>' to restore
```

**JSON output:**
```json
{
  "backups": [
    {
      "original": "/etc/app.conf",
      "backup": "/etc/app.conf.ftl2-backup-20260205-113500",
      "size": 1234,
      "timestamp": "2026-02-05T11:35:00Z"
    }
  ],
  "results": { ... }
}
```

## Gate Implementation

The gate (ftl_gate) handles backup creation to ensure consistency:

```python
class BackupManager:
    """Manages file backups before destructive operations."""

    def discover_backup_paths(
        self,
        module_name: str,
        args: dict,
        module_metadata: dict,
    ) -> list[BackupPath]:
        """Determine which paths need backup based on module metadata."""

    def create_backup(self, path: str) -> BackupResult:
        """Create a timestamped backup of a file or directory."""

    def list_backups(self, original_path: str) -> list[BackupInfo]:
        """List all backups for a given path."""

    def restore_backup(self, backup_path: str) -> RestoreResult:
        """Restore a file from backup."""
```

### Backup Creation Flow

```
1. Load module metadata (parse Backup-Capable, Backup-Paths, Backup-Trigger)
2. If module is backup-capable and operation matches trigger:
   a. Invoke module with _ftl2_discover_backups=true
   b. Get list of paths that need backup
   c. For each path that exists:
      - Create backup with timestamp
      - Record backup metadata
   d. Invoke module with _ftl2_backups_created metadata
3. Return results including backup information
```

## Module Implementation Guide

### Implementing Backup Discovery

Modules implement a `discover_backups` function:

```python
def discover_backups(args: dict) -> list[dict]:
    """Return paths that would be modified by this operation.

    Called when _ftl2_discover_backups is set.

    Returns:
        List of dicts with:
        - path: The file path
        - operation: "delete", "modify", or "create"
        - exists: Whether the file currently exists
    """
    path = args.get("path")
    state = args.get("state", "file")

    if state == "absent":
        return [{
            "path": path,
            "operation": "delete",
            "exists": os.path.exists(path),
        }]
    elif state in ("file", "touch"):
        return [{
            "path": path,
            "operation": "modify" if os.path.exists(path) else "create",
            "exists": os.path.exists(path),
        }]

    return []
```

### Main Function Integration

```python
def main(args: dict) -> dict:
    # Check for backup discovery mode
    if args.get("_ftl2_discover_backups"):
        return {"backup_paths": discover_backups(args)}

    # Normal execution
    # Backup metadata is available if needed
    backups_created = args.get("_ftl2_backups_created", [])

    # ... normal module logic ...

    return result
```

## Backup Storage Options

### Option 1: Adjacent Backups (Default)

Backups stored next to original files:
```
/etc/app.conf
/etc/app.conf.ftl2-backup-20260205-113500
/etc/app.conf.ftl2-backup-20260204-090000
```

**Pros:**
- Easy to find backups
- No extra configuration
- Works on any filesystem

**Cons:**
- Clutters directories
- May not work if directory is read-only

### Option 2: Central Backup Directory

Backups stored in a central location:
```
/var/ftl2/backups/
  etc/
    app.conf.ftl2-backup-20260205-113500
    app.conf.ftl2-backup-20260204-090000
  var/log/
    app.log.ftl2-backup-20260205-120000
```

**Pros:**
- Clean source directories
- Easy to manage/prune backups
- Single location for all backups

**Cons:**
- Requires configuration
- Must preserve directory structure

### Configuration

```yaml
# ftl2.yml or CLI options
backup:
  enabled: true
  location: adjacent  # or "central"
  central_dir: /var/ftl2/backups
  retention_days: 7
  max_backups_per_file: 5
```

## Backup Management Commands

```bash
# List all backups
ftl2 backup list
/etc/app.conf:
  - 2026-02-05 11:35:00 (1.2KB) /etc/app.conf.ftl2-backup-20260205-113500
  - 2026-02-04 09:00:00 (1.1KB) /etc/app.conf.ftl2-backup-20260204-090000

# List backups for specific path
ftl2 backup list /etc/app.conf

# Show backup contents
ftl2 backup show /etc/app.conf.ftl2-backup-20260205-113500

# Diff backup with current
ftl2 backup diff /etc/app.conf.ftl2-backup-20260205-113500

# Restore from backup
ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500
# Restored /etc/app.conf from backup

# Restore with confirmation
ftl2 backup restore /etc/app.conf.ftl2-backup-20260205-113500 --dry-run
# Would restore /etc/app.conf from /etc/app.conf.ftl2-backup-20260205-113500

# Prune old backups
ftl2 backup prune --older-than 7d
# Removed 15 backups (2.3MB)

# Prune keeping only N most recent
ftl2 backup prune --keep 3
```

## Error Handling

### Backup Failures

If backup creation fails, the operation should not proceed:

```
Error: Failed to create backup for /etc/app.conf
  Reason: Permission denied

  Suggested actions:
    1. Run with elevated privileges (sudo)
    2. Use --no-backup to skip backups (not recommended)
    3. Use --backup-dir to specify a writable backup location
```

### Restoration Failures

```
Error: Failed to restore from backup
  Backup: /etc/app.conf.ftl2-backup-20260205-113500
  Reason: Target path /etc/app.conf is a directory

  Suggested actions:
    1. Remove the existing directory first
    2. Use --force to overwrite
```

## Integration with Existing Features

### Dry-Run Mode

Dry-run shows what backups would be created:

```bash
ftl2 run -m file -i hosts.yml -a "path=/etc/app.conf state=absent" --dry-run

Dry Run Preview:
  localhost:
    Would back up: /etc/app.conf → /etc/app.conf.ftl2-backup-20260205-113500
    Would remove: /etc/app.conf
```

### State Tracking

Backup information is included in state files:

```json
{
  "hosts": {
    "web01": {
      "success": true,
      "backups": [
        {
          "original": "/etc/app.conf",
          "backup": "/etc/app.conf.ftl2-backup-20260205-113500"
        }
      ]
    }
  }
}
```

### Workflow Tracking

Workflow reports include backup summaries:

```
Workflow: deploy-2026-02-05
  Step 1 (cleanup): ✓ 3/3 hosts (5 backups created)
  Step 2 (deploy): ✓ 3/3 hosts (0 backups)

Total backups: 5 files, 12.3KB
```

## Security Considerations

1. **Backup permissions**: Backups preserve original file permissions
2. **Sensitive data**: Backups may contain secrets; ensure proper access controls
3. **Backup location**: Central backup directory should have restricted access
4. **Retention**: Automatic pruning to prevent disk exhaustion
5. **Audit trail**: Log all backup and restore operations

## Implementation Phases

### Phase 1: Core Infrastructure
- [ ] Add backup metadata parsing to module_docs.py
- [ ] Create BackupManager class in gate
- [ ] Add --no-backup and --backup-dir CLI options

### Phase 2: Module Updates
- [ ] Add backup metadata to file module
- [ ] Add backup metadata to copy module
- [ ] Add backup metadata to template module
- [ ] Implement discover_backups in each module

### Phase 3: Management Commands
- [ ] Implement `ftl2 backup list`
- [ ] Implement `ftl2 backup restore`
- [ ] Implement `ftl2 backup prune`

### Phase 4: Integration
- [ ] Integrate with dry-run mode
- [ ] Integrate with state tracking
- [ ] Integrate with workflow tracking
- [ ] Add JSON output support
