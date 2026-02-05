"""Retry logic with smart error classification for FTL2.

Provides automatic retry handling with exponential backoff,
error classification (transient vs permanent), and circuit breaker
protection for resilient automation.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .exceptions import ErrorTypes

logger = logging.getLogger(__name__)


# Error classification: which errors are transient (worth retrying)
TRANSIENT_ERRORS = {
    ErrorTypes.CONNECTION_TIMEOUT,
    ErrorTypes.CONNECTION_REFUSED,
    ErrorTypes.HOST_UNREACHABLE,
    ErrorTypes.GATE_ERROR,  # Gate upload/execution issues may be transient
}

PERMANENT_ERRORS = {
    ErrorTypes.AUTHENTICATION_FAILED,
    ErrorTypes.PERMISSION_DENIED,
    ErrorTypes.MODULE_NOT_FOUND,
    ErrorTypes.INVENTORY_ERROR,
}

# MODULE_EXECUTION_ERROR and MODULE_TIMEOUT could go either way
# We'll treat them as potentially transient by default
MAYBE_TRANSIENT_ERRORS = {
    ErrorTypes.MODULE_EXECUTION_ERROR,
    ErrorTypes.MODULE_TIMEOUT,
    ErrorTypes.UNKNOWN,
}


def is_transient_error(error_type: str) -> bool:
    """Check if an error type is transient (worth retrying).

    Args:
        error_type: The error type classification

    Returns:
        True if the error is transient and should be retried
    """
    return error_type in TRANSIENT_ERRORS


def is_permanent_error(error_type: str) -> bool:
    """Check if an error type is permanent (should not retry).

    Args:
        error_type: The error type classification

    Returns:
        True if the error is permanent and should not be retried
    """
    return error_type in PERMANENT_ERRORS


def should_retry(error_type: str, smart_retry: bool = True) -> bool:
    """Determine if an error should be retried.

    Args:
        error_type: The error type classification
        smart_retry: If True, only retry transient errors. If False, retry all.

    Returns:
        True if the error should be retried
    """
    if not smart_retry:
        # Retry everything except module not found and inventory errors
        return error_type not in {ErrorTypes.MODULE_NOT_FOUND, ErrorTypes.INVENTORY_ERROR}

    # Smart retry: only retry transient errors
    if error_type in PERMANENT_ERRORS:
        return False
    if error_type in TRANSIENT_ERRORS:
        return True
    # For maybe-transient errors, default to retry
    return error_type in MAYBE_TRANSIENT_ERRORS


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_attempts: Maximum number of retry attempts (0 = no retries)
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries (for backoff cap)
        backoff_factor: Multiplier for exponential backoff (e.g., 2.0)
        smart_retry: Only retry transient errors (vs retry all)
        retry_on: Specific error types to retry (overrides smart_retry)
    """

    max_attempts: int = 0  # 0 = no retries, 1 = one retry, etc.
    initial_delay: float = 5.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    smart_retry: bool = True
    retry_on: set[str] = field(default_factory=set)

    def should_retry_error(self, error_type: str) -> bool:
        """Check if this error type should be retried based on config.

        Args:
            error_type: The error type classification

        Returns:
            True if the error should be retried
        """
        # If specific retry_on types are set, use those
        if self.retry_on:
            return error_type in self.retry_on

        # Otherwise use smart_retry logic
        return should_retry(error_type, self.smart_retry)

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for a given attempt number.

        Uses exponential backoff with jitter.

        Args:
            attempt: Current attempt number (1-based)

        Returns:
            Delay in seconds before next retry
        """
        if attempt <= 1:
            return self.initial_delay

        # Exponential backoff
        delay = self.initial_delay * (self.backoff_factor ** (attempt - 1))

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add small jitter (±10%) to prevent thundering herd
        import random
        jitter = delay * 0.1 * (random.random() * 2 - 1)
        delay += jitter

        return max(0, delay)


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker protection.

    Attributes:
        enabled: Whether circuit breaker is enabled
        threshold_percent: Failure percentage that triggers the breaker
        min_hosts: Minimum hosts before circuit breaker can trigger
    """

    enabled: bool = False
    threshold_percent: float = 30.0  # Stop if 30%+ hosts failing
    min_hosts: int = 5  # Need at least 5 hosts before triggering


