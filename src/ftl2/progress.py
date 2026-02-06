"""Progress reporting for FTL2.

Provides callback-based progress tracking for module execution,
supporting both text and JSON output formats.

Also includes EventProgressDisplay for displaying real-time module
events (progress, log, data) with Rich progress bars.
"""

import json
import sys
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generator, Protocol

from rich.console import Console
from rich.live import Live
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text


@dataclass
class ProgressEvent:
    """A progress event during execution.

    Attributes:
        event_type: Type of event (started, completed, failed, retrying)
        host: Host name
        timestamp: When the event occurred
        details: Additional event-specific details
    """

    event_type: str
    host: str
    timestamp: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "event": self.event_type,
            "host": self.host,
            "timestamp": self.timestamp,
        }
        result.update(self.details)
        return result

    def to_json(self) -> str:
        """Convert to JSON string (NDJSON format)."""
        return json.dumps(self.to_dict())


class ProgressCallback(Protocol):
    """Protocol for progress callbacks."""

    def __call__(self, event: ProgressEvent) -> None:
        """Handle a progress event."""
        ...


class ProgressReporter(ABC):
    """Base class for progress reporters."""

    @abstractmethod
    def on_execution_start(self, total_hosts: int, module: str) -> None:
        """Called when execution starts."""
        pass

    @abstractmethod
    def on_host_start(self, host: str) -> None:
        """Called when a host execution starts."""
        pass

    @abstractmethod
    def on_host_complete(
        self,
        host: str,
        success: bool,
        changed: bool,
        duration: float,
        error: str | None = None,
    ) -> None:
        """Called when a host execution completes."""
        pass

    @abstractmethod
    def on_host_retry(
        self,
        host: str,
        attempt: int,
        max_attempts: int,
        error: str,
        delay: float,
    ) -> None:
        """Called when a host is about to be retried."""
        pass

    @abstractmethod
    def on_execution_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        duration: float,
    ) -> None:
        """Called when execution completes."""
        pass


class JsonProgressReporter(ProgressReporter):
    """Reports progress as NDJSON (newline-delimited JSON) events."""

    def __init__(self, output: Any = None) -> None:
        """Initialize JSON progress reporter.

        Args:
            output: Output stream (defaults to sys.stderr to not pollute stdout)
        """
        self.output = output or sys.stderr

    def _emit(self, event: ProgressEvent) -> None:
        """Emit a progress event."""
        print(event.to_json(), file=self.output, flush=True)

    def _now(self) -> str:
        """Get current timestamp."""
        return datetime.now(timezone.utc).isoformat()

    def on_execution_start(self, total_hosts: int, module: str) -> None:
        """Called when execution starts."""
        self._emit(ProgressEvent(
            event_type="execution_start",
            host="*",
            timestamp=self._now(),
            details={"total_hosts": total_hosts, "module": module},
        ))

    def on_host_start(self, host: str) -> None:
        """Called when a host execution starts."""
        self._emit(ProgressEvent(
            event_type="host_start",
            host=host,
            timestamp=self._now(),
            details={},
        ))

    def on_host_complete(
        self,
        host: str,
        success: bool,
        changed: bool,
        duration: float,
        error: str | None = None,
    ) -> None:
        """Called when a host execution completes."""
        details: dict[str, Any] = {
            "success": success,
            "changed": changed,
            "duration": round(duration, 3),
        }
        if error:
            details["error"] = error

        self._emit(ProgressEvent(
            event_type="host_complete",
            host=host,
            timestamp=self._now(),
            details=details,
        ))

    def on_host_retry(
        self,
        host: str,
        attempt: int,
        max_attempts: int,
        error: str,
        delay: float,
    ) -> None:
        """Called when a host is about to be retried."""
        self._emit(ProgressEvent(
            event_type="host_retry",
            host=host,
            timestamp=self._now(),
            details={
                "attempt": attempt,
                "max_attempts": max_attempts,
                "error": error,
                "delay": round(delay, 1),
            },
        ))

    def on_execution_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        duration: float,
    ) -> None:
        """Called when execution completes."""
        self._emit(ProgressEvent(
            event_type="execution_complete",
            host="*",
            timestamp=self._now(),
            details={
                "total": total,
                "successful": successful,
                "failed": failed,
                "duration": round(duration, 3),
            },
        ))


