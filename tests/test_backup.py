"""Tests for ftl2.backup module."""

from datetime import datetime
from pathlib import Path

from ftl2.backup import (
    BackupInfo,
    BackupManager,
    BackupPath,
    BackupResult,
    _format_size,
    delete_backup,
    determine_operation,
    format_backup_list_json,
    format_backup_list_text,
    generate_backup_path,
    get_original_path,
    list_backups,
    parse_backup_timestamp,
    prune_backups,
    restore_backup,
)


class TestBackupPath:
    def test_to_dict(self):
        bp = BackupPath(path="/etc/config", operation="modify", exists=True, size=100)
        d = bp.to_dict()
        assert d["path"] == "/etc/config"
        assert d["operation"] == "modify"
        assert d["exists"] is True
        assert d["size"] == 100


class TestBackupResult:
    def test_auto_timestamp(self):
        result = BackupResult(original="/etc/config", backup="/tmp/backup")
        assert result.timestamp != ""

    def test_to_dict_without_error(self):
        result = BackupResult(original="/a", backup="/b", success=True)
        d = result.to_dict()
        assert "error" not in d
        assert d["success"] is True

    def test_to_dict_with_error(self):
        result = BackupResult(original="/a", backup="/b", success=False, error="fail")
        d = result.to_dict()
        assert d["error"] == "fail"


class TestBackupInfo:
    def test_to_dict(self):
        ts = datetime(2026, 4, 18, 12, 0, 0)
        info = BackupInfo(original="/a", backup="/b", size=42, timestamp=ts)
        d = info.to_dict()
        assert d["original"] == "/a"
        assert d["timestamp"] == "2026-04-18T12:00:00"
        assert d["is_directory"] is False


class TestGenerateBackupPath:
    def test_adjacent_backup(self):
        path = generate_backup_path("/etc/hosts")
        assert path.startswith("/etc/hosts.ftl2-backup-")

    def test_central_backup_dir(self, tmp_path):
        path = generate_backup_path("/etc/hosts", backup_dir=tmp_path)
        assert str(tmp_path) in path
        assert "etc/hosts.ftl2-backup-" in path


class TestParseBackupTimestamp:
    def test_valid_timestamp(self):
        ts = parse_backup_timestamp("/etc/hosts.ftl2-backup-20260418-120000")
        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 4
        assert ts.day == 18

    def test_invalid_format(self):
        assert parse_backup_timestamp("/etc/hosts.bak") is None

    def test_no_match(self):
        assert parse_backup_timestamp("/etc/hosts") is None


class TestGetOriginalPath:
    def test_strips_backup_suffix(self):
        orig = get_original_path("/etc/hosts.ftl2-backup-20260418-120000")
        assert orig == "/etc/hosts"

    def test_no_suffix(self):
        orig = get_original_path("/etc/hosts")
        assert orig == "/etc/hosts"


