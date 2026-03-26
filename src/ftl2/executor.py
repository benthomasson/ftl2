"""Module execution orchestration for FTL2.

This module provides the core orchestration logic for running modules
across inventories of hosts with concurrent execution, chunking for
optimal performance, result aggregation, retry logic, and progress reporting.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .exceptions import ErrorTypes
from .inventory import Inventory
from .progress import ProgressReporter, NullProgressReporter
from .retry import (
    RetryConfig,
    CircuitBreakerConfig,
    RetryStats,
    RetryState,
    check_circuit_breaker,
)
from .runners import ExecutionContext, ModuleRunnerFactory
from .types import HostConfig, ModuleResult
from .utils import chunk

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResults:
    """Results from executing a module across multiple hosts.

    Attributes:
        results: Dictionary mapping host names to their execution results
        total_hosts: Total number of hosts executed against
        successful: Number of successful executions
        failed: Number of failed executions
        retry_stats: Statistics about retry behavior (if retries enabled)

    Example:
        >>> results = ExecutionResults(
        ...     results={"host1": ModuleResult(...), "host2": ModuleResult(...)},
        ...     total_hosts=2,
        ...     successful=2,
        ...     failed=0
        ... )
    """

    results: dict[str, ModuleResult] = field(default_factory=dict)
    total_hosts: int = 0
    successful: int = 0
    failed: int = 0
    retry_stats: RetryStats | None = None

    def __post_init__(self) -> None:
        """Calculate statistics from results."""
        if not self.results:
            return

        self.total_hosts = len(self.results)
        self.successful = sum(1 for r in self.results.values() if r.success)
        self.failed = self.total_hosts - self.successful

    def is_success(self) -> bool:
        """Check if all executions succeeded."""
        return self.failed == 0


class ModuleExecutor:
    """Orchestrates module execution across inventories of hosts.

    Manages concurrent execution with chunking, result aggregation,
    retry logic, progress reporting, and proper cleanup of resources.

    Attributes:
        runner_factory: Factory for creating module runners
        chunk_size: Number of hosts to process concurrently
        retry_config: Configuration for retry behavior
        circuit_breaker_config: Configuration for circuit breaker
        progress_reporter: Reporter for progress events

    Example:
        >>> executor = ModuleExecutor()
        >>> context = ExecutionContext(
        ...     execution_config=ExecutionConfig(module_name="ping"),
        ...     gate_config=GateConfig()
        ... )
        >>> results = await executor.run(inventory, context)
        >>> print(f"Success: {results.successful}/{results.total_hosts}")
    """

    def __init__(
        self,
        chunk_size: int = 10,
        retry_config: RetryConfig | None = None,
        circuit_breaker_config: CircuitBreakerConfig | None = None,
        progress_reporter: ProgressReporter | None = None,
    ) -> None:
        """Initialize the executor.

        Args:
            chunk_size: Number of hosts to process concurrently (default: 10)
            retry_config: Configuration for retry behavior
            circuit_breaker_config: Configuration for circuit breaker
            progress_reporter: Reporter for progress events
        """
        self.runner_factory = ModuleRunnerFactory()
        self.chunk_size = chunk_size
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker_config = circuit_breaker_config or CircuitBreakerConfig()
        self.progress_reporter = progress_reporter or NullProgressReporter()

    async def run(
        self,
        inventory: Inventory,
        context: ExecutionContext,
    ) -> ExecutionResults:
        """Execute a module across all hosts in the inventory.

        Supports automatic retries for failed hosts, circuit breaker
        protection to stop execution if too many hosts are failing,
        and progress reporting.

        Args:
            inventory: Inventory of hosts to execute against
            context: Execution context with module and gate config

        Returns:
            ExecutionResults with per-host results and statistics

        Example:
            >>> inventory = load_inventory(Path("hosts.yaml"))
            >>> results = await executor.run(inventory, context)
            >>> for host, result in results.results.items():
            ...     print(f"{host}: {result.success}")
        """
        hosts = inventory.get_all_hosts()
        all_results: dict[str, ModuleResult] = {}
        retry_stats = RetryStats(total_hosts=len(hosts))
        start_time = time.time()

        # Report execution start
        module_name = context.execution_config.module_name
        self.progress_reporter.on_execution_start(len(hosts), module_name)

        # Process hosts in chunks for optimal performance
        for host_chunk in chunk(list(hosts.values()), self.chunk_size):
            chunk_results, chunk_states = await self._execute_chunk_with_retry(
                host_chunk, context
            )
            all_results.update(chunk_results)

            # Update retry stats
            for host_name, state in chunk_states.items():
                retry_stats.host_states[host_name] = state
                if state.succeeded:
                    if state.attempts == 1:
                        retry_stats.succeeded_first_try += 1
                    else:
                        retry_stats.succeeded_after_retry += 1
                else:
                    if state.gave_up:
                        retry_stats.failed_after_retries += 1
                    else:
                        retry_stats.failed_permanent += 1

            # Check circuit breaker after each chunk
            if check_circuit_breaker(
                len(all_results),
                sum(1 for r in all_results.values() if not r.success),
                self.circuit_breaker_config,
            ):
                retry_stats.circuit_breaker_triggered = True
                logger.warning(
                    f"Circuit breaker triggered: "
                    f"{self.circuit_breaker_config.threshold_percent}% failure threshold exceeded"
                )
                break

        results = ExecutionResults(results=all_results)
        if self.retry_config.max_attempts > 0:
            results.retry_stats = retry_stats

        # Report execution complete
        duration = time.time() - start_time
        self.progress_reporter.on_execution_complete(
            total=results.total_hosts,
            successful=results.successful,
            failed=results.failed,
            duration=duration,
        )

        return results

    async def _execute_chunk_with_retry(
        self,
        hosts: list[HostConfig],
        context: ExecutionContext,
    ) -> tuple[dict[str, ModuleResult], dict[str, RetryState]]:
        """Execute module on a chunk of hosts with retry support.

        Args:
            hosts: List of hosts to execute against
            context: Execution context

        Returns:
            Tuple of (results dict, retry states dict)
        """
        results: dict[str, ModuleResult] = {}
        states: dict[str, RetryState] = {}
        pending_hosts = list(hosts)
        max_attempts = self.retry_config.max_attempts + 1

        for attempt in range(1, max_attempts + 1):
            if not pending_hosts:
                break

            # Execute on pending hosts
            chunk_results = await self._execute_chunk(pending_hosts, context)

            # Process results
            hosts_to_retry = []
            for host in pending_hosts:
                result = chunk_results[host.name]

                # Initialize or update state
                if host.name not in states:
                    states[host.name] = RetryState(host_name=host.name)
                state = states[host.name]
                state.attempts = attempt

                if result.success:
                    state.succeeded = True
                    results[host.name] = result
                else:
                    # Determine error type
                    error_type = ErrorTypes.UNKNOWN
                    if result.error_context:
                        error_type = result.error_context.error_type
                    state.last_error_type = error_type
                    state.last_error_message = result.error or ""

                    # Check if we should retry
                    if attempt < max_attempts and self.retry_config.should_retry_error(error_type):
                        hosts_to_retry.append(host)
                        logger.info(
                            f"Will retry {host.name} (attempt {attempt}/{max_attempts}): "
                            f"{error_type}"
                        )
                    else:
                        # No more retries or permanent error
                        state.gave_up = attempt >= max_attempts
                        results[host.name] = result

            pending_hosts = hosts_to_retry

            # Wait before retry if there are hosts to retry
            if pending_hosts and attempt < max_attempts:
                delay = self.retry_config.get_delay(attempt)
                logger.info(f"Waiting {delay:.1f}s before retry attempt {attempt + 1}")

                # Report retry events for each host
                for host in pending_hosts:
                    state = states[host.name]
                    self.progress_reporter.on_host_retry(
                        host=host.name,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=state.last_error_message or "Unknown error",
                        delay=delay,
                    )

                await asyncio.sleep(delay)

        return results, states

    async def _execute_chunk(
        self,
        hosts: list[HostConfig],
        context: ExecutionContext,
    ) -> dict[str, ModuleResult]:
        """Execute module on a chunk of hosts concurrently.

        Args:
            hosts: List of hosts to execute against
            context: Execution context

        Returns:
            Dictionary mapping host names to results
        """
        tasks: list[tuple[str, float, asyncio.Task[ModuleResult]]] = []

        for host in hosts:
            # Report host start
            self.progress_reporter.on_host_start(host.name)

            # Get appropriate runner (local or remote)
            runner = self.runner_factory.create_runner(host)

            # Create task for this host with start time
            start_time = time.time()
            task = asyncio.create_task(runner.run(host, context))
            tasks.append((host.name, start_time, task))

        # Wait for all tasks to complete
        await asyncio.gather(*[task for _, _, task in tasks], return_exceptions=True)

        # Extract results and report completion
        # Runners follow the errors-as-data contract: run() always returns
        # a ModuleResult, never raises. We keep return_exceptions=True above
        # as a safety net for unexpected failures.
        results: dict[str, ModuleResult] = {}
        for host_name, start_time, task in tasks:
            duration = time.time() - start_time
            exc = task.exception()
            if exc is not None:
                # Safety net: should not happen since runners return errors as data,
                # but handle gracefully if an unexpected exception leaks through.
                logger.exception(f"Unexpected exception from runner for {host_name}: {exc}")
                result = ModuleResult.error_result(
                    host_name=host_name,
                    error=str(exc),
                    error_context=getattr(exc, 'context', None),
                )
            else:
                result = task.result()

            results[host_name] = result
            self.progress_reporter.on_host_complete(
                host=host_name,
                success=result.success,
                changed=result.changed,
                duration=duration,
                error=result.error,
            )

        return results

    async def cleanup(self) -> None:
        """Clean up all runner resources."""
        await self.runner_factory.cleanup_all()
