"""Tests for coverage tooling configuration and CI enforcement (#78).

Validates that:
- pyproject.toml has correct coverage configuration
- pytest-cov is installed and functional
- .github/workflows/ci.yml exists with correct structure
- Coverage XML output is configured for CI uploads
- Coverage threshold (fail_under) is set
"""

import tomllib
from pathlib import Path

import yaml

# Project root (one level up from tests/)
PROJECT_ROOT = Path(__file__).parent.parent


class TestPyprojectCoverageConfig:
    """Verify coverage configuration in pyproject.toml."""

    def setup_method(self):
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            self.config = tomllib.load(f)

    def test_pytest_cov_in_dev_dependencies(self):
        """pytest-cov must be listed as a dev dependency."""
        dev_deps = self.config["project"]["optional-dependencies"]["dev"]
        cov_deps = [d for d in dev_deps if d.startswith("pytest-cov")]
        assert len(cov_deps) == 1, "pytest-cov should be in [project.optional-dependencies.dev]"

    def test_cov_flag_in_addopts(self):
        """--cov=ftl2 must be in pytest addopts so coverage runs automatically."""
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        assert "--cov=ftl2" in addopts, "addopts must include --cov=ftl2"

    def test_cov_report_term_missing_in_addopts(self):
        """--cov-report=term-missing should be in addopts for local dev experience."""
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        assert "--cov-report=term-missing" in addopts

    def test_cov_report_html_in_addopts(self):
        """--cov-report=html should be in addopts for browsable reports."""
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        assert "--cov-report=html" in addopts

    def test_cov_report_xml_in_addopts(self):
        """--cov-report=xml must be in addopts for CI artifact uploads."""
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        assert "--cov-report=xml" in addopts, (
            "--cov-report=xml is required for CI coverage artifact uploads"
        )

    def test_coverage_run_source(self):
        """coverage.run source must point to src/ directory."""
        source = self.config["tool"]["coverage"]["run"]["source"]
        assert "src" in source

    def test_coverage_run_branch(self):
        """Branch coverage must be enabled."""
        assert self.config["tool"]["coverage"]["run"]["branch"] is True

    def test_coverage_report_fail_under(self):
        """fail_under threshold must be set and be a positive number."""
        fail_under = self.config["tool"]["coverage"]["report"]["fail_under"]
        assert isinstance(fail_under, (int, float))
        assert fail_under > 0, "fail_under must be positive"
        assert fail_under <= 100, "fail_under cannot exceed 100"

    def test_coverage_report_fail_under_is_60(self):
        """Current threshold is 60% (baseline, ratchet up over time)."""
        fail_under = self.config["tool"]["coverage"]["report"]["fail_under"]
        assert fail_under == 60

    def test_coverage_report_show_missing(self):
        """show_missing should be true for useful coverage reports."""
        assert self.config["tool"]["coverage"]["report"]["show_missing"] is True

    def test_gate_main_omitted_from_coverage(self):
        """ftl_gate/__main__.py should be omitted (runs as subprocess, not unit tested)."""
        omit = self.config["tool"]["coverage"]["run"]["omit"]
        gate_entries = [o for o in omit if "ftl_gate" in o and "__main__" in o]
        assert len(gate_entries) >= 1, "ftl_gate/__main__.py should be in coverage omit list"


