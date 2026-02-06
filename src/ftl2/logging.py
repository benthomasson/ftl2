"""Structured logging utilities for FTL2.

This module provides enhanced logging capabilities including:
- Structured logging with context
- Performance timing
- Log scoping with context managers
- Standardized log formats
- File logging with filtering
- Verbosity levels support
"""

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

# Standard log format
DEFAULT_FORMAT = "%(levelname)s [%(name)s] %(message)s"
DEBUG_FORMAT = "%(asctime)s %(levelname)s [%(name)s:%(funcName)s:%(lineno)d] %(message)s"
TRACE_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s [%(name)s:%(funcName)s:%(lineno)d] %(message)s"
PERFORMANCE_FORMAT = "%(levelname)s [%(name)s] %(message)s (%(duration).3fs)"

# Custom TRACE level (more detailed than DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# Verbosity level mapping
VERBOSITY_LEVELS = {
    0: logging.WARNING,   # Default: warnings and errors only
    1: logging.INFO,      # -v: Info level
    2: logging.DEBUG,     # -vv: Debug level
    3: TRACE,             # -vvv: Trace level (includes SSH commands)
}


def get_level_from_verbosity(verbosity: int) -> int:
    """Convert verbosity count to logging level.

    Args:
        verbosity: Number of -v flags (0-3+)

    Returns:
        Logging level constant
    """
    return VERBOSITY_LEVELS.get(min(verbosity, 3), TRACE)


def get_level_from_name(level_name: str) -> int:
    """Convert level name to logging level.

    Args:
        level_name: Level name (trace, debug, info, warning, error, critical)

    Returns:
        Logging level constant

    Raises:
        ValueError: If level name is invalid
    """
    level_map = {
        "trace": TRACE,
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    level_lower = level_name.lower()
    if level_lower not in level_map:
        valid = ", ".join(level_map.keys())
        raise ValueError(f"Invalid log level: {level_name}. Valid levels: {valid}")
    return level_map[level_lower]


def configure_logging(
    level: int = logging.WARNING,
    format_string: str | None = None,
    debug: bool = False,
    log_file: str | Path | None = None,
    file_level: int | None = None,
) -> None:
    """Configure logging for FTL2.

    Args:
        level: Logging level for console (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_string: Custom format string (uses default if None)
        debug: If True, use debug format with timestamps and line numbers
        log_file: Optional path to write logs to file
        file_level: Optional separate level for file logging (defaults to level)

    Example:
        >>> configure_logging(level=logging.INFO)
        >>> configure_logging(debug=True)
        >>> configure_logging(level=logging.INFO, log_file="/tmp/ftl2.log")
        >>> configure_logging(level=logging.WARNING, log_file="/tmp/ftl2.log", file_level=logging.DEBUG)
    """
    # Determine format based on level
    if format_string is None:
        if level <= TRACE:
            format_string = TRACE_FORMAT
        elif debug or level <= logging.DEBUG:
            format_string = DEBUG_FORMAT
        else:
            format_string = DEFAULT_FORMAT

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(min(level, file_level or level))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(format_string))
    root_logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        log_path = Path(log_file) if isinstance(log_file, str) else log_file
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(file_level or level)
        # Always use detailed format for file logging
        file_handler.setFormatter(logging.Formatter(DEBUG_FORMAT))
        root_logger.addHandler(file_handler)


@contextmanager
def log_scope(
    logger: logging.Logger,
    message: str,
    level: int = logging.INFO,
    **context: Any,
) -> Generator[None, None, None]:
    """Context manager for scoped logging.

    Logs entry and exit of a scope with optional context data.

    Args:
        logger: Logger instance to use
        message: Message describing the scope
        level: Log level to use
        **context: Additional context to include in logs

    Example:
        >>> logger = logging.getLogger(__name__)
        >>> with log_scope(logger, "Processing inventory", hosts=10):
        ...     # Do work
        ...     pass
        INFO: Entering: Processing inventory (hosts=10)
        INFO: Exiting: Processing inventory (hosts=10)
    """
    context_str = ", ".join(f"{k}={v}" for k, v in context.items())
    full_message = f"{message} ({context_str})" if context else message

    logger.log(level, f"Entering: {full_message}")
    try:
        yield
    finally:
        logger.log(level, f"Exiting: {full_message}")


@contextmanager
def log_performance(
    logger: logging.Logger,
    operation: str,
    level: int = logging.INFO,
    threshold: float | None = None,
    **context: Any,
) -> Generator[None, None, None]:
    """Context manager for performance logging.

    Times an operation and logs the duration.

    Args:
        logger: Logger instance to use
        operation: Description of the operation being timed
        level: Log level to use
        threshold: Only log if duration exceeds this threshold (seconds)
        **context: Additional context to include in logs

    Example:
        >>> logger = logging.getLogger(__name__)
        >>> with log_performance(logger, "Module execution", hosts=100):
        ...     time.sleep(0.1)
        INFO: Module execution completed in 0.100s (hosts=100)

        >>> # Only log if slow
        >>> with log_performance(logger, "Fast operation", threshold=1.0):
        ...     time.sleep(0.01)
        # No log output (under threshold)
    """
    start_time = time.perf_counter()
    context_str = ", ".join(f"{k}={v}" for k, v in context.items())

    try:
        yield
    finally:
        duration = time.perf_counter() - start_time

        # Only log if threshold not set or exceeded
        if threshold is None or duration >= threshold:
            full_message = f"{operation} completed in {duration:.3f}s"
            if context:
                full_message += f" ({context_str})"
            logger.log(level, full_message)


