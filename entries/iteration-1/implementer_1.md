Done. Added `if total_hosts == 0: return False` at `src/ftl2/retry.py:284`, before the division on line 289. This prevents the `ZeroDivisionError` when `check_circuit_breaker` is called with zero hosts.

[Committed changes to implementer branch]