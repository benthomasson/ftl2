"""Tests for progress reporting and event display."""

import io
from unittest.mock import MagicMock

import pytest

from ftl2.progress import (
    EventProgressDisplay,
    SimpleEventDisplay,
    TextProgressReporter,
    JsonProgressReporter,
    NullProgressReporter,
    create_progress_reporter,
)


class TestSimpleEventDisplay:
    """Tests for SimpleEventDisplay."""

    def test_handle_progress_event(self):
        """Test handling progress events."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output)

        display.handle_event({"event": "progress", "percent": 0, "message": "Starting"})
        display.handle_event({"event": "progress", "percent": 50, "message": "Halfway"})
        display.handle_event({"event": "progress", "percent": 100, "message": "Done"})

        result = output.getvalue()
        assert "Starting: 0%" in result
        assert "Halfway: 50%" in result
        assert "Done: 100%" in result

    def test_handle_log_event(self):
        """Test handling log events."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output, show_log_events=True)

        display.handle_event({"event": "log", "level": "info", "message": "Info message"})
        display.handle_event({"event": "log", "level": "warning", "message": "Warning!"})

        result = output.getvalue()
        assert "[INFO] Info message" in result
        assert "[WARNING] Warning!" in result

    def test_handle_log_event_disabled(self):
        """Test log events are hidden when disabled."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output, show_log_events=False)

        display.handle_event({"event": "log", "level": "info", "message": "Hidden"})

        assert output.getvalue() == ""

    def test_handle_data_event(self):
        """Test handling data events."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output, show_data_events=True)

        display.handle_event({"event": "data", "stream": "stdout", "data": "output line"})

        assert "output line" in output.getvalue()

    def test_handle_data_event_disabled(self):
        """Test data events are hidden when disabled."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output, show_data_events=False)

        display.handle_event({"event": "data", "stream": "stdout", "data": "hidden"})

        assert output.getvalue() == ""

    def test_make_callback_with_host(self):
        """Test creating callback bound to host."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output)

        callback = display.make_callback("server1")
        callback({"event": "progress", "percent": 100, "message": "Done"})

        assert "[server1]" in output.getvalue()

    def test_progress_throttling(self):
        """Test that progress is throttled to significant changes."""
        output = io.StringIO()
        display = SimpleEventDisplay(output=output)

        # Send many small progress updates
        for i in range(100):
            display.handle_event({"event": "progress", "percent": i, "message": "Working"})

        # Should only print every 10% (0, 10, 20, ..., 100)
        lines = output.getvalue().strip().split("\n")
        assert len(lines) <= 11  # At most 11 lines (0, 10, 20, ..., 100)


class TestEventProgressDisplay:
    """Tests for EventProgressDisplay (Rich-based)."""

    def test_context_manager(self):
        """Test context manager protocol."""
        display = EventProgressDisplay()

        with display:
            assert display.progress is not None

    def test_handle_progress_creates_task(self):
        """Test that progress events create tasks."""
        display = EventProgressDisplay()

        with display:
            display.handle_event({"event": "progress", "percent": 50, "message": "Working"})
            assert display.task_count == 1

    def test_handle_multiple_tasks(self):
        """Test handling events with different task IDs."""
        display = EventProgressDisplay()

        with display:
            display.handle_event({"event": "progress", "percent": 50, "task_id": "task1"})
            display.handle_event({"event": "progress", "percent": 25, "task_id": "task2"})
            assert display.task_count == 2

    def test_handle_log_stores_messages(self):
        """Test that log events are stored."""
        display = EventProgressDisplay(show_log_events=True)

        with display:
            display.handle_event({"event": "log", "level": "info", "message": "Test"})

        assert len(display._log_messages) == 1
        assert display._log_messages[0] == ("info", "", "Test")

    def test_make_callback_with_host(self):
        """Test creating callback bound to host."""
        display = EventProgressDisplay()

        callback = display.make_callback("server1")

        with display:
            callback({"event": "progress", "percent": 100, "message": "Done"})
            assert display.task_count == 1
            # Host should be in the task key
            assert "server1:default" in display._tasks

    def test_clear_tasks(self):
        """Test clearing tasks."""
        display = EventProgressDisplay()

        with display:
            display.handle_event({"event": "progress", "percent": 50})
            assert display.task_count == 1
            display.clear_tasks()
            assert display.task_count == 0


class TestProgressReporters:
    """Tests for host-level progress reporters."""

    def test_text_reporter_execution_start(self):
        """Test TextProgressReporter execution start."""
        output = io.StringIO()
        reporter = TextProgressReporter(output=output)

        reporter.on_execution_start(5, "copy")

        assert "Executing module 'copy' on 5 host(s)" in output.getvalue()

    def test_text_reporter_host_complete_success(self):
        """Test TextProgressReporter host complete (success)."""
        output = io.StringIO()
        reporter = TextProgressReporter(output=output)
        reporter.total = 3
        reporter.completed = 0

        reporter.on_host_complete("server1", success=True, changed=True, duration=1.5)

        result = output.getvalue()
        assert "✓" in result
        assert "server1" in result
        assert "(changed)" in result
        assert "1.50s" in result

    def test_text_reporter_host_complete_failure(self):
        """Test TextProgressReporter host complete (failure)."""
        output = io.StringIO()
        reporter = TextProgressReporter(output=output)
        reporter.total = 3
        reporter.completed = 0

        reporter.on_host_complete("server1", success=False, changed=False, duration=0.5, error="Connection refused")

        result = output.getvalue()
        assert "✗" in result
        assert "FAILED" in result
        assert "Connection refused" in result

    def test_json_reporter_emits_json(self):
        """Test JsonProgressReporter emits valid JSON."""
        import json
        output = io.StringIO()
        reporter = JsonProgressReporter(output=output)

        reporter.on_execution_start(3, "ping")

        line = output.getvalue().strip()
        event = json.loads(line)
        assert event["event"] == "execution_start"
        assert event["total_hosts"] == 3
        assert event["module"] == "ping"

    def test_null_reporter_does_nothing(self):
        """Test NullProgressReporter does nothing."""
        reporter = NullProgressReporter()

        # Should not raise
        reporter.on_execution_start(5, "copy")
        reporter.on_host_start("server1")
        reporter.on_host_complete("server1", True, True, 1.0)
        reporter.on_host_retry("server1", 1, 3, "error", 5.0)
        reporter.on_execution_complete(5, 4, 1, 10.0)

    def test_create_progress_reporter_disabled(self):
        """Test create_progress_reporter with enabled=False."""
        reporter = create_progress_reporter(enabled=False)
        assert isinstance(reporter, NullProgressReporter)

    def test_create_progress_reporter_text(self):
        """Test create_progress_reporter with text format."""
        reporter = create_progress_reporter(enabled=True, json_format=False)
        assert isinstance(reporter, TextProgressReporter)

    def test_create_progress_reporter_json(self):
        """Test create_progress_reporter with JSON format."""
        reporter = create_progress_reporter(enabled=True, json_format=True)
        assert isinstance(reporter, JsonProgressReporter)
