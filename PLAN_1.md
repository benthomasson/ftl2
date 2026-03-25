# Plan (Iteration 1)

Task: ## circuit_breaker division by zero when total_hosts=0

## Bug

`check_circuit_breaker` has an unguarded division by zero when `total_hosts=0`.

## Belief

`circuit-breaker-zero-division-unguarded`

## Resolution

Add a zero guard before the division.

---
*Filed from ftl2-expert spec anti-patterns*

Closes #25

IMPORTANT - EFFORT LEVEL: MINIMAL
Keep plan VERY brief (2-3 paragraphs max). Focus only on algorithm choice. Skip architectural discussions and detailed analysis.

Plan written. The fix is straightforward: add `if total_hosts == 0: return False` in `check_circuit_breaker` at `src/ftl2/retry.py:287` before the division, plus a test case confirming no exception is raised.

[Committed changes to planner branch]