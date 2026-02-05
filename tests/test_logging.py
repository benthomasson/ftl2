"""Tests for logging utilities."""

import logging
import time
from io import StringIO

import pytest

from ftl2.logging import (
    StructuredLogger,
    configure_logging,
    get_logger,
    log_performance,
    log_scope,
)


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def test_configure_default(self):
        """Test default logging configuration."""
        configure_logging()
        logger = logging.getLogger("test")
        assert logger.level == logging.WARNING or logging.root.level == logging.WARNING

    def test_configure_debug(self):
        """Test debug logging configuration."""
        configure_logging(debug=True)
        # Just verify it doesn't crash
        logger = logging.getLogger("test")
        assert logger is not None

    def test_configure_custom_level(self):
        """Test custom log level."""
        configure_logging(level=logging.DEBUG)
        # Verify configuration applied
        assert logging.root.level == logging.DEBUG

    def test_configure_custom_format(self):
        """Test custom format string."""
        configure_logging(format_string="%(message)s")
        # Just verify it doesn't crash
        logger = logging.getLogger("test")
        assert logger is not None


class TestLogScope:
    """Tests for log_scope context manager."""

    def test_log_scope_basic(self, caplog):
        """Test basic log scope."""
        logger = logging.getLogger("test.scope")
        logger.setLevel(logging.INFO)

        with log_scope(logger, "Test operation"):
            pass

        assert "Entering: Test operation" in caplog.text
        assert "Exiting: Test operation" in caplog.text

    def test_log_scope_with_context(self, caplog):
        """Test log scope with context."""
        logger = logging.getLogger("test.scope.context")
        logger.setLevel(logging.INFO)

        with log_scope(logger, "Processing", items=10, batch=2):
            pass

        assert "Entering: Processing (items=10, batch=2)" in caplog.text
        assert "Exiting: Processing (items=10, batch=2)" in caplog.text

    def test_log_scope_exception(self, caplog):
        """Test log scope with exception."""
        logger = logging.getLogger("test.scope.exception")
        logger.setLevel(logging.INFO)

        with pytest.raises(ValueError):
            with log_scope(logger, "Failing operation"):
                raise ValueError("test error")

        # Should still log exit even on exception
        assert "Entering: Failing operation" in caplog.text
        assert "Exiting: Failing operation" in caplog.text

    def test_log_scope_custom_level(self, caplog):
        """Test log scope with custom level."""
        logger = logging.getLogger("test.scope.level")
        logger.setLevel(logging.DEBUG)

        with log_scope(logger, "Debug operation", level=logging.DEBUG):
            pass

        assert "Entering: Debug operation" in caplog.text


class TestLogPerformance:
    """Tests for log_performance context manager."""

    def test_log_performance_basic(self, caplog):
        """Test basic performance logging."""
        logger = logging.getLogger("test.perf")
        logger.setLevel(logging.INFO)

        with log_performance(logger, "Test operation"):
            time.sleep(0.01)

        assert "Test operation completed" in caplog.text
        assert "0." in caplog.text  # Contains duration

    def test_log_performance_with_context(self, caplog):
        """Test performance logging with context."""
        logger = logging.getLogger("test.perf.context")
        logger.setLevel(logging.INFO)

        with log_performance(logger, "Processing", items=100):
            time.sleep(0.01)

        assert "Processing completed" in caplog.text
        assert "items=100" in caplog.text

    def test_log_performance_threshold(self, caplog):
        """Test performance logging with threshold."""
        logger = logging.getLogger("test.perf.threshold")
        logger.setLevel(logging.INFO)

        # Fast operation - should not log
        with log_performance(logger, "Fast operation", threshold=1.0):
            time.sleep(0.01)

        assert "Fast operation" not in caplog.text

        # Slow operation - should log
        with log_performance(logger, "Slow operation", threshold=0.001):
            time.sleep(0.01)

        assert "Slow operation completed" in caplog.text

    def test_log_performance_exception(self, caplog):
        """Test performance logging with exception."""
        logger = logging.getLogger("test.perf.exception")
        logger.setLevel(logging.INFO)

        with pytest.raises(ValueError):
            with log_performance(logger, "Failing operation"):
                raise ValueError("test error")

        # Should still log duration even on exception
        assert "Failing operation completed" in caplog.text


