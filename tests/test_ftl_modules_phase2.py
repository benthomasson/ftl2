"""Tests for FTL modules Phase 2 - Core module implementations."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from ftl2.ftl_modules import FTLModuleError
from ftl2.ftl_modules.file import ftl_file, ftl_copy, ftl_template
from ftl2.ftl_modules.http import ftl_uri, ftl_get_url
from ftl2.ftl_modules.command import ftl_command, ftl_shell
from ftl2.ftl_modules.pip import ftl_pip


class TestFtlFile:
    """Tests for ftl_file module."""

    def test_file_state_existing(self):
        """Test file state with existing file."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        try:
            result = ftl_file(path=path, state="file")
            assert result["changed"] is False
            assert result["state"] == "file"
        finally:
            Path(path).unlink()

    def test_file_state_nonexistent_raises(self):
        """Test file state with nonexistent file raises error."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_file(path="/nonexistent/path/file.txt", state="file")
        assert "does not exist" in str(exc_info.value)

    def test_directory_create(self):
        """Test creating a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_dir = Path(tmpdir) / "new_directory"

            result = ftl_file(path=str(new_dir), state="directory")

            assert result["changed"] is True
            assert new_dir.is_dir()

    def test_directory_exists(self):
        """Test directory state with existing directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ftl_file(path=tmpdir, state="directory")
            assert result["changed"] is False

    def test_touch_create(self):
        """Test touching a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            new_file = Path(tmpdir) / "touched.txt"

            result = ftl_file(path=str(new_file), state="touch")

            assert result["changed"] is True
            assert new_file.exists()

    def test_touch_existing(self):
        """Test touching an existing file."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        try:
            result = ftl_file(path=path, state="touch")
            assert result["changed"] is True  # mtime updated
        finally:
            Path(path).unlink()

    def test_absent_file(self):
        """Test removing a file."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)

        result = ftl_file(path=str(path), state="absent")

        assert result["changed"] is True
        assert not path.exists()

    def test_absent_directory(self):
        """Test removing a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_to_remove = Path(tmpdir) / "to_remove"
            dir_to_remove.mkdir()
            (dir_to_remove / "file.txt").write_text("content")

            result = ftl_file(path=str(dir_to_remove), state="absent")

            assert result["changed"] is True
            assert not dir_to_remove.exists()

    def test_absent_nonexistent(self):
        """Test absent on nonexistent path."""
        result = ftl_file(path="/nonexistent/path", state="absent")
        assert result["changed"] is False

    def test_mode_change(self):
        """Test changing file mode."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)

        try:
            # Set initial mode
            path.chmod(0o644)

            result = ftl_file(path=str(path), state="file", mode="755")

            assert result["changed"] is True
            assert (path.stat().st_mode & 0o7777) == 0o755
        finally:
            path.unlink()

    def test_mode_no_change(self):
        """Test mode when already correct."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = Path(f.name)

        try:
            path.chmod(0o644)

            result = ftl_file(path=str(path), state="file", mode="0644")

            assert result["changed"] is False
        finally:
            path.unlink()

    def test_invalid_state(self):
        """Test invalid state raises error."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_file(path="/tmp/test", state="invalid")
        assert "Invalid state" in str(exc_info.value)