@dataclass
class RetryState:
    """Tracks retry state for a single host.

    Attributes:
        host_name: Name of the host
        attempts: Number of attempts made
        last_error_type: Last error type encountered
        last_error_message: Last error message
        succeeded: Whether execution eventually succeeded
        gave_up: Whether we gave up retrying
    """

    host_name: str
    attempts: int = 0
    last_error_type: str = ""
    last_error_message: str = ""
    succeeded: bool = False
    gave_up: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "host_name": self.host_name,
            "attempts": self.attempts,
            "last_error_type": self.last_error_type,
            "last_error_message": self.last_error_message,
            "succeeded": self.succeeded,
            "gave_up": self.gave_up,
        }


@dataclass
class RetryStats:
    """Statistics about retry behavior across all hosts.

    Attributes:
        total_hosts: Total number of hosts
        succeeded_first_try: Hosts that succeeded on first attempt
        succeeded_after_retry: Hosts that succeeded after retrying
        failed_permanent: Hosts that failed with permanent errors
        failed_after_retries: Hosts that failed after exhausting retries
        circuit_breaker_triggered: Whether circuit breaker stopped execution
        host_states: Per-host retry state
    """

    total_hosts: int = 0
    succeeded_first_try: int = 0
    succeeded_after_retry: int = 0
    failed_permanent: int = 0
    failed_after_retries: int = 0
    circuit_breaker_triggered: bool = False
    host_states: dict[str, RetryState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "total_hosts": self.total_hosts,
            "succeeded_first_try": self.succeeded_first_try,
            "succeeded_after_retry": self.succeeded_after_retry,
            "failed_permanent": self.failed_permanent,
            "failed_after_retries": self.failed_after_retries,
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "total_retries": sum(
                max(0, s.attempts - 1) for s in self.host_states.values()
            ),
        }

    def format_text(self) -> str:
        """Format as human-readable text."""
        lines = ["", "Retry Statistics:"]

        if self.succeeded_first_try:
            lines.append(f"  Succeeded (1st try): {self.succeeded_first_try}")
        if self.succeeded_after_retry:
            lines.append(f"  Succeeded (after retry): {self.succeeded_after_retry}")
        if self.failed_permanent:
            lines.append(f"  Failed (permanent error): {self.failed_permanent}")
        if self.failed_after_retries:
            lines.append(f"  Failed (exhausted retries): {self.failed_after_retries}")
        if self.circuit_breaker_triggered:
            lines.append("  Circuit breaker: TRIGGERED")

        # Show hosts that needed retries
        retried_hosts = [
            s for s in self.host_states.values()
            if s.attempts > 1
        ]
        if retried_hosts:
            lines.append("")
            lines.append("  Hosts that required retries:")
            for state in retried_hosts:
                status = "succeeded" if state.succeeded else "failed"
                lines.append(f"    {state.host_name}: {state.attempts} attempts ({status})")

        return "\n".join(lines)


def check_circuit_breaker(
    total_hosts: int,
    failed_hosts: int,
    config: CircuitBreakerConfig,
) -> bool:
    """Check if circuit breaker should trigger.

    Args:
        total_hosts: Total number of hosts
        failed_hosts: Number of failed hosts
        config: Circuit breaker configuration

    Returns:
        True if circuit breaker should trigger (stop execution)
    """
    if not config.enabled:
        return False

    if total_hosts < config.min_hosts:
        return False

    failure_percent = (failed_hosts / total_hosts) * 100
    return failure_percent >= config.threshold_percent


