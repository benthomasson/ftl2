"""Edge-case tests for become_prefix — supplements test_become.py."""

import pytest
from ftl2.types import BecomeConfig


class TestBecomeEdgeCases:
    """Edge cases from reviewer notes and plan success criteria."""

    def test_sudo_prefix_backward_compat(self):
        """Deprecated sudo_prefix() still works and returns same result."""
        bc = BecomeConfig(become=True)
        assert bc.sudo_prefix("whoami") == bc.become_prefix("whoami")
        assert bc.sudo_prefix("whoami") == "sudo -n whoami"

    def test_su_single_quote_in_command(self):
        """su -c properly escapes commands containing single quotes via shlex.quote."""
        bc = BecomeConfig(become=True, become_method="su")
        result = bc.become_prefix("echo 'hello'")
        assert result == "su - root -c 'echo '\"'\"'hello'\"'\"''"

    def test_unsupported_method_error_message(self):
        """ValueError includes the bad method name and lists supported ones."""
        bc = BecomeConfig(become=True, become_method="pbrun")
        with pytest.raises(ValueError, match="pbrun") as exc_info:
            bc.become_prefix("whoami")
        assert "sudo" in str(exc_info.value)
        assert "su" in str(exc_info.value)
        assert "doas" in str(exc_info.value)

    def test_unsupported_method_not_raised_when_disabled(self):
        """Unsupported method doesn't error if become=False."""
        bc = BecomeConfig(become=False, become_method="pbrun")
        assert bc.become_prefix("whoami") == "whoami"

    def test_with_overrides_preserves_become_method(self):
        """with_overrides keeps become_method from original."""
        bc = BecomeConfig(become=True, become_method="doas")
        bc2 = bc.with_overrides(become_user="catbeez")
        assert bc2.become_method == "doas"
        assert bc2.become_prefix("id") == "doas -n -u catbeez id"

    def test_empty_command(self):
        """Empty command string doesn't crash."""
        bc = BecomeConfig(become=True)
        assert bc.become_prefix("") == "sudo -n "

    def test_doas_complex_command(self):
        """doas with a multi-part command."""
        bc = BecomeConfig(become=True, become_method="doas", become_user="app")
        assert bc.become_prefix("systemctl restart nginx") == "doas -n -u app systemctl restart nginx"

    def test_su_root_explicit(self):
        """su with default root user."""
        bc = BecomeConfig(become=True, become_method="su")
        assert bc.become_prefix("whoami") == "su - root -c whoami"
