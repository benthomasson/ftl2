"""Workflow tracking for FTL2.

Provides functionality to track multi-step workflows, correlating
multiple executions under a single workflow ID.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default workflow directory
DEFAULT_WORKFLOW_DIR = Path.home() / ".ftl2" / "workflows"


@dataclass
class WorkflowStep:
    """A single step in a workflow.

    Attributes:
        step_name: Name/label for this step
        module: Module that was executed
        args: Arguments passed to the module
        timestamp: When the step was executed
        duration: Execution duration in seconds
        total_hosts: Total hosts in the step
        successful: Number of successful hosts
        failed: Number of failed hosts
        failed_hosts: List of hosts that failed
    """

    step_name: str
    module: str
    args: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    duration: float = 0.0
    total_hosts: int = 0
    successful: int = 0
    failed: int = 0
    failed_hosts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "step_name": self.step_name,
            "module": self.module,
            "args": self.args,
            "timestamp": self.timestamp,
            "duration": round(self.duration, 3),
            "total_hosts": self.total_hosts,
            "successful": self.successful,
            "failed": self.failed,
            "failed_hosts": self.failed_hosts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowStep":
        """Create from dictionary."""
        return cls(
            step_name=data["step_name"],
            module=data["module"],
            args=data.get("args", {}),
            timestamp=data.get("timestamp", ""),
            duration=data.get("duration", 0.0),
            total_hosts=data.get("total_hosts", 0),
            successful=data.get("successful", 0),
            failed=data.get("failed", 0),
            failed_hosts=data.get("failed_hosts", []),
        )


@dataclass
class Workflow:
    """A workflow containing multiple execution steps.

    Attributes:
        workflow_id: Unique identifier for the workflow
        created: When the workflow was created
        updated: When the workflow was last updated
        steps: List of execution steps
    """

    workflow_id: str
    created: str = ""
    updated: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Set timestamps if not provided."""
        if not self.created:
            self.created = datetime.now(UTC).isoformat()
        if not self.updated:
            self.updated = self.created

    def add_step(self, step: WorkflowStep) -> None:
        """Add a step to the workflow."""
        self.steps.append(step)
        self.updated = datetime.now(UTC).isoformat()

    def get_total_duration(self) -> float:
        """Get total duration of all steps."""
        return sum(step.duration for step in self.steps)

    def get_total_successful(self) -> int:
        """Get total successful executions across all steps."""
        return sum(step.successful for step in self.steps)

    def get_total_failed(self) -> int:
        """Get total failed executions across all steps."""
        return sum(step.failed for step in self.steps)

    def get_all_failed_hosts(self) -> dict[str, list[str]]:
        """Get all failed hosts grouped by step."""
        result = {}
        for step in self.steps:
            if step.failed_hosts:
                result[step.step_name] = step.failed_hosts
        return result

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "workflow_id": self.workflow_id,
            "created": self.created,
            "updated": self.updated,
            "steps": [step.to_dict() for step in self.steps],
            "summary": {
                "total_steps": len(self.steps),
                "total_duration": round(self.get_total_duration(), 3),
                "total_successful": self.get_total_successful(),
                "total_failed": self.get_total_failed(),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Workflow":
        """Create from dictionary."""
        steps = [WorkflowStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            workflow_id=data["workflow_id"],
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            steps=steps,
        )

    def format_report(self) -> str:
        """Format a human-readable workflow report."""
        lines = [
            "",
            f"Workflow: {self.workflow_id}",
            f"Created: {self.created}",
            f"Updated: {self.updated}",
            "",
            "Steps:",
            "-" * 50,
        ]

        for i, step in enumerate(self.steps, 1):
            status = "✓" if step.failed == 0 else "✗"
            lines.append(
                f"  {i}. {step.step_name} ({step.module}): "
                f"{status} {step.successful}/{step.total_hosts} succeeded ({step.duration:.2f}s)"
            )
            if step.failed_hosts:
                for host in step.failed_hosts:
                    lines.append(f"       ✗ {host}")

        lines.append("-" * 50)
        lines.append(f"Total Steps: {len(self.steps)}")
        lines.append(f"Total Duration: {self.get_total_duration():.2f}s")
        lines.append(f"Total Successful: {self.get_total_successful()}")
        lines.append(f"Total Failed: {self.get_total_failed()}")

        failed_hosts = self.get_all_failed_hosts()
        if failed_hosts:
            lines.append("")
            lines.append("Failed Hosts:")
            for step_name, hosts in failed_hosts.items():
                lines.append(f"  {step_name}: {', '.join(hosts)}")

        lines.append("")
        return "\n".join(lines)


def get_workflow_path(workflow_id: str, workflow_dir: Path | None = None) -> Path:
    """Get the path to a workflow file.

    Args:
        workflow_id: Workflow identifier
        workflow_dir: Optional custom workflow directory

    Returns:
        Path to the workflow file
    """
    base_dir = workflow_dir or DEFAULT_WORKFLOW_DIR
    # Sanitize workflow ID for use in filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in workflow_id)
    return base_dir / f"{safe_id}.json"


def load_workflow(workflow_id: str, workflow_dir: Path | None = None) -> Workflow | None:
    """Load a workflow from disk.

    Args:
        workflow_id: Workflow identifier
        workflow_dir: Optional custom workflow directory

    Returns:
        Workflow if found, None otherwise
    """
    path = get_workflow_path(workflow_id, workflow_dir)

    if not path.exists():
        logger.debug(f"Workflow not found: {path}")
        return None

    try:
        with path.open() as f:
            data = json.load(f)
        return Workflow.from_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to load workflow {workflow_id}: {e}")
        return None


def save_workflow(workflow: Workflow, workflow_dir: Path | None = None) -> Path:
    """Save a workflow to disk.

    Args:
        workflow: Workflow to save
        workflow_dir: Optional custom workflow directory

    Returns:
        Path where workflow was saved
    """
    path = get_workflow_path(workflow.workflow_id, workflow_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w") as f:
        json.dump(workflow.to_dict(), f, indent=2)

    logger.info(f"Workflow saved to {path}")
    return path


def list_workflows(workflow_dir: Path | None = None) -> list[str]:
    """List all workflow IDs.

    Args:
        workflow_dir: Optional custom workflow directory

    Returns:
        List of workflow IDs
    """
    base_dir = workflow_dir or DEFAULT_WORKFLOW_DIR

    if not base_dir.exists():
        return []

    workflows = []
    for path in base_dir.glob("*.json"):
        workflows.append(path.stem)

    return sorted(workflows)


def delete_workflow(workflow_id: str, workflow_dir: Path | None = None) -> bool:
    """Delete a workflow.

    Args:
        workflow_id: Workflow identifier
        workflow_dir: Optional custom workflow directory

    Returns:
        True if deleted, False if not found
    """
    path = get_workflow_path(workflow_id, workflow_dir)

    if not path.exists():
        return False

    path.unlink()
    logger.info(f"Workflow deleted: {workflow_id}")
    return True


def add_step_to_workflow(
    workflow_id: str,
    step: WorkflowStep,
    workflow_dir: Path | None = None,
) -> Workflow:
    """Add a step to a workflow, creating it if needed.

    Args:
        workflow_id: Workflow identifier
        step: Step to add
        workflow_dir: Optional custom workflow directory

    Returns:
        Updated workflow
    """
    workflow = load_workflow(workflow_id, workflow_dir)

    if workflow is None:
        workflow = Workflow(workflow_id=workflow_id)

    workflow.add_step(step)
    save_workflow(workflow, workflow_dir)

    return workflow