async def retry_with_backoff(
    coro_factory,
    config: RetryConfig,
    host_name: str = "",
) -> tuple[Any, RetryState]:
    """Execute a coroutine with retry logic.

    Args:
        coro_factory: Callable that returns a coroutine to execute
        config: Retry configuration
        host_name: Host name for logging/tracking

    Returns:
        Tuple of (result, retry_state)
    """
    state = RetryState(host_name=host_name)
    max_total_attempts = config.max_attempts + 1  # +1 for initial attempt

    for attempt in range(1, max_total_attempts + 1):
        state.attempts = attempt

        try:
            result = await coro_factory()

            # Check if result indicates failure (ModuleResult with success=False)
            if hasattr(result, 'success') and not result.success:
                # Extract error type if available
                error_type = ErrorTypes.UNKNOWN
                if hasattr(result, 'error_context') and result.error_context:
                    error_type = result.error_context.error_type
                elif hasattr(result, 'error') and result.error:
                    # Try to classify from error message
                    error_type = _classify_error_message(result.error)

                state.last_error_type = error_type
                state.last_error_message = getattr(result, 'error', '') or ''

                # Check if we should retry
                if attempt < max_total_attempts and config.should_retry_error(error_type):
                    delay = config.get_delay(attempt)
                    logger.info(
                        f"Retry {attempt}/{config.max_attempts} for {host_name}: "
                        f"{error_type} - waiting {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    # No more retries or permanent error
                    state.gave_up = attempt >= max_total_attempts
                    return result, state
            else:
                # Success
                state.succeeded = True
                return result, state

        except Exception as e:
            error_type = _classify_exception(e)
            state.last_error_type = error_type
            state.last_error_message = str(e)

            if attempt < max_total_attempts and config.should_retry_error(error_type):
                delay = config.get_delay(attempt)
                logger.info(
                    f"Retry {attempt}/{config.max_attempts} for {host_name}: "
                    f"{error_type} - waiting {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue
            else:
                state.gave_up = attempt >= max_total_attempts
                raise

    # Should not reach here, but just in case
    state.gave_up = True
    return None, state


def _classify_error_message(error: str) -> str:
    """Classify error type from error message.

    Args:
        error: Error message string

    Returns:
        Error type classification
    """
    error_lower = error.lower()

    if "timeout" in error_lower:
        return ErrorTypes.CONNECTION_TIMEOUT
    if "connection refused" in error_lower:
        return ErrorTypes.CONNECTION_REFUSED
    if "authentication" in error_lower or "auth" in error_lower:
        return ErrorTypes.AUTHENTICATION_FAILED
    if "permission denied" in error_lower:
        return ErrorTypes.PERMISSION_DENIED
    if "unreachable" in error_lower or "no route" in error_lower:
        return ErrorTypes.HOST_UNREACHABLE
    if "module" in error_lower and "not found" in error_lower:
        return ErrorTypes.MODULE_NOT_FOUND

    return ErrorTypes.UNKNOWN


def _classify_exception(exc: Exception) -> str:
    """Classify error type from exception.

    Args:
        exc: Exception instance

    Returns:
        Error type classification
    """
    # Check exception type name
    exc_name = type(exc).__name__.lower()

    if "timeout" in exc_name:
        return ErrorTypes.CONNECTION_TIMEOUT
    if "connection" in exc_name:
        return ErrorTypes.CONNECTION_REFUSED
    if "auth" in exc_name:
        return ErrorTypes.AUTHENTICATION_FAILED
    if "permission" in exc_name:
        return ErrorTypes.PERMISSION_DENIED

    # Fall back to message classification
    return _classify_error_message(str(exc))


def format_retry_summary(stats: RetryStats) -> str:
    """Format retry statistics as a summary string.

    Args:
        stats: Retry statistics

    Returns:
        Formatted summary string
    """
    parts = []

    if stats.succeeded_after_retry > 0:
        parts.append(f"{stats.succeeded_after_retry} succeeded after retry")

    if stats.failed_after_retries > 0:
        parts.append(f"{stats.failed_after_retries} failed after retries")

    if stats.circuit_breaker_triggered:
        parts.append("circuit breaker triggered")

    if parts:
        return "Retry summary: " + ", ".join(parts)
    return ""
