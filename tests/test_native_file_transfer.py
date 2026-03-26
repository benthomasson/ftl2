"""Tests for native file transfer modules (copy, template, fetch).

These modules bypass the Ansible module execution path and use SFTP directly
for file transfers. This enables relative path resolution from CWD and
proper local-to-remote file copying.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from ftl2.automation.proxy import HostScopedProxy


class TestNativeCopy:
    """Tests for the native copy() method."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock automation context."""
        context = MagicMock()
        context.hosts = MagicMock()
        context.hosts.__getitem__ = MagicMock(return_value=[])
        return context

    def test_copy_requires_dest(self, mock_context):
        """copy() raises ValueError if dest not provided."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with pytest.raises(ValueError, match="dest is required"):
            import asyncio
            asyncio.run(proxy.copy(src="foo.txt"))

    def test_copy_requires_src_or_content(self, mock_context):
        """copy() raises ValueError if neither src nor content provided."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with pytest.raises(ValueError, match="Either 'src' or 'content' must be provided"):
            import asyncio
            asyncio.run(proxy.copy(dest="/tmp/foo.txt"))

    def test_copy_src_not_found(self, mock_context):
        """copy() raises FileNotFoundError if src doesn't exist."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with pytest.raises(FileNotFoundError, match="Source file not found"):
            import asyncio
            asyncio.run(proxy.copy(src="/nonexistent/file.txt", dest="/tmp/foo.txt"))

    @pytest.mark.asyncio
    async def test_copy_localhost_with_content(self, mock_context):
        """copy() with content writes directly to dest on localhost."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "test.txt"

            results = await proxy.copy(content="Hello World", dest=str(dest))

            assert isinstance(results, list)
            assert len(results) == 1
            result = results[0]
            assert result["changed"] is True
            assert result["dest"] == str(dest)
            assert dest.read_text() == "Hello World"

    @pytest.mark.asyncio
    async def test_copy_localhost_idempotent(self, mock_context):
        """copy() returns changed=False if content matches."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "test.txt"
            dest.write_text("Hello World")

            results = await proxy.copy(content="Hello World", dest=str(dest))

            assert results[0]["changed"] is False

    @pytest.mark.asyncio
    async def test_copy_localhost_from_file(self, mock_context):
        """copy() with src reads file and copies to dest."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            src.write_text("Source content")
            dest = Path(tmpdir) / "dest.txt"

            results = await proxy.copy(src=str(src), dest=str(dest))

            assert results[0]["changed"] is True
            assert dest.read_text() == "Source content"

    @pytest.mark.asyncio
    async def test_copy_localhost_relative_path(self, mock_context):
        """copy() resolves relative src paths from CWD."""
        import os
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create source file in tmpdir
            src = Path(tmpdir) / "source.txt"
            src.write_text("Relative source")
            dest = Path(tmpdir) / "dest.txt"

            # Change to tmpdir and use relative path
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                results = await proxy.copy(src="source.txt", dest=str(dest))

                assert results[0]["changed"] is True
                assert dest.read_text() == "Relative source"
            finally:
                os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_copy_localhost_with_mode(self, mock_context):
        """copy() sets file mode when specified."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "test.txt"

            results = await proxy.copy(content="Hello", dest=str(dest), mode="0600")

            assert results[0]["changed"] is True
            assert (dest.stat().st_mode & 0o777) == 0o600


class TestNativeTemplate:
    """Tests for the native template() method."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock automation context."""
        context = MagicMock()
        context.hosts = MagicMock()
        context.hosts.__getitem__ = MagicMock(return_value=[])
        return context

    @pytest.mark.asyncio
    async def test_template_renders_variables(self, mock_context):
        """template() renders Jinja2 variables."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "template.j2"
            src.write_text("Hello {{ name }}!")
            dest = Path(tmpdir) / "output.txt"

            results = await proxy.template(src=str(src), dest=str(dest), name="World")

            assert results[0]["changed"] is True
            assert dest.read_text() == "Hello World!"

    @pytest.mark.asyncio
    async def test_template_relative_path(self, mock_context):
        """template() resolves relative src paths from CWD."""
        import os
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "config.j2"
            src.write_text("port={{ port }}")
            dest = Path(tmpdir) / "config.txt"

            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                results = await proxy.template(src="config.j2", dest=str(dest), port=8080)

                assert results[0]["changed"] is True
                assert dest.read_text() == "port=8080"
            finally:
                os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_template_not_found(self, mock_context):
        """template() raises FileNotFoundError if template doesn't exist."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with pytest.raises(FileNotFoundError, match="Template not found"):
            await proxy.template(src="/nonexistent/template.j2", dest="/tmp/out.txt")

    @pytest.mark.asyncio
    async def test_template_idempotent(self, mock_context):
        """template() returns changed=False if output matches."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "template.j2"
            src.write_text("Hello {{ name }}!")
            dest = Path(tmpdir) / "output.txt"
            dest.write_text("Hello World!")

            results = await proxy.template(src=str(src), dest=str(dest), name="World")

            assert results[0]["changed"] is False


class TestNativeFetch:
    """Tests for the native fetch() method."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock automation context."""
        context = MagicMock()
        context.hosts = MagicMock()
        context.hosts.__getitem__ = MagicMock(return_value=[])
        return context

    @pytest.mark.asyncio
    async def test_fetch_localhost_flat(self, mock_context):
        """fetch() with flat=True copies directly to dest."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.txt"
            src.write_text("Fetch me")
            dest = Path(tmpdir) / "fetched.txt"

            result = await proxy.fetch(src=str(src), dest=str(dest), flat=True)

            assert result["changed"] is True
            assert dest.read_text() == "Fetch me"

    @pytest.mark.asyncio
    async def test_fetch_localhost_nested(self, mock_context):
        """fetch() without flat creates dest/hostname/src structure."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "data" / "file.txt"
            src.parent.mkdir(parents=True)
            src.write_text("Nested fetch")
            dest = Path(tmpdir) / "fetched"

            result = await proxy.fetch(src=str(src), dest=str(dest), flat=False)

            assert result["changed"] is True
            # Should create dest/localhost/path structure
            nested_dest = dest / "localhost" / str(src).lstrip("/")
            assert nested_dest.exists()
            assert nested_dest.read_text() == "Nested fetch"

    @pytest.mark.asyncio
    async def test_fetch_not_found(self, mock_context):
        """fetch() raises FileNotFoundError if source doesn't exist."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with pytest.raises(FileNotFoundError, match="File not found"):
            await proxy.fetch(src="/nonexistent/file.txt", dest="/tmp/out")


class TestShadowedModules:
    """Tests for shadowed module registration."""

    def test_copy_is_shadowed(self):
        """copy module is registered as shadowed."""
        from ftl2.module_loading.shadowed import is_shadowed, get_native_method

        assert is_shadowed("copy")
        assert is_shadowed("ansible.builtin.copy")
        assert get_native_method("copy") == "copy"

    def test_template_is_shadowed(self):
        """template module is registered as shadowed."""
        from ftl2.module_loading.shadowed import is_shadowed, get_native_method

        assert is_shadowed("template")
        assert is_shadowed("ansible.builtin.template")
        assert get_native_method("template") == "template"

    def test_fetch_is_shadowed(self):
        """fetch module is registered as shadowed."""
        from ftl2.module_loading.shadowed import is_shadowed, get_native_method

        assert is_shadowed("fetch")
        assert is_shadowed("ansible.builtin.fetch")
        assert get_native_method("fetch") == "fetch"


class TestSSHHelperMethods:
    """Tests for SSH helper methods added for file transfer."""

    def test_ssh_host_has_read_file_or_none(self):
        """SSHHost has read_file_or_none method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "read_file_or_none")

    def test_ssh_host_has_chmod(self):
        """SSHHost has chmod method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "chmod")

    def test_ssh_host_has_chown(self):
        """SSHHost has chown method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "chown")

    def test_ssh_host_has_stat(self):
        """SSHHost has stat method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "stat")

    def test_ssh_host_has_rename(self):
        """SSHHost has rename method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "rename")

    def test_ssh_host_has_path_exists(self):
        """SSHHost has path_exists method."""
        from ftl2.ssh import SSHHost

        assert hasattr(SSHHost, "path_exists")


