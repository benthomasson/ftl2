# Tester Report: circuit_breaker division by zero (issue #25)

## TEST CASES

Test file: `tester/test_circuit_breaker_zero_div.py` — 9 tests, all passing.

| # | Test | What it validates |
|---|------|-------------------|
| 1 | `test_zero_hosts_zero_failed_min_hosts_zero` | Core bug: `(0, 0)` with `min_hosts=0` returns `False`, no `ZeroDivisionError` |
| 2 | `test_zero_hosts_zero_failed_default_min_hosts` | `(0, 0)` with default `min_hosts=5` returns `False` |
| 3 | `test_zero_hosts_nonzero_failed` | Degenerate `(0, 5)` returns `False` |
| 4 | `test_zero_hosts_disabled` | Disabled breaker with zero hosts returns `False` |
| 5 | `test_normal_below_threshold` | Sanity: `(10, 2)` at 20% below 30% threshold returns `False` |
| 6 | `test_normal_above_threshold` | Sanity: `(10, 4)` at 40% above threshold returns `True` |
| 7 | `test_exactly_at_threshold` | Edge: `(10, 3)` exactly at 30% triggers |
| 8 | `test_one_host_zero_failures` | Single host, no failures returns `False` |
| 9 | `test_one_host_one_failure` | Single host, 100% failure triggers |

Run command:
```bash
cd workspaces/issue-25 && python -m pytest tester/test_circuit_breaker_zero_div.py -v -o "addopts="
```

Result: **9 passed in 0.24s**

Note: The installed `ftl2` package resolves to `workspaces/issue-22` (a different workspace). The existing test in `tests/test_cli.py::test_circuit_breaker_zero_hosts` fails because it imports the unfixed version. My tests explicitly use `issue-25/src` and confirm the fix works correctly.

## USAGE INSTRUCTIONS FOR USER

```python
from ftl2.retry import check_circuit_breaker, CircuitBreakerConfig

config = CircuitBreakerConfig(enabled=True, threshold_percent=30.0, min_hosts=0)

# Safe with zero hosts — returns False instead of ZeroDivisionError
check_circuit_breaker(total_hosts=0, failed_hosts=0, config=config)  # False
```

Run tests: `python -m pytest tester/test_circuit_breaker_zero_div.py -v -o "addopts="`

## SELF-REVIEW

1. **Easy to test:** Pure function with simple inputs/outputs — straightforward.
2. **Missing info:** None needed.
3. **Next time:** Nothing — clean bug, clean fix.
4. **Gaps found:** The installed package points to a different workspace (`issue-22`), so `tests/test_cli.py` imports the wrong source. Not a bug in the fix itself — just a workspace isolation issue.

## Verdict
STATUS: TESTS_PASSED
OPEN_ISSUES: none