class TestFtlCopy:
    """Tests for ftl_copy module."""

    def test_copy_file(self):
        """Test copying a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("hello world")

            result = ftl_copy(src=str(src), dest=str(dest))

            assert result["changed"] is True
            assert dest.read_text() == "hello world"

    def test_copy_to_directory(self):
        """Test copying to a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest_dir = Path(tmpdir) / "dest_dir"
            dest_dir.mkdir()
            src.write_text("content")

            result = ftl_copy(src=str(src), dest=str(dest_dir))

            assert result["changed"] is True
            assert (dest_dir / "source.txt").read_text() == "content"

    def test_copy_identical_no_change(self):
        """Test copying identical file reports no change."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            content = "identical content"
            src.write_text(content)
            dest.write_text(content)

            result = ftl_copy(src=str(src), dest=str(dest))

            assert result["changed"] is False

    def test_copy_with_mode(self):
        """Test copying with mode change."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("content")

            result = ftl_copy(src=str(src), dest=str(dest), mode="755")

            assert result["changed"] is True
            assert (dest.stat().st_mode & 0o7777) == 0o755

    def test_copy_force_false(self):
        """Test copy with force=False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("new content")
            dest.write_text("existing content")

            result = ftl_copy(src=str(src), dest=str(dest), force=False)

            assert result["changed"] is False
            assert dest.read_text() == "existing content"

    def test_copy_with_backup(self):
        """Test copy with backup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("new content")
            dest.write_text("old content")

            result = ftl_copy(src=str(src), dest=str(dest), backup=True)

            assert result["changed"] is True
            assert "backup" in result
            backup_path = Path(result["backup"])
            assert backup_path.read_text() == "old content"

    def test_copy_missing_source(self):
        """Test copy with missing source raises error."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_copy(src="/nonexistent/source.txt", dest="/tmp/dest.txt")
        assert "not found" in str(exc_info.value)

    def test_copy_relative_path_resolves_from_cwd(self):
        """Test that relative src paths are resolved from CWD (Issue 17)."""
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create source file in tmpdir
            src_file = Path(tmpdir) / "source.txt"
            src_file.write_text("relative path test")
            dest_file = Path(tmpdir) / "dest.txt"

            # Change to tmpdir and use relative path
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # Use just the filename (relative path)
                result = ftl_copy(src="source.txt", dest=str(dest_file))

                assert result["changed"] is True
                assert dest_file.read_text() == "relative path test"
            finally:
                os.chdir(original_cwd)


class TestFtlTemplate:
    """Tests for ftl_template module."""

    def test_render_template(self):
        """Test rendering a simple template."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "template.j2"
            dest = Path(tmpdir) / "output.txt"
            src.write_text("Hello, {{ name }}!")

            result = ftl_template(
                src=str(src),
                dest=str(dest),
                variables={"name": "World"},
            )

            assert result["changed"] is True
            assert dest.read_text() == "Hello, World!"

    def test_template_no_change(self):
        """Test template with identical output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "template.j2"
            dest = Path(tmpdir) / "output.txt"
            src.write_text("Hello, {{ name }}!")
            dest.write_text("Hello, World!")

            result = ftl_template(
                src=str(src),
                dest=str(dest),
                variables={"name": "World"},
            )

            assert result["changed"] is False

    def test_template_creates_parent_dirs(self):
        """Test template creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "template.j2"
            dest = Path(tmpdir) / "subdir" / "output.txt"
            src.write_text("content")

            result = ftl_template(src=str(src), dest=str(dest))

            assert result["changed"] is True
            assert dest.exists()

    def test_template_missing_source(self):
        """Test template with missing source raises error."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_template(src="/nonexistent/template.j2", dest="/tmp/out.txt")
        assert "not found" in str(exc_info.value)

    def test_template_relative_path_resolves_from_cwd(self):
        """Test that relative src paths are resolved from CWD (Issue 17)."""
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create template in tmpdir
            src_file = Path(tmpdir) / "template.j2"
            src_file.write_text("Hello, {{ name }}!")
            dest_file = Path(tmpdir) / "output.txt"

            # Change to tmpdir and use relative path
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # Use just the filename (relative path)
                result = ftl_template(
                    src="template.j2",
                    dest=str(dest_file),
                    variables={"name": "World"},
                )

                assert result["changed"] is True
                assert dest_file.read_text() == "Hello, World!"
            finally:
                os.chdir(original_cwd)


class TestFtlCommand:
    """Tests for ftl_command module."""

    def test_simple_command(self):
        """Test running a simple command."""
        result = ftl_command(cmd="echo hello")

        assert result["changed"] is True
        assert result["rc"] == 0
        assert "hello" in result["stdout"]

    def test_command_with_chdir(self):
        """Test running command in directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = ftl_command(cmd="pwd", chdir=tmpdir)

            assert result["changed"] is True
            assert tmpdir in result["stdout"]

    def test_command_creates_skip(self):
        """Test creates parameter skips when file exists."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        try:
            result = ftl_command(cmd="echo should not run", creates=path)

            assert result["changed"] is False
            assert "Skipped" in result.get("msg", "")
        finally:
            Path(path).unlink()

    def test_command_creates_runs(self):
        """Test creates parameter runs when file doesn't exist."""
        result = ftl_command(cmd="echo should run", creates="/nonexistent/file")

        assert result["changed"] is True
        assert "should run" in result["stdout"]

    def test_command_removes_skip(self):
        """Test removes parameter skips when file doesn't exist."""
        result = ftl_command(cmd="echo should not run", removes="/nonexistent/file")

        assert result["changed"] is False
        assert "Skipped" in result.get("msg", "")

    def test_command_removes_runs(self):
        """Test removes parameter runs when file exists."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name

        try:
            result = ftl_command(cmd="echo should run", removes=path)

            assert result["changed"] is True
            assert "should run" in result["stdout"]
        finally:
            Path(path).unlink()

    def test_command_check_failure(self):
        """Test check=True raises on non-zero exit."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_command(cmd="exit 1", check=True)

        assert exc_info.value.result["rc"] == 1

    def test_command_failure_no_check(self):
        """Test non-zero exit without check returns normally."""
        result = ftl_command(cmd="exit 42")

        assert result["changed"] is True
        assert result["rc"] == 42


class TestFtlShell:
    """Tests for ftl_shell module."""

    def test_shell_is_alias(self):
        """Test shell is alias for command."""
        result = ftl_shell(cmd="echo test")

        assert result["changed"] is True
        assert "test" in result["stdout"]

    def test_shell_with_pipe(self):
        """Test shell supports pipes."""
        result = ftl_shell(cmd="echo hello | tr 'h' 'H'")

        assert result["changed"] is True
        assert "Hello" in result["stdout"]


