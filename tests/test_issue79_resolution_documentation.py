"""Tests for GH-79: Add resolution documentation to closed issues.

This issue was resolved by discovering that PRs already exist for all 29
closed hardening issues via closingIssuesReferences. The beliefs registry
was updated to retract the incorrect assumptions.

No source code was modified — only beliefs.md was changed.
"""

import re
import subprocess
from pathlib import Path

import pytest


# Paths relative to the repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
BELIEFS_FILE = REPO_ROOT / "beliefs.md"
SRC_DIR = REPO_ROOT / "src"


class TestBeliefsFileExists:
    """Verify the beliefs registry file exists and is well-formed."""

    def test_beliefs_file_exists(self):
        """beliefs.md must exist at the workspace root."""
        assert BELIEFS_FILE.exists(), f"beliefs.md not found at {BELIEFS_FILE}"

    def test_beliefs_file_not_empty(self):
        """beliefs.md must have content."""
        content = BELIEFS_FILE.read_text()
        assert len(content.strip()) > 0, "beliefs.md is empty"

    def test_beliefs_file_has_heading(self):
        """beliefs.md should start with a markdown heading."""
        content = BELIEFS_FILE.read_text()
        assert content.strip().startswith("# "), "beliefs.md should start with a # heading"


class TestRetractedBeliefs:
    """Verify that the two incorrect beliefs were properly retracted."""

    @pytest.fixture
    def beliefs_content(self):
        return BELIEFS_FILE.read_text()

    def test_resolution_documentation_absent_retracted(self, beliefs_content):
        """resolution-documentation-systematically-absent must be [OUT] RETRACTED."""
        pattern = r"resolution-documentation-systematically-absent\s+\[OUT\]\s+RETRACTED"
        assert re.search(pattern, beliefs_content), (
            "Belief 'resolution-documentation-systematically-absent' should be marked [OUT] RETRACTED"
        )

    def test_no_verification_trail_retracted(self, beliefs_content):
        """no-verification-trail-for-resolutions must be [OUT] RETRACTED."""
        pattern = r"no-verification-trail-for-resolutions\s+\[OUT\]\s+RETRACTED"
        assert re.search(pattern, beliefs_content), (
            "Belief 'no-verification-trail-for-resolutions' should be marked [OUT] RETRACTED"
        )

    def test_retracted_beliefs_have_rationale(self, beliefs_content):
        """Each retracted belief should have a 'Retracted:' explanation."""
        # Find the Retractions section (split on h2 boundary, not h3)
        match = re.search(
            r"^## Retractions\n(.*?)(?=\n## [^#]|\Z)",
            beliefs_content, re.DOTALL | re.MULTILINE
        )
        assert match, "Missing '## Retractions' section"
        retraction_text = match.group(1)
        retracted_count = retraction_text.count("**Retracted:**")
        assert retracted_count == 2, (
            f"Expected 2 retracted beliefs with rationale, found {retracted_count}"
        )

    def test_retracted_beliefs_reference_plan(self, beliefs_content):
        """Retracted beliefs should reference the plan that retracted them."""
        assert beliefs_content.count("Retracted by: plan-1-2") == 2, (
            "Both retracted beliefs should reference 'plan-1-2' as the retractor"
        )

    def test_retracted_beliefs_have_dates(self, beliefs_content):
        """Retracted beliefs should have date stamps."""
        match = re.search(
            r"^## Retractions\n(.*?)(?=\n## [^#]|\Z)",
            beliefs_content, re.DOTALL | re.MULTILINE
        )
        assert match, "Missing '## Retractions' section"
        retraction_text = match.group(1)
        date_count = len(re.findall(r"Date: \d{4}-\d{2}-\d{2}", retraction_text))
        assert date_count == 2, f"Expected 2 dates in retractions, found {date_count}"


class TestUnblockedBeliefs:
    """Verify that downstream beliefs were properly unblocked."""

    @pytest.fixture
    def beliefs_content(self):
        return BELIEFS_FILE.read_text()

    def test_hardening_gains_unblocked(self, beliefs_content):
        """hardening-gains-survive-contributor-change should be [UNBLOCKED]."""
        assert "hardening-gains-survive-contributor-change [UNBLOCKED]" in beliefs_content

    def test_project_handoff_unblocked(self, beliefs_content):
        """project-handoff-viable should be [UNBLOCKED]."""
        assert "project-handoff-viable [UNBLOCKED]" in beliefs_content

    def test_next_cleanup_unblocked(self, beliefs_content):
        """next-cleanup-achieves-verified-resolution should be [UNBLOCKED]."""
        assert "next-cleanup-achieves-verified-resolution [UNBLOCKED]" in beliefs_content

    def test_unblocked_beliefs_reference_retracted_blockers(self, beliefs_content):
        """Unblocked beliefs should explain what was previously blocking them."""
        unblocked_section = beliefs_content.split("## Unblocked")[1] if "## Unblocked" in beliefs_content else ""
        assert "resolution-documentation-systematically-absent" in unblocked_section, (
            "Unblocked section should reference the retracted blocker"
        )
        assert "no-verification-trail-for-resolutions" in unblocked_section, (
            "Unblocked section should reference the retracted blocker"
        )