class TestStructuredLogger:
    """Tests for StructuredLogger class."""

    def test_create_logger(self):
        """Test creating a structured logger."""
        logger = StructuredLogger("test.structured")
        assert logger.logger.name == "test.structured"
        assert logger.context == {}

    def test_create_logger_with_context(self):
        """Test creating logger with initial context."""
        logger = StructuredLogger("test", module="ping", version="1.0")
        assert logger.context == {"module": "ping", "version": "1.0"}

    def test_add_context(self):
        """Test adding context."""
        logger = StructuredLogger("test")
        logger.add_context(host="localhost", port=22)
        assert logger.context == {"host": "localhost", "port": 22}

    def test_remove_context(self):
        """Test removing context."""
        logger = StructuredLogger("test", key1="val1", key2="val2")
        logger.remove_context("key1")
        assert logger.context == {"key2": "val2"}

    def test_remove_nonexistent_context(self):
        """Test removing nonexistent context key."""
        logger = StructuredLogger("test", key1="val1")
        logger.remove_context("nonexistent")  # Should not raise
        assert logger.context == {"key1": "val1"}

    def test_clear_context(self):
        """Test clearing all context."""
        logger = StructuredLogger("test", key1="val1", key2="val2")
        logger.clear_context()
        assert logger.context == {}

    def test_log_with_context(self, caplog):
        """Test logging with default context."""
        logger = StructuredLogger("test.context")
        logger.logger.setLevel(logging.INFO)
        logger.add_context(module="ping", hosts=10)

        logger.info("Starting execution")

        assert "Starting execution (module=ping, hosts=10)" in caplog.text

    def test_log_with_extra_context(self, caplog):
        """Test logging with extra context."""
        logger = StructuredLogger("test.extra")
        logger.logger.setLevel(logging.INFO)
        logger.add_context(module="ping")

        logger.info("Processing host", host="web01")

        assert "Processing host (module=ping, host=web01)" in caplog.text

    def test_log_without_context(self, caplog):
        """Test logging without any context."""
        logger = StructuredLogger("test.nocontext")
        logger.logger.setLevel(logging.INFO)

        logger.info("Simple message")

        assert "Simple message" in caplog.text
        assert "(" not in caplog.text  # No context parentheses

    def test_log_levels(self, caplog):
        """Test different log levels."""
        logger = StructuredLogger("test.levels")
        logger.logger.setLevel(logging.DEBUG)

        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")
        logger.error("Error message")
        logger.critical("Critical message")

        assert "Debug message" in caplog.text
        assert "Info message" in caplog.text
        assert "Warning message" in caplog.text
        assert "Error message" in caplog.text
        assert "Critical message" in caplog.text

    def test_scope_context_manager(self, caplog):
        """Test scope context manager."""
        logger = StructuredLogger("test.scope")
        logger.logger.setLevel(logging.INFO)
        logger.add_context(module="ping")

        with logger.scope("Execution", hosts=10):
            logger.info("Running")

        assert "Entering: Execution (module=ping, hosts=10)" in caplog.text
        assert "Running (module=ping" in caplog.text
        assert "Exiting: Execution (module=ping, hosts=10)" in caplog.text

    def test_scope_context_isolation(self, caplog):
        """Test that scope context is isolated."""
        logger = StructuredLogger("test.isolation")
        logger.logger.setLevel(logging.INFO)
        logger.add_context(module="ping")

        with logger.scope("Execution", hosts=10):
            assert logger.context == {"module": "ping", "hosts": 10}

        # After scope, hosts should be removed
        assert logger.context == {"module": "ping"}

    def test_performance_context_manager(self, caplog):
        """Test performance context manager."""
        logger = StructuredLogger("test.perf")
        logger.logger.setLevel(logging.INFO)
        logger.add_context(module="ping")

        with logger.performance("Execution", hosts=100):
            time.sleep(0.01)

        assert "Execution completed" in caplog.text
        assert "module=ping, hosts=100" in caplog.text

    def test_performance_threshold(self, caplog):
        """Test performance threshold."""
        logger = StructuredLogger("test.threshold")
        logger.logger.setLevel(logging.INFO)

        # Fast - should not log
        with logger.performance("Fast", threshold=1.0):
            time.sleep(0.01)

        assert "Fast" not in caplog.text

        # Slow - should log
        with logger.performance("Slow", threshold=0.001):
            time.sleep(0.01)

        assert "Slow completed" in caplog.text


class TestGetLogger:
    """Tests for get_logger convenience function."""

    def test_get_logger_basic(self):
        """Test getting a basic logger."""
        logger = get_logger("test.get")
        assert isinstance(logger, StructuredLogger)
        assert logger.logger.name == "test.get"

    def test_get_logger_with_context(self):
        """Test getting logger with context."""
        logger = get_logger("test.get.context", component="executor")
        assert logger.context == {"component": "executor"}

    def test_get_logger_usage(self, caplog):
        """Test using logger from get_logger."""
        logger = get_logger("test.usage", app="ftl2")
        logger.logger.setLevel(logging.INFO)

        logger.info("Test message")

        assert "Test message (app=ftl2)" in caplog.text
