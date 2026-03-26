"""Tests for empty YAML file handling (Issue #28).

Verifies that empty or whitespace-only YAML files do not crash with
AttributeError at any safe_load call site.
"""

import tempfile
from pathlib import Path

import pytest

from ftl2.policy import Policy
from ftl2.inventory import Inventory, load_inventory
from ftl2.module_loading.requirements import parse_requirements, ModuleRequirements


class TestPolicyEmptyYAML:
    """Policy.from_file() with empty/blank YAML inputs."""

    def test_empty_file(self, tmp_path):
        """Empty file should produce Policy with no rules."""
        f = tmp_path / "empty.yaml"
        f.write_text("")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0
        assert policy.evaluate("anything", {}).permitted is True

    def test_whitespace_only(self, tmp_path):
        """Whitespace-only file (safe_load returns None)."""
        f = tmp_path / "ws.yaml"
        f.write_text("   \n\n  \n")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0

    def test_comment_only(self, tmp_path):
        """File with only YAML comments (safe_load returns None)."""
        f = tmp_path / "comment.yaml"
        f.write_text("# This is a comment\n# Another comment\n")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0

    def test_yaml_document_separator_only(self, tmp_path):
        """File with only '---' (safe_load returns None)."""
        f = tmp_path / "sep.yaml"
        f.write_text("---\n")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0

    def test_explicit_empty_dict(self, tmp_path):
        """File containing '{}' should also work (returns empty dict)."""
        f = tmp_path / "braces.yaml"
        f.write_text("{}\n")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0

    def test_normal_policy_still_works(self, tmp_path):
        """Regression: non-empty policy file still loads correctly."""
        f = tmp_path / "policy.yaml"
        f.write_text(
            "rules:\n"
            "  - decision: deny\n"
            "    match:\n"
            "      module: shell\n"
            "    reason: No shell\n"
        )
        policy = Policy.from_file(f)
        assert len(policy.rules) == 1
        assert policy.evaluate("shell", {}).permitted is False
        assert policy.evaluate("ping", {}).permitted is True

    def test_empty_rules_key(self, tmp_path):
        """File with 'rules:' but no entries (value is None).

        safe_load returns {'rules': None}. The fix uses
        `data.get("rules") or []` to handle this.
        """
        f = tmp_path / "null_rules.yaml"
        f.write_text("rules:\n")
        policy = Policy.from_file(f)
        assert len(policy.rules) == 0


class TestInventoryEmptyYAML:
    """load_inventory() with empty/blank YAML inputs."""

    def test_empty_file(self, tmp_path):
        """Empty inventory file should produce empty Inventory."""
        f = tmp_path / "empty.yaml"
        f.write_text("")
        inv = load_inventory(f, require_hosts=False)
        assert isinstance(inv, Inventory)
        assert inv.get_all_hosts() == {}

    def test_whitespace_only(self, tmp_path):
        """Whitespace-only inventory file."""
        f = tmp_path / "ws.yml"
        f.write_text("  \n\n")
        inv = load_inventory(f, require_hosts=False)
        assert isinstance(inv, Inventory)

    def test_comment_only(self, tmp_path):
        """Comment-only inventory file."""
        f = tmp_path / "comment.yml"
        f.write_text("# no hosts yet\n")
        inv = load_inventory(f, require_hosts=False)
        assert isinstance(inv, Inventory)

    def test_yaml_document_separator_only(self, tmp_path):
        """'---' only inventory file."""
        f = tmp_path / "sep.yml"
        f.write_text("---\n")
        inv = load_inventory(f, require_hosts=False)
        assert isinstance(inv, Inventory)

    def test_explicit_empty_dict(self, tmp_path):
        """Explicit empty dict '{}' inventory file."""
        f = tmp_path / "braces.yml"
        f.write_text("{}\n")
        inv = load_inventory(f, require_hosts=False)
        assert isinstance(inv, Inventory)

    def test_normal_inventory_still_works(self, tmp_path):
        """Regression: non-empty YAML inventory still loads."""
        f = tmp_path / "hosts.yml"
        f.write_text(
            "all:\n"
            "  hosts:\n"
            "    web01:\n"
            "      ansible_host: 192.168.1.10\n"
        )
        inv = load_inventory(f)
        hosts = inv.get_all_hosts()
        assert "web01" in hosts


class TestRequirementsEmptyYAML:
    """parse_requirements() already handles None — verify it stays safe."""

    def test_empty_string(self):
        """Empty string input."""
        result = parse_requirements("")
        assert isinstance(result, ModuleRequirements)
        assert result.requirements == []

    def test_whitespace_only(self):
        """Whitespace-only input."""
        result = parse_requirements("   \n\n")
        assert isinstance(result, ModuleRequirements)
        assert result.requirements == []

    def test_comment_only(self):
        """Comment-only input."""
        result = parse_requirements("# just a comment\n")
        assert isinstance(result, ModuleRequirements)
        assert result.requirements == []

    def test_yaml_doc_separator(self):
        """Document separator only."""
        result = parse_requirements("---\n")
        assert isinstance(result, ModuleRequirements)
        assert result.requirements == []

    def test_normal_documentation_still_works(self):
        """Regression: valid DOCUMENTATION still parses correctly."""
        doc = (
            "---\n"
            "module: test\n"
            "short_description: A test module\n"
            "requirements:\n"
            "  - boto3\n"
            "  - requests\n"
        )
        result = parse_requirements(doc)
        assert result.requirements == ["boto3", "requests"]
        assert result.short_description == "A test module"
