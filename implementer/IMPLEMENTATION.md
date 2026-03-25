# Implementation: Fix circuit_breaker division by zero (Issue #25)

## IMPLEMENTATION

Added `if total_hosts == 0: return False` guard at `src/ftl2/retry.py:284`, before the `min_hosts` check and the division on line 289. When there are zero hosts, the circuit breaker cannot meaningfully trip, so returning `False` is correct.

### Change

**File**: `src/ftl2/retry.py` (line 284)

```python
    if total_hosts == 0:
        return False
```

Inserted before the existing `min_hosts` check. This ensures `total_hosts=0` never reaches the division at line 289.

## SELF-REVIEW

1. Clean one-line fix, exactly what was needed.
2. Plan was clear — no friction.
3. Nothing needed.
4. No concerns. The guard is unconditional and placed before any arithmetic. If `min_hosts` defaults to something ≥ 1 it would also catch this, but the explicit guard is safer and more readable.
