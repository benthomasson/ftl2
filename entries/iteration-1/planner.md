Plan written. The fix is a one-liner: guard `total_hosts == 0` with an early `return False` before the division at `retry.py:287`. Zero hosts means no failures, so the circuit breaker should not trip.

[Committed changes to planner branch]