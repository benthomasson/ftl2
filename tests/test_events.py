"""Tests for ftl2.events module."""

import json

from ftl2.events import (
    DataEvent,
    LogEvent,
    ModuleEvent,
    ProgressEvent,
    emit_data,
    emit_log,
    emit_progress,
    parse_event,
    parse_events,
)


class TestModuleEvent:
    """Tests for base ModuleEvent class."""

    def test_creates_with_timestamp(self):
        event = ModuleEvent(event="test")
        assert event.event == "test"
        assert event.timestamp > 0

    def test_to_dict(self):
        event = ModuleEvent(event="test", timestamp=1234567890.0)
        d = event.to_dict()
        assert d == {"event": "test", "timestamp": 1234567890.0}

    def test_to_json(self):
        event = ModuleEvent(event="test", timestamp=1234567890.0)
        j = event.to_json()
        assert json.loads(j) == {"event": "test", "timestamp": 1234567890.0}

    def test_emit_writes_to_stderr(self, capsys):
        event = ModuleEvent(event="test", timestamp=1234567890.0)
        event.emit()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "test" in captured.err
        assert json.loads(captured.err.strip()) == {"event": "test", "timestamp": 1234567890.0}


class TestProgressEvent:
    """Tests for ProgressEvent class."""

    def test_default_values(self):
        event = ProgressEvent()
        assert event.event == "progress"
        assert event.percent == 0
        assert event.message == ""
        assert event.current is None
        assert event.total is None
        assert event.task_id is None

    def test_with_values(self):
        event = ProgressEvent(
            percent=50,
            message="Halfway",
            current=500,
            total=1000,
            task_id="task1",
        )
        d = event.to_dict()
        assert d["event"] == "progress"
        assert d["percent"] == 50
        assert d["message"] == "Halfway"
        assert d["current"] == 500
        assert d["total"] == 1000
        assert d["task_id"] == "task1"


class TestLogEvent:
    """Tests for LogEvent class."""

    def test_default_values(self):
        event = LogEvent()
        assert event.event == "log"
        assert event.level == "info"
        assert event.message == ""

    def test_with_values(self):
        event = LogEvent(level="warning", message="Something happened")
        d = event.to_dict()
        assert d["event"] == "log"
        assert d["level"] == "warning"
        assert d["message"] == "Something happened"


class TestDataEvent:
    """Tests for DataEvent class."""

    def test_default_values(self):
        event = DataEvent()
        assert event.event == "data"
        assert event.stream == "stdout"
        assert event.data == ""

    def test_with_values(self):
        event = DataEvent(stream="stderr", data="error output")
        d = event.to_dict()
        assert d["event"] == "data"
        assert d["stream"] == "stderr"
        assert d["data"] == "error output"


class TestEmitFunctions:
    """Tests for convenience emit functions."""

    def test_emit_progress(self, capsys):
        emit_progress(75, "Almost done", current=750, total=1000)
        captured = capsys.readouterr()
        event = json.loads(captured.err.strip())
        assert event["event"] == "progress"
        assert event["percent"] == 75
        assert event["message"] == "Almost done"
        assert event["current"] == 750
        assert event["total"] == 1000

    def test_emit_log(self, capsys):
        emit_log("Test message", level="error")
        captured = capsys.readouterr()
        event = json.loads(captured.err.strip())
        assert event["event"] == "log"
        assert event["level"] == "error"
        assert event["message"] == "Test message"

    def test_emit_data(self, capsys):
        emit_data("output line\n", stream="stdout")
        captured = capsys.readouterr()
        event = json.loads(captured.err.strip())
        assert event["event"] == "data"
        assert event["stream"] == "stdout"
        assert event["data"] == "output line\n"


class TestParseFunctions:
    """Tests for event parsing functions."""

    def test_parse_event_valid(self):
        line = '{"event": "progress", "percent": 50}'
        event = parse_event(line)
        assert event == {"event": "progress", "percent": 50}

    def test_parse_event_invalid_json(self):
        line = "not json"
        event = parse_event(line)
        assert event is None

    def test_parse_event_no_event_field(self):
        line = '{"foo": "bar"}'
        event = parse_event(line)
        assert event is None

    def test_parse_event_empty(self):
        event = parse_event("")
        assert event is None

    def test_parse_event_whitespace(self):
        line = '  {"event": "log", "message": "test"}  '
        event = parse_event(line)
        assert event == {"event": "log", "message": "test"}

    def test_parse_events_mixed(self):
        stderr = '''{"event": "progress", "percent": 0}
Some warning text
{"event": "progress", "percent": 50}
Another line
{"event": "progress", "percent": 100}'''

        events, other = parse_events(stderr)

        assert len(events) == 3
        assert events[0]["percent"] == 0
        assert events[1]["percent"] == 50
        assert events[2]["percent"] == 100

        assert "Some warning text" in other
        assert "Another line" in other
        assert "progress" not in other

    def test_parse_events_empty(self):
        events, other = parse_events("")
        assert events == []
        assert other == ""

    def test_parse_events_no_events(self):
        stderr = "Just some\nregular output"
        events, other = parse_events(stderr)
        assert events == []
        assert other == "Just some\nregular output"

    def test_parse_events_only_events(self):
        stderr = '''{"event": "log", "message": "one"}
{"event": "log", "message": "two"}'''
        events, other = parse_events(stderr)
        assert len(events) == 2
        assert other == ""
