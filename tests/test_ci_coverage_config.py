"""Tests for CI workflow and coverage configuration (issue #78).

Validates that:
- The GitHub Actions CI workflow is well-formed and correct
- Coverage tooling is properly configured in pyproject.toml
- The conflicting [dependency-groups] section has been removed
- .gitignore excludes coverage artifacts
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

# Project root relative to this test file
PROJECT_ROOT = Path(__file__).parent.parent


class TestCIWorkflow:
    """Validate .github/workflows/ci.yml structure and correctness."""

    def setup_method(self):
        ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_path.exists(), f"CI workflow not found at {ci_path}"
        with open(ci_path) as f:
            self.workflow = yaml.safe_load(f)

    def test_workflow_has_name(self):
        assert "name" in self.workflow
        assert self.workflow["name"] == "CI"

    def test_triggers_on_push_to_main(self):
        triggers = self.workflow.get(True, self.workflow.get("on"))
        assert triggers is not None, "No trigger configuration found"
        assert "push" in triggers
        assert "main" in triggers["push"]["branches"]

    def test_triggers_on_pull_request(self):
        triggers = self.workflow.get(True, self.workflow.get("on"))
        assert "pull_request" in triggers

    def test_permissions_least_privilege(self):
        perms = self.workflow.get("permissions", {})
        assert perms.get("contents") == "read", (
            "Workflow should use least-privilege permissions (contents: read)"
        )

    def test_runs_on_ubuntu(self):
        job = self.workflow["jobs"]["test"]
        assert "ubuntu" in job["runs-on"]

    def test_uses_python_313(self):
        """Python version must match requires-python >= 3.13."""
        job = self.workflow["jobs"]["test"]
        setup_step = next(
            s for s in job["steps"]
            if s.get("name", "").startswith("Set up Python")
        )
        assert setup_step["with"]["python-version"] == "3.13"

    def test_installs_dev_dependencies(self):
        job = self.workflow["jobs"]["test"]
        install_step = next(
            s for s in job["steps"]
            if s.get("name", "").startswith("Install")
        )
        assert ".[dev]" in install_step["run"]

    def test_runs_linter(self):
        job = self.workflow["jobs"]["test"]
        lint_step = next(
            s for s in job["steps"]
            if s.get("name", "") == "Run linter"
        )
        assert "ruff check" in lint_step["run"]
        assert "src/" in lint_step["run"]
        assert "tests/" in lint_step["run"]

    def test_runs_tests_with_coverage(self):
        job = self.workflow["jobs"]["test"]
        test_step = next(
            s for s in job["steps"]
            if s.get("name", "") == "Run tests with coverage"
        )
        assert "pytest" in test_step["run"]
        assert "--cov-report=xml" in test_step["run"]

    def test_uploads_coverage_artifacts(self):
        job = self.workflow["jobs"]["test"]
        upload_step = next(
            s for s in job["steps"]
            if s.get("name", "") == "Upload coverage report"
        )
        assert upload_step.get("if") == "always()"
        assert "actions/upload-artifact" in upload_step["uses"]
        paths = upload_step["with"]["path"]
        assert "htmlcov/" in paths
        assert "coverage.xml" in paths

    def test_step_order_is_correct(self):
        """Linting should run before tests (fail fast on style issues)."""
        job = self.workflow["jobs"]["test"]
        step_names = [s.get("name", "") for s in job["steps"]]
        lint_idx = step_names.index("Run linter")
        test_idx = step_names.index("Run tests with coverage")
        assert lint_idx < test_idx, "Linter should run before tests"


class TestCoverageConfig:
    """Validate [tool.coverage.*] sections in pyproject.toml."""

    def setup_method(self):
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            self.config = tomllib.load(f)

    def test_coverage_run_source(self):
        source = self.config["tool"]["coverage"]["run"]["source"]
        assert source == ["src"]

    def test_coverage_run_branch_enabled(self):
        assert self.config["tool"]["coverage"]["run"]["branch"] is True

    def test_coverage_run_omits_gate_main(self):
        """Gate __main__.py runs as subprocess — should be omitted."""
        omit = self.config["tool"]["coverage"]["run"].get("omit", [])
        assert any("ftl_gate/__main__.py" in o for o in omit)

    def test_coverage_report_fail_under(self):
        fail_under = self.config["tool"]["coverage"]["report"]["fail_under"]
        assert isinstance(fail_under, int)
        assert fail_under > 0, "fail_under must be positive"

    def test_coverage_report_show_missing(self):
        assert self.config["tool"]["coverage"]["report"]["show_missing"] is True


class TestPytestConfig:
    """Validate [tool.pytest.ini_options] in pyproject.toml."""

    def setup_method(self):
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            self.config = tomllib.load(f)
        self.pytest_cfg = self.config["tool"]["pytest"]["ini_options"]

    def test_testpaths(self):
        assert self.pytest_cfg["testpaths"] == ["tests"]

    def test_asyncio_mode_auto(self):
        assert self.pytest_cfg["asyncio_mode"] == "auto"

    def test_addopts_includes_cov(self):
        addopts = self.pytest_cfg["addopts"]
        assert "--cov=ftl2" in addopts

    def test_addopts_includes_term_missing(self):
        addopts = self.pytest_cfg["addopts"]
        assert "--cov-report=term-missing" in addopts

    def test_addopts_includes_html_report(self):
        addopts = self.pytest_cfg["addopts"]
        assert "--cov-report=html" in addopts

    def test_strict_markers(self):
        addopts = self.pytest_cfg["addopts"]
        assert "--strict-markers" in addopts


class TestDevDependencies:
    """Validate dev dependencies include all required tooling."""

    def setup_method(self):
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            self.config = tomllib.load(f)
        self.dev_deps = self.config["project"]["optional-dependencies"]["dev"]

    def test_pytest_cov_in_dev_deps(self):
        assert any("pytest-cov" in d for d in self.dev_deps)

    def test_pytest_in_dev_deps(self):
        assert any(d.startswith("pytest>=") for d in self.dev_deps)

    def test_pytest_asyncio_in_dev_deps(self):
        assert any("pytest-asyncio" in d for d in self.dev_deps)

    def test_ruff_in_dev_deps(self):
        assert any("ruff" in d for d in self.dev_deps)

    def test_mypy_in_dev_deps(self):
        assert any("mypy" in d for d in self.dev_deps)

    def test_dependency_groups_removed(self):
        """PEP 735 [dependency-groups] conflicted with [project.optional-dependencies]."""
        assert "dependency-groups" not in self.config, (
            "[dependency-groups] section should be removed — it conflicted "
            "with [project.optional-dependencies] dev"
        )


class TestGitignoreCoverageEntries:
    """Validate .gitignore excludes coverage artifacts."""

    def setup_method(self):
        gitignore = PROJECT_ROOT / ".gitignore"
        assert gitignore.exists()
        self.lines = gitignore.read_text().splitlines()

    def test_htmlcov_ignored(self):
        assert any("htmlcov" in line for line in self.lines)

    def test_coverage_file_ignored(self):
        assert any(line.strip() == ".coverage" for line in self.lines)

    def test_coverage_xml_ignored(self):
        assert any("coverage.xml" in line for line in self.lines)


class TestCIAndConfigConsistency:
    """Cross-validate CI workflow against pyproject.toml config."""

    def setup_method(self):
        with open(PROJECT_ROOT / ".github" / "workflows" / "ci.yml") as f:
            self.workflow = yaml.safe_load(f)
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            self.config = tomllib.load(f)

    def test_ci_python_matches_requires_python(self):
        """CI Python version should satisfy requires-python."""
        requires = self.config["project"]["requires-python"]  # ">=3.13"
        job = self.workflow["jobs"]["test"]
        setup_step = next(
            s for s in job["steps"]
            if s.get("name", "").startswith("Set up Python")
        )
        ci_version = setup_step["with"]["python-version"]
        # Extract minimum version from requires-python
        min_version = requires.replace(">=", "").strip()
        assert ci_version.startswith(min_version.rsplit(".", 1)[0]), (
            f"CI Python {ci_version} should match requires-python {requires}"
        )

    def test_ci_coverage_xml_supplements_addopts(self):
        """CI adds --cov-report=xml; addopts already has --cov=ftl2."""
        addopts = self.config["tool"]["pytest"]["ini_options"]["addopts"]
        assert "--cov=ftl2" in addopts, "addopts must provide --cov=ftl2"

        job = self.workflow["jobs"]["test"]
        test_step = next(
            s for s in job["steps"]
            if "pytest" in s.get("run", "")
        )
        assert "--cov-report=xml" in test_step["run"], (
            "CI must add --cov-report=xml for artifact upload"
        )

    def test_ci_installs_dev_extras(self):
        """CI install command uses [dev] extras matching optional-dependencies."""
        assert "dev" in self.config["project"]["optional-dependencies"]
        job = self.workflow["jobs"]["test"]
        install_step = next(
            s for s in job["steps"]
            if "pip install" in s.get("run", "")
        )
        assert ".[dev]" in install_step["run"]
