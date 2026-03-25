# Implementation: Fix circuit_breaker division by zero (#25)

## IMPLEMENTATION

Added a zero guard `if total_hosts == 0: return False` at `src/ftl2/retry.py:287` before the division on line 290. This prevents `ZeroDivisionError` when `total_hosts=0`.

The guard is placed after the `min_hosts` check because when `min_hosts=0`, the `min_hosts` check passes through, and we still need to catch `total_hosts=0` before dividing.

**Changed file:** `src/ftl2/retry.py` — added 3 lines (guard clause).

## SELF-REVIEW

1. **What went well:** Straightforward one-liner fix, exactly as planned.
2. **What was unclear:** Nothing — the plan was precise with line numbers.
3. **What would help next time:** N/A, this was ideal.
4. **Concerns for reviewer:** None. The guard returns `False` (don't trip breaker) which is correct — zero hosts means nothing failed.
