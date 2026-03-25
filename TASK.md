# Task

## circuit_breaker division by zero when total_hosts=0

## Bug

`check_circuit_breaker` has an unguarded division by zero when `total_hosts=0`.

## Belief

`circuit-breaker-zero-division-unguarded`

## Resolution

Add a zero guard before the division.

---
*Filed from ftl2-expert spec anti-patterns*

Closes #25

Started: 2026-03-25T16:32:45.271578