Done. Added `if total_hosts == 0: return False` at `src/ftl2/retry.py:284`, before the `min_hosts` check and the division. This prevents the `ZeroDivisionError` when no hosts are present — returning `False` (don't trip the breaker) is the correct behavior since zero hosts means zero failures.

[Committed changes to implementer branch]