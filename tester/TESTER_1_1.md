# Tester Report: circuit_breaker division by zero (Issue #25)

## TEST CASES

Test file: `tester/test_circuit_breaker_zero_division.py`

8 test cases covering the zero-division fix and surrounding behavior:

1. **test_zero_hosts_zero_failed_returns_false** — Primary bug case: `total_hosts=0, failed_hosts=0` returns `False` without raising
2. **test_zero_hosts_with_min_hosts_zero** — Critical edge case: `min_hosts=0` with `total_hosts=0` would previously pass the `min_hosts` check and hit `ZeroDivisionError`
3. **test_zero_hosts_nonzero_failed** — Degenerate case: `failed_hosts > total_hosts` with `total_hosts=0`
4. **test_one_host_no_failures** — Boundary: single host, no failures, below `min_hosts`
5. **test_one_host_one_failure_below_min_hosts** — Single host 100% failure, but below `min_hosts`
6. **test_normal_above_threshold_triggers** — Sanity: 40% >= 30% threshold triggers
7. **test_normal_below_threshold_no_trigger** — Sanity: 20% < 30% threshold doesn't trigger
8. **test_disabled_with_zero_hosts** — Disabled breaker with zero hosts returns `False`

Run command:
```bash
cd workspaces/issue-25
PYTHONPATH=src:$PYTHONPATH uv run pytest tester/test_circuit_breaker_zero_division.py -v -o "addopts="
```

Result: **8 passed in 0.13s**

Note: The parent venv has a stale `ftl2` install from `issue-22`. Setting `PYTHONPATH=src` ensures the correct (fixed) source is imported.

## USAGE INSTRUCTIONS FOR USER

```python
from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig

config = CircuitBreakerConfig(
    enabled=True,
    threshold_percent=30.0,
    min_hosts=5,
)

# Safe with zero hosts (previously raised ZeroDivisionError)
result = check_circuit_breaker(total_hosts=0, failed_hosts=0, config=config)
# Returns False — breaker does not trip

# Normal usage
result = check_circuit_breaker(total_hosts=10, failed_hosts=4, config=config)
# Returns True — 40% >= 30% threshold, breaker trips
```

## SELF-REVIEW

1. **Easy to test**: Pure function with clear inputs/outputs. No mocking needed.
2. **Missing info**: The parent venv has a stale install pointing to `issue-22`, which caused initial test failures until `PYTHONPATH` was set. This is an environment issue, not a code issue.
3. **Next time**: A clean venv per workspace would avoid cross-workspace import conflicts.
4. **Gaps found**: None. The fix is correct and complete.

## Verdict
STATUS: TESTS_PASSED
OPEN_ISSUES: none