class TestBackupManager:
    def test_should_backup_disabled(self):
        mgr = BackupManager(enabled=False)
        assert not mgr.should_backup(True, ["modify"], "modify")

    def test_should_backup_not_capable(self):
        mgr = BackupManager(enabled=True)
        assert not mgr.should_backup(False, ["modify"], "modify")

    def test_should_backup_operation_match(self):
        mgr = BackupManager(enabled=True)
        assert mgr.should_backup(True, ["modify", "delete"], "modify")

    def test_should_backup_operation_no_match(self):
        mgr = BackupManager(enabled=True)
        assert not mgr.should_backup(True, ["delete"], "modify")

    def test_create_backup_file(self, tmp_path):
        src = tmp_path / "config.txt"
        src.write_text("hello")

        mgr = BackupManager()
        result = mgr.create_backup(str(src))
        assert result.success
        assert Path(result.backup).exists()
        assert Path(result.backup).read_text() == "hello"
        assert result.size == 5

    def test_create_backup_directory(self, tmp_path):
        src = tmp_path / "mydir"
        src.mkdir()
        (src / "a.txt").write_text("aaa")
        (src / "b.txt").write_text("bbb")

        mgr = BackupManager()
        result = mgr.create_backup(str(src))
        assert result.success
        backup = Path(result.backup)
        assert (backup / "a.txt").read_text() == "aaa"
        assert (backup / "b.txt").read_text() == "bbb"

    def test_create_backup_nonexistent(self, tmp_path):
        mgr = BackupManager()
        result = mgr.create_backup(str(tmp_path / "nope"))
        assert not result.success
        assert "does not exist" in result.error

    def test_create_backup_central_dir(self, tmp_path):
        src = tmp_path / "data" / "config.txt"
        src.parent.mkdir(parents=True)
        src.write_text("content")

        backup_dir = tmp_path / "backups"
        mgr = BackupManager(backup_dir=backup_dir)
        result = mgr.create_backup(str(src))
        assert result.success
        assert str(backup_dir) in result.backup

    def test_get_created_backups(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("data")

        mgr = BackupManager()
        mgr.create_backup(str(src))
        backups = mgr.get_created_backups()
        assert len(backups) == 1
        assert backups[0].original == str(src)

    def test_clear_created_backups(self, tmp_path):
        src = tmp_path / "file.txt"
        src.write_text("data")

        mgr = BackupManager()
        mgr.create_backup(str(src))
        mgr.clear_created_backups()
        assert len(mgr.get_created_backups()) == 0

    def test_discover_backup_paths(self, tmp_path):
        f = tmp_path / "target.txt"
        f.write_text("hello world")

        mgr = BackupManager()
        paths = mgr.discover_backup_paths(
            module_args={"dest": str(f), "src": "/nonexistent"},
            backup_path_args=["dest", "src"],
            operation="modify",
        )
        assert len(paths) == 2
        assert paths[0].exists is True
        assert paths[0].size == 11
        assert paths[1].exists is False

    def test_create_backups_skips_nonexistent(self, tmp_path):
        mgr = BackupManager()
        paths = [
            BackupPath(path=str(tmp_path / "nope"), operation="modify", exists=False),
        ]
        results = mgr.create_backups(paths)
        assert len(results) == 0


class TestRestoreBackup:
    def test_restore_file(self, tmp_path):
        original = tmp_path / "config.txt"
        original.write_text("original")

        mgr = BackupManager()
        backup_result = mgr.create_backup(str(original))

        # Modify original
        original.write_text("modified")

        # Restore
        result = restore_backup(backup_result.backup, force=True)
        assert result.success
        assert original.read_text() == "original"

    def test_restore_nonexistent_backup(self):
        result = restore_backup("/nonexistent.ftl2-backup-20260418-120000")
        assert not result.success
        assert "does not exist" in result.error

    def test_restore_target_exists_no_force(self, tmp_path):
        original = tmp_path / "config.txt"
        original.write_text("original")

        mgr = BackupManager()
        backup_result = mgr.create_backup(str(original))

        result = restore_backup(backup_result.backup, force=False)
        assert not result.success
        assert "--force" in result.error


class TestDeleteBackup:
    def test_delete_file(self, tmp_path):
        f = tmp_path / "backup.ftl2-backup-20260418-120000"
        f.write_text("data")
        assert delete_backup(str(f))
        assert not f.exists()

    def test_delete_directory(self, tmp_path):
        d = tmp_path / "backup.ftl2-backup-20260418-120000"
        d.mkdir()
        (d / "file.txt").write_text("data")
        assert delete_backup(str(d))
        assert not d.exists()

    def test_delete_nonexistent(self):
        assert not delete_backup("/nonexistent")


class TestListBackups:
    def test_list_adjacent_backups(self, tmp_path):
        original = tmp_path / "config.txt"
        original.write_text("data")
        # Create fake backup files
        (tmp_path / "config.txt.ftl2-backup-20260418-120000").write_text("v1")
        (tmp_path / "config.txt.ftl2-backup-20260418-130000").write_text("v2")

        backups = list_backups(str(original))
        assert len(backups) == 2
        # Newest first
        assert backups[0].timestamp > backups[1].timestamp

    def test_list_central_backups(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "etc-hosts.ftl2-backup-20260418-120000").write_text("data")

        backups = list_backups(backup_dir=backup_dir)
        assert len(backups) == 1

    def test_list_no_backups(self, tmp_path):
        backups = list_backups(str(tmp_path / "nope.txt"))
        assert len(backups) == 0


class TestPruneBackups:
    def test_prune_keep_n(self, tmp_path):
        original = tmp_path / "config.txt"
        original.write_text("data")
        (tmp_path / "config.txt.ftl2-backup-20260418-100000").write_text("v1")
        (tmp_path / "config.txt.ftl2-backup-20260418-110000").write_text("v2")
        (tmp_path / "config.txt.ftl2-backup-20260418-120000").write_text("v3")

        deleted = prune_backups(str(original), keep=1)
        assert len(deleted) == 2
        remaining = list_backups(str(original))
        assert len(remaining) == 1


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500B"

    def test_kilobytes(self):
        assert "KB" in _format_size(2048)

    def test_megabytes(self):
        assert "MB" in _format_size(2 * 1024 * 1024)


class TestDetermineOperation:
    def test_file_absent(self):
        assert determine_operation("file", {"state": "absent"}) == "delete"

    def test_file_default(self):
        assert determine_operation("file", {}) == "modify"

    def test_copy(self):
        assert determine_operation("copy", {}) == "modify"

    def test_template(self):
        assert determine_operation("template", {}) == "modify"

    def test_unknown_module(self):
        assert determine_operation("service", {}) == "modify"


class TestFormatBackupList:
    def test_text_no_backups(self):
        assert format_backup_list_text([]) == "No backups found."

    def test_text_with_backups(self):
        backups = [
            BackupInfo(
                original="/etc/config",
                backup="/tmp/config.ftl2-backup-20260418-120000",
                size=1024,
                timestamp=datetime(2026, 4, 18, 12, 0, 0),
            ),
        ]
        text = format_backup_list_text(backups)
        assert "/etc/config" in text
        assert "1 backup(s)" in text

    def test_json_format(self):
        backups = [
            BackupInfo(
                original="/etc/config",
                backup="/tmp/backup",
                size=100,
                timestamp=datetime(2026, 4, 18),
            ),
        ]
        data = format_backup_list_json(backups)
        assert data["total_count"] == 1
        assert data["total_size"] == 100