class TestFtlUri:
    """Tests for ftl_uri module."""

    @pytest.mark.asyncio
    async def test_get_request(self):
        """Test simple GET request."""
        with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.url = "https://example.com/"
            mock_response.text = "response body"
            mock_response.headers = {"content-type": "text/html"}

            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            result = await ftl_uri(url="https://example.com/")

            assert result["changed"] is False  # GET doesn't change
            assert result["status"] == 200
            assert result["content"] == "response body"

    @pytest.mark.asyncio
    async def test_post_request(self):
        """Test POST request marks changed."""
        with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_response.url = "https://api.example.com/items"
            mock_response.text = '{"id": 1}'
            mock_response.headers = {"content-type": "application/json"}
            mock_response.json.return_value = {"id": 1}

            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            result = await ftl_uri(
                url="https://api.example.com/items",
                method="POST",
                body='{"name": "test"}',
            )

            assert result["changed"] is True  # POST changes
            assert result["status"] == 201
            assert result["json"] == {"id": 1}

    @pytest.mark.asyncio
    async def test_status_code_check(self):
        """Test status code validation."""
        with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.url = "https://example.com/missing"
            mock_response.text = "Not Found"
            mock_response.headers = {}

            mock_client_instance = AsyncMock()
            mock_client_instance.request.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_client_instance

            with pytest.raises(FTLModuleError) as exc_info:
                await ftl_uri(url="https://example.com/missing", status_code=200)

            assert "404" in str(exc_info.value)


class TestFtlGetUrl:
    """Tests for ftl_get_url module."""

    @staticmethod
    def _mock_streaming_client(content: bytes):
        """Create a mock httpx.AsyncClient that supports streaming.

        Sets up the mock to work with:
            async with client.stream("GET", url) as response:
                async for chunk in response.aiter_bytes():
                    ...
        """
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-length": str(len(content))}

        async def aiter_bytes(chunk_size=65536):
            yield content

        mock_response.aiter_bytes = aiter_bytes

        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=False)

        mock_client_instance = MagicMock()
        mock_client_instance.stream.return_value = mock_stream_cm

        mock_client_cm = MagicMock()
        mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_cm.__aexit__ = AsyncMock(return_value=False)

        return mock_client_cm

    @pytest.mark.asyncio
    async def test_download_file(self):
        """Test downloading a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "downloaded.txt"

            with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
                mock_client.return_value = self._mock_streaming_client(b"file content")

                result = await ftl_get_url(
                    url="https://example.com/file.txt",
                    dest=str(dest),
                    emit_events=False,
                )

                assert result["changed"] is True
                assert dest.read_text() == "file content"
                assert "checksum" in result

    @pytest.mark.asyncio
    async def test_download_with_checksum(self):
        """Test download with checksum verification."""
        import hashlib

        content = b"test content"
        expected_checksum = hashlib.sha256(content).hexdigest()

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "verified.txt"

            with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
                mock_client.return_value = self._mock_streaming_client(content)

                result = await ftl_get_url(
                    url="https://example.com/file.txt",
                    dest=str(dest),
                    checksum=expected_checksum,
                    emit_events=False,
                )

                assert result["changed"] is True
                assert dest.exists()

    @pytest.mark.asyncio
    async def test_download_checksum_mismatch(self):
        """Test download with checksum mismatch raises error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "bad.txt"

            with patch("ftl2.ftl_modules.http.httpx.AsyncClient") as mock_client:
                mock_client.return_value = self._mock_streaming_client(b"actual content")

                with pytest.raises(FTLModuleError) as exc_info:
                    await ftl_get_url(
                        url="https://example.com/file.txt",
                        dest=str(dest),
                        checksum="wrongchecksum",
                        emit_events=False,
                    )

                assert "Checksum mismatch" in str(exc_info.value)


class TestFtlPip:
    """Tests for ftl_pip module."""

    def test_pip_requires_name_or_requirements(self):
        """Test pip requires either name or requirements."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_pip()
        assert "Either 'name' or 'requirements'" in str(exc_info.value)

    def test_pip_invalid_state(self):
        """Test pip with invalid state."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_pip(name="package", state="invalid")
        assert "Invalid state" in str(exc_info.value)

    def test_pip_requirements_not_found(self):
        """Test pip with missing requirements file."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_pip(requirements="/nonexistent/requirements.txt")
        assert "not found" in str(exc_info.value)

    def test_pip_absent_with_requirements(self):
        """Test pip absent not supported with requirements."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"some-package\n")
            path = f.name

        try:
            with pytest.raises(FTLModuleError) as exc_info:
                ftl_pip(requirements=path, state="absent")
            assert "not supported" in str(exc_info.value)
        finally:
            Path(path).unlink()

    def test_pip_invalid_virtualenv(self):
        """Test pip with invalid virtualenv."""
        with pytest.raises(FTLModuleError) as exc_info:
            ftl_pip(name="package", virtualenv="/nonexistent/venv")
        assert "Virtualenv not found" in str(exc_info.value)
