# Implementation: Fix circuit_breaker division by zero (Issue #25)

## IMPLEMENTATION

Added a zero guard `if total_hosts == 0: return False` at `src/ftl2/retry.py:284`, before the `min_hosts` check and the division on line 288. When there are zero hosts, the breaker cannot trip, so returning `False` is correct.

**Changed file**: `src/ftl2/retry.py` — one line added.

## SELF-REVIEW

1. Clean one-liner fix, exactly matches the plan.
2. Nothing unclear — the plan was precise.
3. Nothing needed — plan was well-scoped.
4. No concerns. The guard is placed before `min_hosts` so it catches `total_hosts=0` even when `config.min_hosts=0`.