class TestNativeShell:
    """Tests for the native shell() method."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock automation context."""
        context = MagicMock()
        context.hosts = MagicMock()
        context.hosts.__getitem__ = MagicMock(return_value=[])
        return context

    @pytest.mark.asyncio
    async def test_shell_basic_command(self, mock_context):
        """shell() executes basic command through shell."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="echo hello")

        assert result["changed"] is True
        assert result["stdout"].strip() == "hello"
        assert result["rc"] == 0
        assert result["cmd"] == "echo hello"

    @pytest.mark.asyncio
    async def test_shell_with_pipes(self, mock_context):
        """shell() supports pipes."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="echo 'line1\nline2\nline3' | wc -l")

        assert result["changed"] is True
        assert result["stdout"].strip() == "3"
        assert result["rc"] == 0

    @pytest.mark.asyncio
    async def test_shell_with_redirects(self, mock_context):
        """shell() supports redirects."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "output.txt"

            result = await proxy.shell(cmd=f"echo 'redirected' > {dest}")

            assert result["changed"] is True
            assert result["rc"] == 0
            assert dest.read_text().strip() == "redirected"

    @pytest.mark.asyncio
    async def test_shell_with_env_vars(self, mock_context):
        """shell() expands environment variables."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="echo $HOME")

        assert result["changed"] is True
        assert result["rc"] == 0
        # $HOME should be expanded to something non-empty
        assert len(result["stdout"].strip()) > 0

    @pytest.mark.asyncio
    async def test_shell_creates_skips_if_exists(self, mock_context):
        """shell() skips execution if creates path exists."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "marker"
            marker.touch()

            result = await proxy.shell(
                cmd="echo 'should not run'",
                creates=str(marker)
            )

            assert result["changed"] is False
            assert result["rc"] == 0
            assert "skipped" in result.get("msg", "")

    @pytest.mark.asyncio
    async def test_shell_creates_runs_if_missing(self, mock_context):
        """shell() runs if creates path doesn't exist."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(
            cmd="echo 'should run'",
            creates="/nonexistent/path/that/doesnt/exist"
        )

        assert result["changed"] is True
        assert result["stdout"].strip() == "should run"

    @pytest.mark.asyncio
    async def test_shell_removes_skips_if_missing(self, mock_context):
        """shell() skips execution if removes path doesn't exist."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(
            cmd="echo 'should not run'",
            removes="/nonexistent/path"
        )

        assert result["changed"] is False
        assert result["rc"] == 0
        assert "skipped" in result.get("msg", "")

    @pytest.mark.asyncio
    async def test_shell_removes_runs_if_exists(self, mock_context):
        """shell() runs if removes path exists."""
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "marker"
            marker.touch()

            result = await proxy.shell(
                cmd="echo 'should run'",
                removes=str(marker)
            )

            assert result["changed"] is True
            assert result["stdout"].strip() == "should run"

    @pytest.mark.asyncio
    async def test_shell_with_chdir(self, mock_context):
        """shell() changes to chdir before execution."""
        import os
        proxy = HostScopedProxy(mock_context, "localhost")

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await proxy.shell(cmd="pwd", chdir=tmpdir)

            assert result["changed"] is True
            # Use realpath to resolve symlinks (e.g., /var -> /private/var on macOS)
            assert os.path.realpath(result["stdout"].strip()) == os.path.realpath(tmpdir)

    @pytest.mark.asyncio
    async def test_shell_with_bash(self, mock_context):
        """shell() uses specified executable."""
        proxy = HostScopedProxy(mock_context, "localhost")

        # Bash-specific brace expansion
        result = await proxy.shell(
            cmd="echo {1..3}",
            executable="/bin/bash"
        )

        assert result["changed"] is True
        assert result["stdout"].strip() == "1 2 3"

    @pytest.mark.asyncio
    async def test_shell_nonzero_exit_code(self, mock_context):
        """shell() captures non-zero exit codes."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="exit 42")

        assert result["changed"] is True
        assert result["rc"] == 42

    @pytest.mark.asyncio
    async def test_shell_captures_stderr(self, mock_context):
        """shell() captures stderr output."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="echo error >&2")

        assert result["changed"] is True
        assert "error" in result["stderr"]

    @pytest.mark.asyncio
    async def test_shell_stdout_lines(self, mock_context):
        """shell() provides stdout_lines list."""
        proxy = HostScopedProxy(mock_context, "localhost")

        result = await proxy.shell(cmd="echo -e 'line1\nline2\nline3'")

        assert result["changed"] is True
        assert "stdout_lines" in result
        assert len(result["stdout_lines"]) == 3


class TestShellShadowed:
    """Tests for shell module shadowing."""

    def test_shell_is_shadowed(self):
        """shell module is registered as shadowed."""
        from ftl2.module_loading.shadowed import is_shadowed, get_native_method

        assert is_shadowed("shell")
        assert is_shadowed("ansible.builtin.shell")
        assert get_native_method("shell") == "shell"
