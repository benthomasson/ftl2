# Tester (Iteration 1, Attempt 1)

## TEST CASES

9 tests in `tester/test_circuit_breaker_zero_div.py`, all passing:

1. **test_zero_hosts_zero_failed_no_exception** — Core bug: `check_circuit_breaker(0, 0, config)` must return `False` without raising `ZeroDivisionError`
2. **test_zero_hosts_with_failed_hosts** — Edge: `failed_hosts > 0` but `total_hosts = 0` (logically impossible but must not crash)
3. **test_zero_hosts_disabled** — Disabled breaker with zero hosts
4. **test_zero_hosts_min_hosts_zero** — Zero hosts with `min_hosts=0` config (ensures the zero guard fires before division even when min_hosts wouldn't catch it)
5. **test_normal_below_threshold** — Sanity: 20% failure with 30% threshold returns `False`
6. **test_normal_above_threshold** — Sanity: 40% failure with 30% threshold returns `True`
7. **test_one_host_zero_failures** — Single host, no failure
8. **test_one_host_one_failure** — Single host, 100% failure trips breaker
9. **test_exact_threshold** — Failure rate exactly at threshold triggers breaker

Run with: `python -m pytest tester/test_circuit_breaker_zero_div.py -v -o "addopts="`

## USAGE INSTRUCTIONS FOR USER

```python
from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig

config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=5)

# Returns False (safe) when total_hosts is 0 — no ZeroDivisionError
check_circuit_breaker(0, 0, config)   # False

# Normal usage
check_circuit_breaker(10, 2, config)  # False (20% < 30%)
check_circuit_breaker(10, 4, config)  # True  (40% >= 30%)
```

To run the tests: `cd workspaces/issue-25 && python -m pytest tester/test_circuit_breaker_zero_div.py -v -o "addopts="`

## SELF-REVIEW

1. Easy to test — pure function with simple inputs/outputs, no async or side effects.
2. Nothing missing; the implementation and plan were clear.
3. Having `pytest-cov` installed would avoid needing `-o "addopts="` override.
4. No gaps found. The zero guard is correctly placed before the division and all edge cases pass.

## Verdict
STATUS: TESTS_PASSED
OPEN_ISSUES: none

[Committed changes to tester branch]