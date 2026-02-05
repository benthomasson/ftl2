"""Module execution orchestration for FTL2.

This module provides the core orchestration logic for running modules
across inventories of hosts with concurrent execution, chunking for
optimal performance, result aggregation, and retry logic.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from .exceptions import FTL2Error, ErrorTypes
from .inventory import Inventory
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
    retry logic, and proper cleanup of resources.

    Attributes:
        runner_factory: Factory for creating module runners
        chunk_size: Number of hosts to process concurrently
        retry_config: Configuration for retry behavior
        circuit_breaker_config: Configuration for circuit breaker

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
    ) -> None:
        """Initialize the executor.

        Args:
            chunk_size: Number of hosts to process concurrently (default: 10)
            retry_config: Configuration for retry behavior
            circuit_breaker_config: Configuration for circuit breaker
        """
        self.runner_factory = ModuleRunnerFactory()
        self.chunk_size = chunk_size
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker_config = circuit_breaker_config or CircuitBreakerConfig()

    async def run(
        self,
        inventory: Inventory,
        context: ExecutionContext,
    ) -> ExecutionResults:
        """Execute a module across all hosts in the inventory.

        Supports automatic retries for failed hosts and circuit breaker
        protection to stop execution if too many hosts are failing.

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
        tasks: list[tuple[str, asyncio.Task[ModuleResult]]] = []

        for host in hosts:
            # Get appropriate runner (local or remote)
            runner = self.runner_factory.create_runner(host)

            # Create task for this host
            task = asyncio.create_task(runner.run(host, context))
            tasks.append((host.name, task))

        # Wait for all tasks to complete
        await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)

        # Extract results
        results: dict[str, ModuleResult] = {}
        for host_name, task in tasks:
            try:
                result = task.result()
                results[host_name] = result
            except FTL2Error as e:
                # Capture rich error context from FTL2 exceptions
                logger.error(f"Execution failed on {host_name}: {e}")
                results[host_name] = ModuleResult(
                    host_name=host_name,
                    success=False,
                    changed=False,
                    output={},
                    error=str(e),
                    error_context=e.context,
                )
            except Exception as e:
                # Convert other exceptions to error result
                logger.exception(f"Execution failed on {host_name}: {e}")
                results[host_name] = ModuleResult(
                    host_name=host_name,
                    success=False,
                    changed=False,
                    output={},
                    error=str(e),
                )

        return results

    async def cleanup(self) -> None:
        """Clean up all runner resources."""
        await self.runner_factory.cleanup_all()
