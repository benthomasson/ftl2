"""Event streaming for FTL2 modules.

Provides a protocol for modules to emit incremental progress events
during execution. Events are emitted as JSON-lines to stderr, while
the final result remains on stdout.

Example:
    from ftl2.events import emit_progress, emit_log

    def my_module():
        emit_progress(0, "Starting...")
        # do work
        emit_progress(50, "Halfway there")
        # more work
        emit_progress(100, "Complete")
        return {"changed": True}
"""

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ModuleEvent:
    """Base event emitted by modules.

    All events include a type identifier and timestamp. Subclasses
    add event-specific fields.

    Attributes:
        event: Event type identifier (e.g., "progress", "log", "data")
        timestamp: Unix timestamp when event was created
    """

    event: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(self.to_dict())

    def emit(self) -> None:
        """Emit event to stderr as JSON line.

        Events are written to stderr with a trailing newline and
        flushed immediately to ensure real-time delivery.
        """
        print(self.to_json(), file=sys.stderr, flush=True)


@dataclass
class ProgressEvent(ModuleEvent):
    """Progress update event.

    Used to report incremental progress during long-running operations
    like file transfers, package installations, or bulk operations.

    Attributes:
        percent: Progress percentage (0-100)
        message: Human-readable status message
        current: Current item count (e.g., bytes transferred)
        total: Total item count (e.g., total bytes)
        task_id: Optional task identifier for multi-task progress
    """

    event: str = field(default="progress", init=False)
    percent: int = 0
    message: str = ""
    current: int | None = None
    total: int | None = None
    task_id: str | None = None


@dataclass
class LogEvent(ModuleEvent):
    """Log message event.

    Used to emit log messages during module execution that should
    be visible to the user in real-time.

    Attributes:
        level: Log level (debug, info, warning, error)
        message: Log message text
    """

    event: str = field(default="log", init=False)
    level: str = "info"
    message: str = ""


@dataclass
class DataEvent(ModuleEvent):
    """Incremental data event.

    Used to stream output data in real-time, such as command stdout
    or log file contents.

    Attributes:
        stream: Stream identifier (stdout, stderr, or custom)
        data: Data content as string
    """

    event: str = field(default="data", init=False)
    stream: str = "stdout"
    data: str = ""


# Convenience functions for common event emission patterns


def emit_progress(
    percent: int,
    message: str = "",
    current: int | None = None,
    total: int | None = None,
    task_id: str | None = None,
) -> None:
    """Emit a progress event.

    Args:
        percent: Progress percentage (0-100)
        message: Human-readable status message
        current: Current item count (optional)
        total: Total item count (optional)
        task_id: Task identifier for multi-task progress (optional)

    Example:
        emit_progress(25, "Downloading file", current=256000, total=1024000)
    """
    ProgressEvent(
        percent=percent,
        message=message,
        current=current,
        total=total,
        task_id=task_id,
    ).emit()


def emit_log(message: str, level: str = "info") -> None:
    """Emit a log event.

    Args:
        message: Log message text
        level: Log level (debug, info, warning, error)

    Example:
        emit_log("Starting download", level="info")
        emit_log("Connection failed, retrying", level="warning")
    """
    LogEvent(message=message, level=level).emit()


def emit_data(data: str, stream: str = "stdout") -> None:
    """Emit a data event.

    Args:
        data: Data content as string
        stream: Stream identifier (stdout, stderr, or custom)

    Example:
        emit_data("Command output line 1\\n", stream="stdout")
    """
    DataEvent(data=data, stream=stream).emit()


# Event parsing utilities for the executor side


def parse_event(line: str) -> dict[str, Any] | None:
    """Parse a JSON event line.

    Args:
        line: A line of text that may be a JSON event

    Returns:
        Parsed event dict if valid, None otherwise
    """
    line = line.strip()
    if not line.startswith("{") or not line.endswith("}"):
        return None

    try:
        event = json.loads(line)
        if isinstance(event, dict) and "event" in event:
            return event
    except json.JSONDecodeError:
        pass

    return None


def parse_events(stderr: str) -> tuple[list[dict[str, Any]], str]:
    """Parse JSON-line events from stderr output.

    Separates event lines from regular stderr output.

    Args:
        stderr: Full stderr output from a module

    Returns:
        Tuple of (list of parsed events, remaining non-event stderr text)

    Example:
        events, other_stderr = parse_events(result.stderr)
        for event in events:
            if event["event"] == "progress":
                print(f"Progress: {event['percent']}%")
    """
    events: list[dict[str, Any]] = []
    other_lines: list[str] = []

    for line in stderr.splitlines():
        event = parse_event(line)
        if event is not None:
            events.append(event)
        else:
            other_lines.append(line)

    return events, "\n".join(other_lines)