class TestAxioms:
    """Verify that the plan axioms are correctly recorded."""

    @pytest.fixture
    def beliefs_content(self):
        return BELIEFS_FILE.read_text()

    def test_plan_1_1_axiom_exists(self, beliefs_content):
        """plan-1-1 axiom (close GH-79) should be [IN] AXIOM."""
        assert "plan-1-1 [IN] AXIOM" in beliefs_content

    def test_plan_1_2_axiom_exists(self, beliefs_content):
        """plan-1-2 axiom (retract beliefs) should be [IN] AXIOM."""
        assert "plan-1-2 [IN] AXIOM" in beliefs_content

    def test_plan_1_3_axiom_exists(self, beliefs_content):
        """plan-1-3 axiom (no code changes) should be [IN] AXIOM."""
        assert "plan-1-3 [IN] AXIOM" in beliefs_content


class TestNoSourceCodeModified:
    """Verify that no source code under src/ was modified by this task."""

    def test_src_unchanged_from_main(self):
        """No files under src/ should differ from the main branch."""
        result = subprocess.run(
            ["git", "diff", "main", "--name-only", "--", "src/"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        changed_files = result.stdout.strip()
        assert changed_files == "", (
            f"Source files should not be modified by GH-79, but found changes:\n{changed_files}"
        )

    def test_tests_unchanged_from_main(self):
        """No pre-existing test files should differ from the main branch.

        This test file itself is new, so we check only for modifications
        to existing files, not additions.
        """
        result = subprocess.run(
            ["git", "diff", "main", "--diff-filter=M", "--name-only", "--", "tests/"],
            capture_output=True, text=True, cwd=REPO_ROOT
        )
        modified_files = result.stdout.strip()
        assert modified_files == "", (
            f"Existing test files should not be modified by GH-79:\n{modified_files}"
        )


class TestBeliefRegistryConsistency:
    """Cross-check belief states for internal consistency."""

    @pytest.fixture
    def beliefs_content(self):
        return BELIEFS_FILE.read_text()

    def test_no_belief_is_both_in_and_out(self, beliefs_content):
        """No belief name should appear as both [IN] and [OUT]."""
        in_beliefs = set(re.findall(r"### (\S+) \[IN\]", beliefs_content))
        out_beliefs = set(re.findall(r"### (\S+) \[OUT\]", beliefs_content))
        overlap = in_beliefs & out_beliefs
        assert overlap == set(), f"Beliefs cannot be both [IN] and [OUT]: {overlap}"

    def test_retracted_beliefs_are_out(self, beliefs_content):
        """All RETRACTED beliefs should have [OUT] state."""
        retracted = re.findall(r"### (\S+) \[(\w+)\] RETRACTED", beliefs_content)
        for name, state in retracted:
            assert state == "OUT", f"Retracted belief '{name}' has state [{state}], expected [OUT]"

    def test_axioms_are_in(self, beliefs_content):
        """All AXIOM beliefs should have [IN] state."""
        axioms = re.findall(r"### (\S+) \[(\w+)\] AXIOM", beliefs_content)
        for name, state in axioms:
            assert state == "IN", f"Axiom '{name}' has state [{state}], expected [IN]"

    def test_no_orphan_retraction_references(self, beliefs_content):
        """'Retracted by' references should point to existing beliefs."""
        retracted_by = re.findall(r"Retracted by: (\S+)", beliefs_content)
        for ref in retracted_by:
            assert ref in beliefs_content, (
                f"Retraction references '{ref}' but that belief is not defined"
            )


class TestNogoods:
    """Verify no contradictions were introduced."""

    def test_nogoods_file_empty(self):
        """nogoods.md should have no active contradictions for this task."""
        nogoods_file = REPO_ROOT / "nogoods.md"
        if nogoods_file.exists():
            content = nogoods_file.read_text()
            # Strip the heading — check if there's any actual nogood content
            lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#")]
            assert len(lines) == 0, (
                f"nogoods.md should be empty for this task, but contains: {lines}"
            )