class TextProgressReporter(ProgressReporter):
    """Reports progress as human-readable text."""

    def __init__(self, output: Any = None) -> None:
        """Initialize text progress reporter.

        Args:
            output: Output stream (defaults to sys.stderr)
        """
        self.output = output or sys.stderr
        self.completed = 0
        self.total = 0

    def _emit(self, message: str) -> None:
        """Emit a progress message."""
        print(message, file=self.output, flush=True)

    def on_execution_start(self, total_hosts: int, module: str) -> None:
        """Called when execution starts."""
        self.total = total_hosts
        self.completed = 0
        self._emit(f"Executing module '{module}' on {total_hosts} host(s)...")

    def on_host_start(self, host: str) -> None:
        """Called when a host execution starts."""
        # Don't emit for start in text mode to reduce noise
        pass

    def on_host_complete(
        self,
        host: str,
        success: bool,
        changed: bool,
        duration: float,
        error: str | None = None,
    ) -> None:
        """Called when a host execution completes."""
        self.completed += 1
        status = "✓" if success else "✗"
        changed_str = " (changed)" if changed else ""

        if success:
            self._emit(f"  [{self.completed}/{self.total}] {status} {host}{changed_str} ({duration:.2f}s)")
        else:
            error_msg = f": {error}" if error else ""
            self._emit(f"  [{self.completed}/{self.total}] {status} {host} FAILED{error_msg}")

    def on_host_retry(
        self,
        host: str,
        attempt: int,
        max_attempts: int,
        error: str,
        delay: float,
    ) -> None:
        """Called when a host is about to be retried."""
        self._emit(f"  ⟳ {host}: retrying in {delay:.0f}s (attempt {attempt}/{max_attempts}): {error}")

    def on_execution_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        duration: float,
    ) -> None:
        """Called when execution completes."""
        if failed == 0:
            self._emit(f"Completed: {successful}/{total} succeeded in {duration:.2f}s")
        else:
            self._emit(f"Completed: {successful}/{total} succeeded, {failed} failed in {duration:.2f}s")


class NullProgressReporter(ProgressReporter):
    """No-op progress reporter that discards all events."""

    def on_execution_start(self, total_hosts: int, module: str) -> None:
        pass

    def on_host_start(self, host: str) -> None:
        pass

    def on_host_complete(
        self,
        host: str,
        success: bool,
        changed: bool,
        duration: float,
        error: str | None = None,
    ) -> None:
        pass

    def on_host_retry(
        self,
        host: str,
        attempt: int,
        max_attempts: int,
        error: str,
        delay: float,
    ) -> None:
        pass

    def on_execution_complete(
        self,
        total: int,
        successful: int,
        failed: int,
        duration: float,
    ) -> None:
        pass


def create_progress_reporter(
    enabled: bool,
    json_format: bool = False,
    output: Any = None,
) -> ProgressReporter:
    """Create a progress reporter.

    Args:
        enabled: Whether progress reporting is enabled
        json_format: Use JSON format instead of text
        output: Output stream (defaults to sys.stderr)

    Returns:
        ProgressReporter instance
    """
    if not enabled:
        return NullProgressReporter()

    if json_format:
        return JsonProgressReporter(output)
    else:
        return TextProgressReporter(output)


