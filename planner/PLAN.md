# Plan: Fix circuit_breaker division by zero (Issue #25)

## PLAN

**Bug**: `check_circuit_breaker` in `src/ftl2/retry.py:287` divides `failed_hosts / total_hosts` without guarding against `total_hosts=0`. While there is a `min_hosts` check on line 284, if `config.min_hosts` is set to 0, the division on line 287 will raise `ZeroDivisionError`.

**Fix**: Add an early return `if total_hosts == 0: return False` before the division on line 287. This is the simplest correct fix — zero hosts means nothing has failed, so the breaker should not trip. Place it before the `min_hosts` check or right after it, before the division.

**Success criteria**: `check_circuit_breaker(total_hosts=0, failed_hosts=0, config)` returns `False` instead of raising. Add a test case for this in `tests/test_cli.py` (or wherever circuit breaker tests live).

## SELF-REVIEW

1. Straightforward bug with an obvious fix — high confidence.
2. No missing info needed.
3. N/A
4. Confidence: **HIGH** — single-line guard for a clear division-by-zero bug.