class TestCIWorkflow:
    """Verify GitHub Actions CI workflow structure."""

    def setup_method(self):
        self.workflow_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

    def test_workflow_file_exists(self):
        """CI workflow file must exist."""
        assert self.workflow_path.exists(), (
            ".github/workflows/ci.yml must exist for CI enforcement"
        )

    def test_workflow_is_valid_yaml(self):
        """CI workflow must be valid YAML."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        assert isinstance(workflow, dict), "Workflow YAML must parse to a dict"

    def test_workflow_has_name(self):
        """Workflow should have a name."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        assert "name" in workflow

    def test_workflow_triggers_on_push_to_main(self):
        """Workflow must trigger on push to main branch."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        # PyYAML parses 'on' as boolean True
        triggers = workflow[True]
        assert "push" in triggers, "Workflow must trigger on push"
        push_branches = triggers["push"]["branches"]
        assert "main" in push_branches, "Push trigger must include main branch"

    def test_workflow_triggers_on_pull_request_to_main(self):
        """Workflow must trigger on PRs targeting main."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        # PyYAML parses 'on' as boolean True
        triggers = workflow[True]
        assert "pull_request" in triggers, "Workflow must trigger on pull_request"
        pr_branches = triggers["pull_request"]["branches"]
        assert "main" in pr_branches, "PR trigger must target main branch"

    def test_workflow_has_test_job(self):
        """Workflow must have a test job."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        assert "test" in workflow["jobs"], "Workflow must have a 'test' job"

    def test_workflow_uses_ubuntu(self):
        """Test job should run on ubuntu-latest."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        runs_on = workflow["jobs"]["test"]["runs-on"]
        assert "ubuntu" in runs_on, "Test job should run on Ubuntu"

    def test_workflow_uses_python_313(self):
        """Workflow must test with Python 3.13 (matching requires-python)."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        test_job = workflow["jobs"]["test"]
        # Check matrix or direct python-version
        if "strategy" in test_job and "matrix" in test_job["strategy"]:
            versions = test_job["strategy"]["matrix"]["python-version"]
            assert "3.13" in versions, "Matrix must include Python 3.13"
        else:
            # Check steps for setup-python with version
            steps = test_job["steps"]
            python_steps = [
                s for s in steps
                if "setup-python" in s.get("uses", "")
            ]
            assert len(python_steps) > 0, "Must use setup-python action"

    def test_workflow_uses_checkout(self):
        """Workflow must checkout the repo."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        checkout_steps = [
            s for s in steps
            if "checkout" in s.get("uses", "")
        ]
        assert len(checkout_steps) >= 1, "Must use actions/checkout"

    def test_workflow_installs_uv(self):
        """Workflow should install uv for dependency management."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        uv_steps = [
            s for s in steps
            if "setup-uv" in s.get("uses", "")
        ]
        assert len(uv_steps) >= 1, "Must use astral-sh/setup-uv"

    def test_workflow_runs_pytest(self):
        """Workflow must run pytest."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        run_commands = [s.get("run", "") for s in steps if "run" in s]
        pytest_found = any("pytest" in cmd for cmd in run_commands)
        assert pytest_found, "Workflow must run pytest"

    def test_workflow_uploads_coverage_artifacts(self):
        """Workflow should upload coverage artifacts."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        upload_steps = [
            s for s in steps
            if "upload-artifact" in s.get("uses", "")
        ]
        assert len(upload_steps) >= 1, "Should upload coverage artifacts"

    def test_workflow_uploads_on_failure(self):
        """Coverage artifact upload should run even on test failure."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        upload_steps = [
            s for s in steps
            if "upload-artifact" in s.get("uses", "")
        ]
        assert len(upload_steps) >= 1
        # The upload step should have if: always() to run even on failure
        upload_step = upload_steps[0]
        assert upload_step.get("if") == "always()", (
            "Coverage upload should use 'if: always()' to run even when tests fail"
        )

    def test_workflow_uploads_coverage_xml(self):
        """Upload artifact must include coverage.xml."""
        with open(self.workflow_path) as f:
            workflow = yaml.safe_load(f)
        steps = workflow["jobs"]["test"]["steps"]
        upload_steps = [
            s for s in steps
            if "upload-artifact" in s.get("uses", "")
        ]
        assert len(upload_steps) >= 1
        path = upload_steps[0].get("with", {}).get("path", "")
        assert "coverage.xml" in path, "Upload must include coverage.xml"


class TestGitignore:
    """Verify coverage artifacts are gitignored."""

    def setup_method(self):
        self.gitignore = (PROJECT_ROOT / ".gitignore").read_text()

    def test_coverage_xml_gitignored(self):
        """coverage.xml should be in .gitignore."""
        assert "coverage.xml" in self.gitignore

    def test_htmlcov_gitignored(self):
        """htmlcov/ should be in .gitignore."""
        assert "htmlcov/" in self.gitignore

    def test_dot_coverage_gitignored(self):
        """.coverage files should be in .gitignore."""
        assert ".coverage" in self.gitignore


class TestPytestCovInstalled:
    """Verify pytest-cov is actually installed and functional."""

    def test_pytest_cov_importable(self):
        """pytest-cov must be installed."""
        import pytest_cov  # noqa: F401

    def test_coverage_importable(self):
        """coverage package must be installed."""
        import coverage  # noqa: F401