class EventProgressDisplay:
    """Display module events as Rich progress bars.

    This class handles real-time module events (progress, log, data) and
    displays them using Rich progress bars. It's designed to be used as
    an event callback for streaming executor functions.

    Example:
        display = EventProgressDisplay()
        with display:
            result = await execute_local_streaming(
                module_path,
                params,
                event_callback=display.handle_event,
            )

    For multi-host execution:
        display = EventProgressDisplay()
        with display:
            async def run_host(host):
                callback = display.make_callback(host.name)
                return await execute_remote_streaming(
                    host, bundle_path, params, event_callback=callback
                )
            results = await asyncio.gather(*[run_host(h) for h in hosts])
    """

    def __init__(
        self,
        console: Console | None = None,
        show_log_events: bool = True,
        show_data_events: bool = False,
    ) -> None:
        """Initialize event progress display.

        Args:
            console: Rich Console to use (creates new one if None)
            show_log_events: Whether to display log events
            show_data_events: Whether to display data events
        """
        self.console = console or Console(stderr=True)
        self.show_log_events = show_log_events
        self.show_data_events = show_data_events

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        )

        # Map of task_id -> Rich task ID
        self._tasks: dict[str, int] = {}
        # Map of host -> task_id prefix for multi-host tracking
        self._host_prefixes: dict[str, str] = {}
        # Log messages to display
        self._log_messages: list[tuple[str, str, str]] = []  # (level, host, message)
        # Live display context
        self._live: Live | None = None

    def __enter__(self) -> "EventProgressDisplay":
        """Start the progress display."""
        self.progress.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Stop the progress display."""
        self.progress.stop()

        # Print any accumulated log messages
        for level, host, message in self._log_messages:
            style = self._level_style(level)
            prefix = f"[{host}] " if host else ""
            self.console.print(f"{prefix}{message}", style=style)

    def _level_style(self, level: str) -> str:
        """Get Rich style for log level."""
        styles = {
            "debug": "dim",
            "info": "blue",
            "warning": "yellow",
            "error": "red bold",
        }
        return styles.get(level, "")

    def _get_task_key(self, event: dict[str, Any], host: str = "") -> str:
        """Generate a unique task key from event."""
        task_id = event.get("task_id") or "default"
        if host:
            return f"{host}:{task_id}"
        return task_id

    def handle_event(self, event: dict[str, Any], host: str = "") -> None:
        """Handle an incoming module event.

        This is the main callback to pass to streaming executor functions.

        Args:
            event: Event dictionary from module
            host: Optional host name for multi-host tracking
        """
        event_type = event.get("event")

        if event_type == "progress":
            self._handle_progress(event, host)
        elif event_type == "log" and self.show_log_events:
            self._handle_log(event, host)
        elif event_type == "data" and self.show_data_events:
            self._handle_data(event, host)

    def _handle_progress(self, event: dict[str, Any], host: str) -> None:
        """Handle a progress event."""
        task_key = self._get_task_key(event, host)
        percent = event.get("percent", 0)
        message = event.get("message", "Working...")
        current = event.get("current")
        total = event.get("total")

        # Add host prefix to description if multi-host
        description = f"[{host}] {message}" if host else message

        if task_key not in self._tasks:
            # Create new task
            task_total = total if total else 100
            task_id = self.progress.add_task(
                description,
                total=task_total,
                completed=current if current else percent,
            )
            self._tasks[task_key] = task_id
        else:
            # Update existing task
            task_id = self._tasks[task_key]
            completed = current if current else percent
            self.progress.update(
                task_id,
                description=description,
                completed=completed,
            )

            # Update total if provided
            if total:
                self.progress.update(task_id, total=total)

    def _handle_log(self, event: dict[str, Any], host: str) -> None:
        """Handle a log event."""
        level = event.get("level", "info")
        message = event.get("message", "")

        # Store for display after progress completes
        self._log_messages.append((level, host, message))

        # Also print immediately if it's a warning or error
        if level in ("warning", "error"):
            style = self._level_style(level)
            prefix = f"[{host}] " if host else ""
            self.console.print(f"{prefix}{message}", style=style)

    def _handle_data(self, event: dict[str, Any], host: str) -> None:
        """Handle a data event."""
        stream = event.get("stream", "stdout")
        data = event.get("data", "")

        # Print data immediately
        prefix = f"[{host}] " if host else ""
        style = "dim" if stream == "stderr" else ""
        self.console.print(f"{prefix}{data}", style=style, end="")

    def make_callback(self, host: str) -> Callable[[dict[str, Any]], None]:
        """Create a callback bound to a specific host.

        Useful for multi-host execution where each host needs its own callback.

        Args:
            host: Host name to bind to callback

        Returns:
            Callback function that includes host in event handling
        """
        def callback(event: dict[str, Any]) -> None:
            self.handle_event(event, host=host)
        return callback

    def clear_tasks(self) -> None:
        """Clear all tracked tasks."""
        for task_id in self._tasks.values():
            self.progress.remove_task(task_id)
        self._tasks.clear()

    @property
    def task_count(self) -> int:
        """Number of active tasks."""
        return len(self._tasks)


class SimpleEventDisplay:
    """Simple text-based event display without Rich.

    Provides a minimal event display that works without Rich,
    useful for non-interactive environments or when Rich is not available.

    Example:
        display = SimpleEventDisplay()
        result = await execute_local_streaming(
            module_path,
            params,
            event_callback=display.handle_event,
        )
    """

    def __init__(
        self,
        output: Any = None,
        show_log_events: bool = True,
        show_data_events: bool = False,
    ) -> None:
        """Initialize simple event display.

        Args:
            output: Output stream (defaults to sys.stderr)
            show_log_events: Whether to display log events
            show_data_events: Whether to display data events
        """
        self.output = output or sys.stderr
        self.show_log_events = show_log_events
        self.show_data_events = show_data_events
        self._last_percent: dict[str, int] = {}

    def handle_event(self, event: dict[str, Any], host: str = "") -> None:
        """Handle an incoming module event."""
        event_type = event.get("event")

        if event_type == "progress":
            self._handle_progress(event, host)
        elif event_type == "log" and self.show_log_events:
            self._handle_log(event, host)
        elif event_type == "data" and self.show_data_events:
            self._handle_data(event, host)

    def _handle_progress(self, event: dict[str, Any], host: str) -> None:
        """Handle a progress event."""
        task_id = event.get("task_id") or "default"
        key = f"{host}:{task_id}" if host else task_id
        percent = event.get("percent", 0)
        message = event.get("message", "")

        # Only print on significant progress (every 10%)
        last = self._last_percent.get(key, -10)
        if percent >= last + 10 or percent == 100:
            self._last_percent[key] = percent
            prefix = f"[{host}] " if host else ""
            print(f"{prefix}{message}: {percent}%", file=self.output, flush=True)

    def _handle_log(self, event: dict[str, Any], host: str) -> None:
        """Handle a log event."""
        level = event.get("level", "info").upper()
        message = event.get("message", "")
        prefix = f"[{host}] " if host else ""
        print(f"{prefix}[{level}] {message}", file=self.output, flush=True)

    def _handle_data(self, event: dict[str, Any], host: str) -> None:
        """Handle a data event."""
        data = event.get("data", "")
        prefix = f"[{host}] " if host else ""
        print(f"{prefix}{data}", file=self.output, end="", flush=True)

    def make_callback(self, host: str) -> Callable[[dict[str, Any]], None]:
        """Create a callback bound to a specific host."""
        def callback(event: dict[str, Any]) -> None:
            self.handle_event(event, host=host)
        return callback