class StructuredLogger:
    """Logger with structured logging capabilities.

    Wraps a standard Python logger to add structured context to all log messages.

    Attributes:
        logger: Underlying Python logger
        context: Default context dict added to all log messages

    Example:
        >>> logger = StructuredLogger("ftl2.executor")
        >>> logger.add_context(module="ping", hosts=10)
        >>> logger.info("Starting execution")
        INFO [ftl2.executor] Starting execution (module=ping, hosts=10)

        >>> logger.remove_context("hosts")
        >>> logger.warning("Module not found")
        WARNING [ftl2.executor] Module not found (module=ping)
    """

    def __init__(self, name: str, **context: Any) -> None:
        """Initialize structured logger.

        Args:
            name: Logger name (typically __name__)
            **context: Initial context to add to all messages
        """
        self.logger = logging.getLogger(name)
        self.context: dict[str, Any] = context.copy()

    def add_context(self, **context: Any) -> None:
        """Add context that will be included in all future log messages.

        Args:
            **context: Context key-value pairs to add
        """
        self.context.update(context)

    def remove_context(self, *keys: str) -> None:
        """Remove context keys.

        Args:
            *keys: Context keys to remove
        """
        for key in keys:
            self.context.pop(key, None)

    def clear_context(self) -> None:
        """Remove all context."""
        self.context.clear()

    def _format_message(self, message: str, **extra: Any) -> str:
        """Format message with context.

        Args:
            message: Base message
            **extra: Additional context for this message only

        Returns:
            Formatted message with context
        """
        # Merge default context with extra context
        combined = {**self.context, **extra}

        if not combined:
            return message

        context_str = ", ".join(f"{k}={v}" for k, v in combined.items())
        return f"{message} ({context_str})"

    def debug(self, message: str, **extra: Any) -> None:
        """Log debug message with context."""
        self.logger.debug(self._format_message(message, **extra))

    def info(self, message: str, **extra: Any) -> None:
        """Log info message with context."""
        self.logger.info(self._format_message(message, **extra))

    def warning(self, message: str, **extra: Any) -> None:
        """Log warning message with context."""
        self.logger.warning(self._format_message(message, **extra))

    def error(self, message: str, **extra: Any) -> None:
        """Log error message with context."""
        self.logger.error(self._format_message(message, **extra))

    def critical(self, message: str, **extra: Any) -> None:
        """Log critical message with context."""
        self.logger.critical(self._format_message(message, **extra))

    @contextmanager
    def scope(
        self,
        message: str,
        level: int = logging.INFO,
        **context: Any,
    ) -> Generator[None, None, None]:
        """Context manager for scoped logging with structured context.

        Args:
            message: Scope description
            level: Log level
            **context: Additional context for this scope

        Example:
            >>> logger = StructuredLogger("ftl2", module="ping")
            >>> with logger.scope("Execution", hosts=10):
            ...     logger.info("Running")
            INFO: Entering: Execution (module=ping, hosts=10)
            INFO: Running (module=ping)
            INFO: Exiting: Execution (module=ping, hosts=10)
        """
        # Temporarily add scope context
        original_context = self.context.copy()
        self.add_context(**context)

        context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
        full_message = f"{message} ({context_str})" if context_str else message

        self.logger.log(level, f"Entering: {full_message}")
        try:
            yield
        finally:
            self.logger.log(level, f"Exiting: {full_message}")
            # Restore original context
            self.context = original_context

    @contextmanager
    def performance(
        self,
        operation: str,
        level: int = logging.INFO,
        threshold: float | None = None,
        **context: Any,
    ) -> Generator[None, None, None]:
        """Context manager for performance logging with structured context.

        Args:
            operation: Operation description
            level: Log level
            threshold: Only log if duration exceeds threshold (seconds)
            **context: Additional context for this operation

        Example:
            >>> logger = StructuredLogger("ftl2", module="ping")
            >>> with logger.performance("Execution", hosts=100):
            ...     time.sleep(0.1)
            INFO: Execution completed in 0.100s (module=ping, hosts=100)
        """
        start_time = time.perf_counter()

        # Temporarily add operation context
        original_context = self.context.copy()
        self.add_context(**context)

        try:
            yield
        finally:
            duration = time.perf_counter() - start_time

            # Only log if threshold not set or exceeded
            if threshold is None or duration >= threshold:
                context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
                full_message = f"{operation} completed in {duration:.3f}s"
                if context_str:
                    full_message += f" ({context_str})"
                self.logger.log(level, full_message)

            # Restore original context
            self.context = original_context


def get_logger(name: str, **context: Any) -> StructuredLogger:
    """Get a structured logger.

    Convenience function to create a StructuredLogger instance.

    Args:
        name: Logger name (typically __name__)
        **context: Initial context

    Returns:
        StructuredLogger instance

    Example:
        >>> logger = get_logger(__name__, component="executor")
        >>> logger.info("Starting")
        INFO [__main__] Starting (component=executor)
    """
    return StructuredLogger(name, **context)
