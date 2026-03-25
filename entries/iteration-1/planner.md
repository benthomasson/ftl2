Plan written. The fix is straightforward: add `if total_hosts == 0: return False` in `check_circuit_breaker` at `src/ftl2/retry.py:287` before the division, plus a test case confirming no exception is raised.

[Committed changes to planner branch]